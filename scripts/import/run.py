"""One-off import of the Valio Aimo product data into wholesaler.* (owner: P1).

Run from the repo root after migrations 0001-0003:
    python scripts/import/run.py                 # full import: labels via LLM, embeddings, upsert
    python scripts/import/run.py --skip-llm      # no category labels from the LLM
    python scripts/import/run.py --skip-embed    # no embeddings (search falls back to full-text)
    python scripts/import/run.py --limit 200     # quick test on the first 200 products

LLM labels and embeddings are cached in data/.cache/, so a re-run only pays for what changed.
"""
import argparse
import base64
import csv
import glob
import hashlib
import json
import os
import re
import sys
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psycopg

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:      # inside docker compose the env comes from env_file
    pass

from labels import ALLERGENS, CLAIM_WORDS, SALES_UNITS

ROOT = Path(__file__).resolve().parents[2]
DATA, CACHE = ROOT / "data", ROOT / "data" / ".cache"
OR_URL = "https://openrouter.ai/api/v1"
VOL = {"LTR": 1.0, "CLT": 0.01, "MLT": 0.001}
WGT = {"KGM": 1.0, "GRM": 0.001}
SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|l|dl|cl|ml)\b", re.I)
SIZE_FACTOR = {"kg": ("w", 1), "g": ("w", 0.001), "l": ("v", 1), "dl": ("v", 0.1), "cl": ("v", 0.01), "ml": ("v", 0.001)}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- flatten one product
def by_lang(items, lang: str) -> str | None:
    for i in items or []:
        if i.get("language") == lang and i.get("value"):
            return i["value"].strip()
    return None


def class_ids(s: dict, name: str, units: tuple[str, ...] | None = None) -> list[str]:
    out = []
    for c in s.get("classifications") or []:
        if c.get("name") == name:
            for v in c.get("values") or []:
                if isinstance(v, dict) and v.get("id") and (units is None or v.get("unit") in units):
                    out.append(v["id"])
    return sorted(set(out))


def unit_size(p: dict, s: dict, name: str) -> tuple[float | None, float | None, bool, str | None]:
    """Volume (l) and weight (kg) of ONE sales unit, whether it was parsed from the name, and a display label."""
    if p["salesUnit"] == "KG":
        return None, 1.0, False, "1 kg"
    vol = wgt = None
    for u in s.get("unitConversions") or []:
        if u.get("synkkaPackagingType") != "BASE_UNIT_OR_EACH":
            continue
        v, w = u.get("volume") or {}, u.get("netWeight") or {}
        if v.get("unit") in VOL and v.get("value"):
            vol = v["value"] * VOL[v["unit"]]
        if w.get("unit") in WGT and w.get("value"):
            wgt = w["value"] * WGT[w["unit"]]
    name_v = name_w = None                      # first volume and first weight written in the name
    for m in SIZE_RE.finditer(name or ""):
        kind, f = SIZE_FACTOR[m.group(2).lower()]
        val = float(m.group(1).replace(",", ".")) * f
        if kind == "v" and name_v is None:
            name_v = val
        if kind == "w" and name_w is None:
            name_w = val
    # Source data sometimes stores grams/millilitres under KGM/LTR (1000x too big): fix against the name.
    if vol and ((name_v and vol / name_v > 50) or (not name_v and wgt and vol > 50 * wgt)):
        vol /= 1000
    if wgt and ((name_w and wgt / name_w > 50) or (not name_w and name_v and wgt > 50 * name_v)
                or (not name_w and vol and wgt > 50 * vol)):
        wgt /= 1000
    derived = False
    if vol is None and wgt is None and (name_v or name_w):
        vol, wgt, derived = (name_v, None, True) if name_v else (None, name_w, True)
    mult = next((u["sizeInBaseUnits"] for u in p.get("units") or [] if u.get("unitId") == p["salesUnit"]), 1.0) or 1.0
    vol = round(vol * mult, 4) if vol is not None else None
    wgt = round(wgt * mult, 4) if wgt is not None else None
    base = f"{vol / mult:g} l" if vol else (f"{wgt / mult:g} kg" if wgt else None)
    label = (f"{mult:g} x {base}" if mult != 1 else base) if base else None
    return vol, wgt, derived, label


def flatten(p: dict, reliability: dict, co2: dict) -> dict:
    s = p.get("synkkaData") or {}
    names = s.get("names") or []
    name_en, name_fi, name_sv = by_lang(names, "en"), by_lang(names, "fi"), by_lang(names, "sv")
    vol, wgt, derived, label = unit_size(p, s, name_en or name_fi or "")
    pid = int(p["id"])
    return {
        "id": pid, "vendor_name": p.get("vendorName"), "category_code": p.get("category"),
        "sales_unit": p["salesUnit"], "base_unit": p.get("baseUnit"), "allowed_lot_size": p.get("allowedLotSize"),
        "deleted": bool(p.get("deleted")), "name_en": name_en, "name_fi": name_fi, "name_sv": name_sv,
        "brand": s.get("brand"), "gtin": s.get("gtin"), "sales_unit_gtin": p.get("salesUnitGtin"),
        "country_of_origin": p.get("countryOfOrigin"),
        "temperature_condition": str(p["temperatureCondition"]) if p.get("temperatureCondition") is not None else None,
        "claims": class_ids(s, "nutritionalClaim"),
        "allergens_contains": class_ids(s, "allergen", ("CONTAINS", "MAY_CONTAIN")),
        "labels": class_ids(s, "packagingMarkedLabel"),
        "ingredients": (by_lang(s.get("keyIngredients"), "en") or by_lang(s.get("keyIngredients"), "fi") or "")[:2000] or None,
        "unit_volume_l": vol, "unit_weight_kg": wgt, "size_derived": derived, "unit_label": label,
        "delivery_reliability": reliability.get(pid), "co2_kg_per_kg": co2.get(pid),
    }


def read_csv(pattern: str, col: str) -> dict[int, float]:
    path = next(iter(glob.glob(str(DATA / pattern))), None)
    if not path:
        sys.exit(f"Missing {pattern} in {DATA}")
    with open(path, newline="", encoding="utf-8") as f:
        return {int(r["productId"]): float(r[col]) for r in csv.DictReader(f) if r.get(col) not in (None, "")}


# ---------------------------------------------------------------- OpenRouter helpers
def or_post(client: httpx.Client, path: str, body: dict, tries: int = 4) -> dict:
    for attempt in range(tries):
        r = client.post(f"{OR_URL}/{path}", json=body)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (408, 429, 500, 502, 503, 504) and attempt < tries - 1:
            time.sleep(2 ** attempt)
            continue
        raise RuntimeError(f"OpenRouter {path} HTTP {r.status_code}: {r.text[:200]}")
    raise RuntimeError("unreachable")


def category_labels(client: httpx.Client, rows: list[dict]) -> dict[str, str]:
    cache_file = CACHE / "category_labels.json"
    cache = json.loads(cache_file.read_text("utf-8")) if cache_file.exists() else {}
    samples: dict[str, list[str]] = {}
    for r in rows:
        if r["category_code"] and r["name_en"] and len(samples.setdefault(r["category_code"], [])) < 15:
            samples[r["category_code"]].append(r["name_en"])
    todo = [c for c in samples if c not in cache]
    log(f"category labels: {len(samples)} codes, {len(todo)} to ask the LLM")
    schema = {"type": "object", "additionalProperties": False, "required": ["labels"], "properties": {"labels": {
        "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["code", "label"],
                                   "properties": {"code": {"type": "string"}, "label": {"type": "string"}}}}}}
    models = [m for m in (os.environ.get("LLM_MODEL"), os.environ.get("LLM_MODEL_FALLBACK")) if m]
    step, skipped = 15, []
    for i in range(0, len(todo), step):
        batch = {c: samples[c] for c in todo[i:i + step]}
        prompt = ("You name food-wholesale product categories. For each category code, return a short English "
                  "category label (2-4 words, e.g. 'Lactose-free milk', 'Frozen fish fillets') that covers ALL its "
                  "sample products. Return every code exactly once. Answer with the JSON only.\n\n"
                  + json.dumps(batch, ensure_ascii=False))
        got = None
        for model in models:            # try the cheap model, then the fallback, one at a time
            try:
                out = or_post(client, "chat/completions", {
                    "model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 8000,
                    "reasoning": {"effort": "low", "exclude": True},
                    "response_format": {"type": "json_schema", "json_schema": {"name": "labels", "strict": True, "schema": schema}},
                    "provider": {"sort": "price", "require_parameters": True}})
                choice = out["choices"][0]
                got = json.loads(choice["message"]["content"])["labels"]
                break
            except (json.JSONDecodeError, KeyError, TypeError, RuntimeError) as e:
                reason = out["choices"][0].get("finish_reason") if "out" in locals() and out.get("choices") else type(e).__name__
                log(f"  batch {i // step + 1}: {model} failed ({reason}), trying next model")
        if got is None:
            skipped += list(batch)
            continue
        cache.update({g["code"]: g["label"].strip() for g in got if g["code"] in batch and g["label"].strip()})
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), "utf-8")
        log(f"  labelled {min(i + step, len(todo))}/{len(todo)}")
    if skipped:
        log(f"  {len(skipped)} codes left without a label (search still works; re-run to retry): {skipped[:10]}")
    return cache


def embed_all(client: httpx.Client, rows: list[dict], model: str) -> None:
    cache_file = CACHE / f"embeddings_{model.replace('/', '_')}.f32b64"
    cache: dict[str, list[float]] = {}
    if cache_file.exists():
        for line in cache_file.read_text("utf-8").splitlines():
            k, v = line.split(" ", 1)
            cache[k] = array("f", base64.b64decode(v)).tolist()
    key = lambda r: hashlib.sha1(r["search_text"].encode()).hexdigest()
    todo = [r for r in rows if key(r) not in cache]
    log(f"embeddings: {len(rows)} rows, {len(todo)} to embed with {model}")

    def run(batch: list[dict]) -> list[tuple[str, list[float]]]:
        out = or_post(client, "embeddings", {"model": model, "input": [r["search_text"] for r in batch]})
        vecs = [d["embedding"] for d in sorted(out["data"], key=lambda d: d["index"])]
        return [(key(r), v) for r, v in zip(batch, vecs)]

    batches = [todo[i:i + 100] for i in range(0, len(todo), 100)]
    done = 0
    with ThreadPoolExecutor(4) as pool, open(cache_file, "a", encoding="utf-8") as f:
        for result in pool.map(run, batches):
            for k, v in result:
                cache[k] = v
                f.write(f"{k} {base64.b64encode(array('f', v).tobytes()).decode()}\n")
            done += len(result)
            if done % 2000 < 100 or done == len(todo):
                log(f"  embedded {done}/{len(todo)}")
    for r in rows:
        v = cache.get(key(r))
        r["embedding"] = "[" + ",".join(f"{x:.6f}" for x in v) + "]" if v else None


# ---------------------------------------------------------------- database
COLS = ["id", "vendor_name", "category_code", "sales_unit", "base_unit", "allowed_lot_size", "deleted", "name_en",
        "name_fi", "name_sv", "brand", "gtin", "sales_unit_gtin", "country_of_origin", "temperature_condition",
        "claims", "allergens_contains", "labels", "ingredients", "unit_volume_l", "unit_weight_kg", "size_derived",
        "unit_label", "delivery_reliability", "co2_kg_per_kg", "search_text", "embedding"]


def write_db(rows: list[dict], subs: list[tuple[int, int, int]], labels: dict[str, str], cat_labels: dict[str, str]) -> None:
    placeholders = ", ".join(f"%({c})s::extensions.vector" if c == "embedding" else f"%({c})s" for c in COLS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in COLS if c != "id")
    with psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None) as conn:
        cur = conn.cursor()
        log(f"upserting {len(rows)} products")
        for i in range(0, len(rows), 1000):
            cur.executemany(f"insert into wholesaler.products ({', '.join(COLS)}) values ({placeholders}) "
                            f"on conflict (id) do update set {updates}", rows[i:i + 1000])
            conn.commit()
            log(f"  {min(i + 1000, len(rows))}/{len(rows)}")
        ids = {r["id"] for r in rows}
        subs = [s for s in subs if s[0] in ids and s[1] in ids]
        cur.executemany("insert into wholesaler.product_substitutions (product_id, substitute_id, rank) values (%s, %s, %s) "
                        "on conflict do nothing", subs)
        code_rows = ([("category", c, l, True) for c, l in cat_labels.items()]
                     + [("claim", c, CLAIM_WORDS.get(c, c.replace("_", " ").capitalize()), c not in CLAIM_WORDS) for c in labels["claim"]]
                     + [("allergen", c, ALLERGENS.get(c), True) for c in labels["allergen"]]
                     + [("sales_unit", c, SALES_UNITS.get(c), True) for c in labels["sales_unit"]]
                     + [("temperature", c, None, False) for c in labels["temperature"]])
        cur.executemany("insert into wholesaler.code_labels (kind, code, label, derived) values (%s, %s, %s, %s) "
                        "on conflict (kind, code) do update set label = excluded.label, derived = excluded.derived", code_rows)
        conn.commit()
        n, n_emb = cur.execute("select count(*), count(embedding) from wholesaler.products").fetchone()
        size = cur.execute("select pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        log(f"done: {n} products, {n_emb} with embeddings, {len(subs)} substitution pairs, "
            f"{len(code_rows)} code labels, database size {size}")


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--skip-embed", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)

    path = next(iter(sorted(glob.glob(str(DATA / "valio_aimo_junction26_product_data*.json")))), None)
    if not path:
        sys.exit(f"Missing valio_aimo_junction26_product_data*.json in {DATA}")
    log(f"reading {Path(path).name}")
    products = json.loads(Path(path).read_text("utf-8"))[: args.limit]
    reliability = read_csv("*delivery_reliability*.csv", "deliveryReliability")
    co2 = read_csv("*carbon_footprint*.csv", "AVG kg CO2-eq/kg")
    rows = [flatten(p, reliability, co2) for p in products]
    subs = [(int(s["product"]), int(s["substituteProduct"]), i + 1)
            for p in products for i, s in enumerate(p.get("substitutions") or [])]
    log(f"{len(rows)} products, {sum(r['unit_volume_l'] is not None for r in rows)} with volume, "
        f"{sum(r['unit_weight_kg'] is not None for r in rows)} with weight, {sum(r['size_derived'] for r in rows)} sizes from names")

    client = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}"})
    cat_labels = {} if args.skip_llm else category_labels(client, rows)
    for r in rows:
        cat = cat_labels.get(r["category_code"] or "")
        r["search_text"] = " | ".join(filter(None, [r["name_en"], r["name_fi"], r["name_sv"], r["brand"], cat]))
        r["embedding"] = None
    if not args.skip_embed:
        embed_all(client, rows, os.environ.get("EMBED_MODEL", "baai/bge-m3"))

    labels = {"claim": sorted({c for r in rows for c in r["claims"]}),
              "allergen": sorted({c for r in rows for c in r["allergens_contains"]}),
              "sales_unit": sorted({r["sales_unit"] for r in rows}),
              "temperature": sorted({r["temperature_condition"] for r in rows if r["temperature_condition"]})}
    write_db(rows, subs, labels, cat_labels)


if __name__ == "__main__":
    main()
