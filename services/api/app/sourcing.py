"""Deterministic sourcing: needs -> catalog candidates -> baskets.

No LLM and no policy decisions here: the gateway re-prices and judges whatever is sent to it.
"""

import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from tta_contracts import MandateLimits, Offer, ProductHit

from .brain import Dish, Item

FRACTIONAL_UNITS = {"KG", "KGM", "L", "LTR"}  # sales units that can be ordered in fractions
STRATEGIES = (("best_match", "Best match"), ("cheapest", "Cheapest"), ("most_reliable", "Most reliable"))
CENT = Decimal("0.01")


@dataclass
class Need:
    key: str
    name: str
    search_en: str
    search_fi: str | None
    amount: Decimal  # in unit
    unit: str  # kg | l | pcs
    claims: tuple[str, ...] = ()
    essential: bool = True
    dishes: tuple[str, ...] = ()
    alt_of: str | None = None  # key of the need this one can replace (recipe-level substitute)
    note: str = ""

    @property
    def ref(self) -> str:
        return f"{norm(self.name)}|{self.unit}"


@dataclass
class Candidate:
    need: Need
    hit: ProductHit
    offer: Offer
    rank: int
    size: Decimal
    step: Decimal
    units: Decimal
    cost: Decimal
    available: Decimal
    substitute_for: int | None = None  # original product when this is a Valio substitute pair

    @property
    def enough(self) -> bool:
        return self.available >= self.units


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def qty(amount, unit: str = "") -> str:
    text = f"{Decimal(str(amount)).quantize(Decimal('0.01')):f}".rstrip("0").rstrip(".")
    return f"{text} {unit}".strip()


def needs_from_items(items: list[Item]) -> list[Need]:
    return [
        Need(
            key=f"i{index}",
            name=item.name,
            search_en=item.search_en,
            search_fi=item.search_fi,
            amount=Decimal(str(item.quantity)),
            unit=item.unit,
            claims=tuple(item.claims),
        )
        for index, item in enumerate(items)
        if item.quantity and item.unit
    ]


def needs_from_dishes(dishes: list[Dish], headcount: int, dairy_claims: tuple[str, ...] = ()) -> list[Need]:
    """Scale per-person amounts, merge ingredients shared by several dishes, attach recipe alternatives."""
    merged: dict[str, Need] = {}
    alternatives: dict[str, Need] = {}
    for dish in dishes:
        for ingredient in dish.ingredients:
            unit = "l" if ingredient.unit == "ml" else "kg"
            amount = (Decimal(str(ingredient.amount)) * headcount / 1000).quantize(Decimal("0.001"))
            ref = f"{norm(ingredient.name)}|{unit}"
            need = merged.get(ref)
            if need is None:
                need = merged[ref] = Need(
                    key=f"n{len(merged)}",
                    name=ingredient.name,
                    search_en=ingredient.search_en,
                    search_fi=ingredient.search_fi,
                    amount=Decimal(0),
                    unit=unit,
                    claims=dairy_claims if ingredient.dairy else (),
                    essential=False,
                )
            need.amount += amount
            need.essential = need.essential or ingredient.essential
            if dish.name not in need.dishes:
                need.dishes += (dish.name,)
            alt = ingredient.alt
            if alt and ingredient.essential and need.key not in alternatives and norm(alt.name) != norm(need.name):
                alternatives[need.key] = Need(
                    key=need.key + "a",
                    name=alt.name,
                    search_en=alt.search_en,
                    search_fi=alt.search_fi,
                    amount=Decimal(0),
                    unit=unit,
                    claims=need.claims,
                    alt_of=need.key,
                    note=alt.note,
                )
    by_key = {need.key: need for need in merged.values()}
    for key, alt in alternatives.items():
        alt.amount = by_key[key].amount
    return list(merged.values()) + list(alternatives.values())


def criteria(need: Need, avoid: list[str]) -> dict:
    return {
        "query_text": (need.search_en or need.name)[:200],
        "query_text_alt": need.search_fi[:200] if need.search_fi else None,
        "must": {"claims_all": list(need.claims), "allergens_none": avoid},
        "limit": 5,
    }


def size_of(hit: ProductHit, unit: str) -> Decimal | None:
    """Size of ONE sales unit in the need's unit. Weight and volume stand in for each other (1 l ~ 1 kg)."""
    if unit == "pcs":
        return Decimal(1)
    first, second = (hit.unit_weight_kg, hit.unit_volume_l) if unit == "kg" else (hit.unit_volume_l, hit.unit_weight_kg)
    value = first if first and first > 0 else second
    return Decimal(str(value)) if value and value > 0 else None


GENERIC = {
    "fresh", "frozen", "whole", "sliced", "diced", "cooked", "smoked", "boneless", "organic", "large", "small",
    "cut", "cubes", "cube", "pieces", "raw", "dried", "ground", "minced", "peeled", "red", "green", "white",
    "black", "yellow", "with", "and", "for", "the", "plain", "natural", "finnish", "skinless", "chopped",
}


def core(text: str | None) -> list[str]:
    return [w for w in re.findall(r"[a-zäöå]{3,}", norm(text or "")) if w not in GENERIC]


def relevant(hit: ProductHit, need: Need) -> bool:
    """The product must name the ingredient: its head noun plus most of the other words (English), or
    the Finnish word. Keeps "Werther's cream candies" out of sour cream and air freshener out of rutabaga."""
    en = norm(" ".join(filter(None, [hit.name_en, hit.category_name])))
    fi = norm(hit.name_fi or "")
    words = core(need.search_en) or core(need.name)
    if words:
        hits = [w for w in words if w[:5] in en]
        if words[-1][:5] in en and len(hits) >= (len(words) + 2) // 2:
            return True
    finnish = core(need.search_fi)
    return bool(finnish) and all(w[:5] in fi for w in finnish[-2:])


def pick(needs: list[Need], hits: dict[str, list[ProductHit]], cap: int = 50):
    """Candidates per need and the product ids to price, within the gateway's 50 ids per quote."""
    primaries = [need for need in needs if not need.alt_of]
    alternatives = [need for need in needs if need.alt_of]
    per_need = max(1, min(5, (cap - len(alternatives)) // max(1, len(primaries))))
    chosen: dict[str, list[ProductHit]] = {}
    ids: list[int] = []
    for need in primaries + alternatives:
        usable = [hit for hit in hits.get(need.ref, []) if size_of(hit, need.unit)]
        good = [hit for hit in usable if relevant(hit, need)]
        chosen[need.ref] = good[: 1 if need.alt_of else per_need]
        ids += [hit.product_id for hit in chosen[need.ref] if hit.product_id not in ids]
    return chosen, ids[:cap]


def candidates(need: Need, hits: list[ProductHit], offers: dict[int, Offer], origin=None) -> list[Candidate]:
    """Priced, sized options for a need. `origin` maps a Valio substitute's id to the product it replaces."""
    result = []
    for rank, hit in enumerate(hits):
        offer = offers.get(hit.product_id)
        size = size_of(hit, need.unit)
        if not offer or offer.error or offer.sales_block == "blocked" or offer.unit_price_eur is None or size is None:
            continue
        lot = Decimal(str(hit.allowed_lot_size)) if hit.allowed_lot_size and hit.allowed_lot_size > 0 else Decimal(1)
        # Cartons and packs are whole units even when the catalog allows 0.01 lots.
        step = lot if (hit.sales_unit or "").upper() in FRACTIONAL_UNITS or lot >= 1 else Decimal(1)
        units = max((need.amount / size / step).to_integral_value(rounding=ROUND_CEILING) * step, step)
        result.append(
            Candidate(
                need=need,
                hit=hit,
                offer=offer,
                rank=rank,
                size=size,
                step=step,
                units=units,
                cost=(offer.unit_price_eur * units).quantize(CENT),
                available=Decimal(str(offer.available_on_date or 0)),
                substitute_for=(origin or {}).get(hit.product_id),
            )
        )
    return result


def order_key(strategy: str):
    if strategy == "cheapest":
        return lambda c: (c.offer.unit_price_eur / c.size, c.rank)
    if strategy == "most_reliable":
        return lambda c: (-(c.hit.delivery_reliability or -1), c.rank)
    return lambda c: c.rank


def split(options: list[Candidate], strategy: str, need: Need) -> list[Candidate] | None:
    """Cover the amount with several products of the same ingredient when no single one has enough."""
    remaining, picks = need.amount, []
    for option in sorted(options, key=order_key(strategy)):
        if remaining <= 0:
            break
        stock = (option.available / option.step).to_integral_value(rounding=ROUND_FLOOR) * option.step
        if stock <= 0:
            continue
        wanted = max((remaining / option.size / option.step).to_integral_value(rounding=ROUND_CEILING) * option.step, option.step)
        units = min(wanted, stock)
        picks.append(replace(option, units=units, cost=(option.offer.unit_price_eur * units).quantize(CENT)))
        remaining -= units * option.size
    return picks if remaining <= 0 and len(picks) > 1 else None


def choose(options: list[Candidate], strategy: str) -> Candidate | None:
    enough = [option for option in options if option.enough]
    if not enough:
        return None
    if strategy == "cheapest":
        return min(enough, key=lambda c: (c.cost, c.rank))
    if strategy == "most_reliable":
        return max(enough, key=lambda c: (c.hit.delivery_reliability or -1, -c.rank))
    return min(enough, key=lambda c: c.rank)


def earliest(options: list[Candidate], day: date) -> date | None:
    days = [
        line.available_date
        for option in options
        for line in option.offer.schedule
        if line.available_date > day and not line.sales_block and Decimal(str(line.available_amount)) >= option.units
    ]
    return min(days) if days else None


def resolve(need: Need, alt: Need | None, pool: dict[str, list[Candidate]], strategy: str, day: date):
    """Basket lines for one need, in this order: one product; several products of the same ingredient;
    a Valio substitute pair (the gateway holds it for approval); the recipe's own alternative; a partial
    fill. Returns (lines, note, problem): problem explains a need that cannot be sourced at all."""
    options = pool.get(need.key, [])
    found = choose(options, strategy)
    if found:
        return [found], "", None
    if picks := split(options, strategy, need):
        return picks, f"Split across {len(picks)} products to cover {qty(need.amount, need.unit)}", None
    if found := choose(pool.get(need.key + "/valio", []), strategy):
        return [found], f"Valio Aimo substitute for {need.name}: needs your approval", None
    if alt and (found := choose(pool.get(alt.key, []), strategy)):
        note = f"Instead of {need.name}: {alt.name}" + (f" ({alt.note})" if alt.note else "")
        return [found], note, None
    partial = max((o for o in options if o.available >= o.step), key=lambda o: o.available, default=None)
    if partial:
        units = (partial.available / partial.step).to_integral_value(rounding=ROUND_FLOOR) * partial.step
        short = replace(partial, units=units, cost=(partial.offer.unit_price_eur * units).quantize(CENT))
        return [short], f"Only {qty(units)} of {qty(partial.units)} units in stock that day", None
    when = earliest(options, day)
    if not options:
        reason = "no matching product in the catalog"
    elif all(option.offer.sales_block == "cutoff" for option in options):
        reason = "the order cut-off for that date has passed"
    else:
        reason = "not in stock on that date"
    return [], "", {"need": need.name, "essential": need.essential, "reason": reason,
                    "earliest": when.isoformat() if when else None}


def make_pool(needs: list[Need], chosen: dict, offers: dict[int, Offer], valio: dict | None = None) -> dict:
    """Candidates per need key; Valio substitute pairs under '<key>/valio'."""
    pool = {need.key: candidates(need, chosen.get(need.ref, []), offers) for need in needs}
    for need in needs:
        pairs = (valio or {}).get(need.ref, [])
        if pairs:
            pool[need.key + "/valio"] = candidates(
                need, [hit for _, hit in pairs], offers, {hit.product_id: original for original, hit in pairs}
            )
    return pool


def short_needs(needs: list[Need], chosen: dict, offers: dict[int, Offer]) -> list[tuple[Need, int]]:
    """Needs no single product covers on the day, with the product a Valio substitute should replace."""
    result = []
    for need in needs:
        if need.alt_of or not chosen.get(need.ref):
            continue
        options = candidates(need, chosen[need.ref], offers)
        if not any(option.enough for option in options):
            result.append((need, chosen[need.ref][0].product_id))
    return result


def gate(total: Decimal, limits: MandateLimits | None) -> tuple[str, str]:
    if limits is None:
        return "unknown", "The gateway decides when you order"
    if total > limits.order_limit_eur:
        return "blocked", f"Above your {limits.order_limit_eur:.0f} EUR per-order limit: the gateway will block it"
    if total > limits.auto_limit_eur:
        return "approval", f"Above your {limits.auto_limit_eur:.0f} EUR auto limit: it will wait in Approvals"
    return "auto", "Within your auto limit: placed immediately"


def footprint(lines: list[dict]) -> tuple[float | None, float | None]:
    """(kg CO2e for the basket, cost-weighted delivery reliability), from what the catalog knows."""
    co2 = [line["co2_kg"] for line in lines if line["co2_kg"] is not None]
    weighted = [(float(line["line_total"]), line["reliability"]) for line in lines if line["reliability"] is not None]
    spend = sum(cost for cost, _ in weighted)
    reliability = sum(cost * value for cost, value in weighted) / spend if spend else None
    return (round(sum(co2), 1) if co2 else None, round(reliability, 3) if reliability is not None else None)


def line_of(found: Candidate, need: Need, note: str) -> dict:
    hit = found.hit
    weight = found.units * found.size if need.unit in ("kg", "l") else None
    return {
        "product_id": hit.product_id,
        "name": hit.name_en or hit.name_fi or f"#{hit.product_id}",
        "units": float(found.units),
        "sales_unit": hit.sales_unit,
        "pack": qty(found.size, "pcs" if need.unit == "pcs" else need.unit),
        "unit_price": str(found.offer.unit_price_eur),
        "line_total": str(found.cost),
        "need": need.name,
        "amount": qty(need.amount, need.unit),
        "note": note,
        "substitute_for": found.substitute_for,
        "reliability": hit.delivery_reliability,
        "co2_kg": round(float(weight) * hit.co2_kg_per_kg, 1) if weight and hit.co2_kg_per_kg is not None else None,
        "origin": hit.country_of_origin,
        "dishes": list(need.dishes),
    }


def build_baskets(needs: list[Need], chosen: dict, offers: dict[int, Offer], day: date, limits=None, valio=None) -> list[dict]:
    primaries = [need for need in needs if not need.alt_of]
    alternative = {need.alt_of: need for need in needs if need.alt_of}
    pool = make_pool(needs, chosen, offers, valio)
    baskets, seen = [], set()
    for label, title in STRATEGIES:
        lines, missing, total = [], [], Decimal(0)
        for need in primaries:
            picks, note, problem = resolve(need, alternative.get(need.key), pool, label, day)
            if problem:
                missing.append(problem)
                continue
            for found in picks:
                total += found.cost
                lines.append(line_of(found, need, note))
        signature = tuple((line["product_id"], line["units"]) for line in lines)
        if not lines or signature in seen:
            continue
        seen.add(signature)
        level, gate_note = gate(total, limits)
        if any(line["substitute_for"] for line in lines) and level == "auto":
            level, gate_note = "approval", "Contains a Valio substitute: it will wait in Approvals"
        co2, reliability = footprint(lines)
        baskets.append(
            {
                "label": label,
                "title": title,
                "total": str(total.quantize(CENT)),
                "lines": lines,
                "missing": missing,
                "gate": level,
                "gate_note": gate_note,
                "co2_kg": co2,
                "reliability": reliability,
                "explanation": f"{title}: {len(lines)} lines, {total.quantize(CENT)} EUR"
                + (f", {len(missing)} not available" if missing else ""),
            }
        )
    return baskets


def autonomous(basket: dict) -> bool:
    """The agent may order this basket without asking: within the auto limit, complete, no substitutes."""
    return (
        basket["gate"] == "auto"
        and not basket["missing"]
        and all(not line["note"] or line["note"].startswith("Split") for line in basket["lines"])
    )


def dish_cards(dishes: list[Dish], headcount: int, chosen: dict, offers: dict, day: date, dairy_claims=()) -> list[dict]:
    """What each suggested dish would need from Valio's stock on the delivery date."""
    cards = []
    for dish in dishes:
        needs = needs_from_dishes([dish], headcount, dairy_claims)
        primaries = [need for need in needs if not need.alt_of]
        alternative = {need.alt_of: need for need in needs if need.alt_of}
        pool = make_pool(needs, chosen, offers)
        covered, cost, absent, swaps = 0, Decimal(0), [], []
        for need in primaries:
            picks, note, problem = resolve(need, alternative.get(need.key), pool, "best_match", day)
            if problem or note.startswith("Only"):
                absent.append(need.name)
            else:
                covered += 1
                if note and not note.startswith("Split"):
                    swaps.append(note)
            cost += sum((found.cost for found in picks), Decimal(0))
        per_person = (cost / headcount).quantize(CENT) if headcount and cost else None
        stock = (
            f"all {len(primaries)} ingredients in stock"
            if not absent
            else f"{covered} of {len(primaries)} in stock (missing: {', '.join(absent)})"
        )
        cards.append(
            {
                "name": dish.name,
                "why": dish.why,
                "covered": covered,
                "total": len(primaries),
                "missing": absent,
                "swaps": swaps,
                "per_person": str(per_person) if per_person is not None else None,
                "ingredients": [f"{qty(i.amount, i.unit)} {i.name}" for i in dish.ingredients],
                "pantry": dish.pantry,
                "line": " · ".join(filter(None, [dish.why, stock, f"about {per_person} EUR per person" if per_person else ""])),
            }
        )
    return sorted(cards, key=lambda card: (-(card["covered"] / max(1, card["total"])), card["name"]))


def missing_report(needs: list[Need], chosen: dict, offers: dict[int, Offer], day: date, valio=None) -> list[dict]:
    """Why each need that cannot be sourced is missing (used when no basket could be built)."""
    pool = make_pool(needs, chosen, offers, valio)
    alternative = {need.alt_of: need for need in needs if need.alt_of}
    report = []
    for need in needs:
        if need.alt_of:
            continue
        problem = resolve(need, alternative.get(need.key), pool, "best_match", day)[2]
        if problem:
            report.append(problem)
    return report
