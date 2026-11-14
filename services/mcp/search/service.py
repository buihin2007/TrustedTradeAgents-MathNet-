"""search_products and get_facets for the gateway (owner: P1). P2 calls these from the MCP tools.

    engine = SearchEngine()
    facets = await engine.facets(conn)
    result, trace = await engine.search(conn, criteria)   # trace.sql etc. go to gateway_events

`conn` is a psycopg AsyncConnection logged in as postgres (pass prepare_threshold=None if you use
the transaction pooler). Generated SQL runs inside a read-only transaction as role search_ro with a
2 s timeout; every returned row is re-checked against criteria.must in Python.
"""
import json
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row

from tta_contracts import CodeLabel, Criteria, Facets, ProductHit, Relaxation, SearchResult
from tta_contracts.limits import SQL_TIMEOUT_S

from . import guard, template
from .openrouter import OpenRouter

try:
    from opentelemetry import trace as _otel
    _tracer = _otel.get_tracer("tta.search")
    span = _tracer.start_as_current_span
except ImportError:                       # tracing optional
    def span(name):
        return nullcontext()

K = 300                                   # candidates per branch of hybrid_rank
FACETS_TTL_S = 600
PARAM_DOCS = {
    "q_text": "text: search words (English and Finnish)",
    "q_emb": "text: query embedding literal, pass straight to hybrid_rank",
    "k": "int: candidates per ranking branch, pass straight to hybrid_rank",
    "limit": "int: max rows to return",
    "claims_all": "text[]: product must have ALL these claim codes",
    "allergens_none": "text[]: product must contain NONE of these allergen codes",
    "category_codes": "text[]: product category must be one of these",
    "unit_volume_min": "numeric: min litres per sales unit", "unit_volume_max": "numeric: max litres per sales unit",
    "unit_weight_min": "numeric: min kg per sales unit", "unit_weight_max": "numeric: max kg per sales unit",
    "temperature": "text[]: temperature_condition must be one of these",
    "vendor_names": "text[]: vendor_name must be one of these",
    "brands": "text[]: preferred brands, ORDER BY only, never a filter",
}
SQL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sql"],
              "properties": {"sql": {"type": "string"}}}


class SearchInputError(ValueError):
    """Criteria use codes that are not in get_facets. Message lists them."""


@dataclass
class SearchTrace:
    sql: str = ""
    sql_source: str = ""
    llm_error: str | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)


class SearchEngine:
    def __init__(self, llm: OpenRouter | None = None, use_llm_sql: bool = True):
        self.llm = llm or OpenRouter()
        self.use_llm_sql = use_llm_sql
        self.sql_cache: dict[str, str] = {}          # criteria shape -> guarded SQL from the LLM
        self._facets: tuple[float, Facets] | None = None
        self._schema_card: str | None = None

    # ------------------------------------------------------------ facets
    async def facets(self, conn: psycopg.AsyncConnection) -> Facets:
        if self._facets and time.monotonic() - self._facets[0] < FACETS_TTL_S:
            return self._facets[1]
        cur = conn.cursor(row_factory=dict_row)
        rows = await (await cur.execute("select kind, code, label, derived from wholesaler.code_labels order by kind, code")).fetchall()
        by = lambda k: [CodeLabel(code=r["code"], label=r["label"], derived=r["derived"]) for r in rows if r["kind"] == k]
        f = Facets(claims=by("claim"), allergens=by("allergen"), categories=by("category"),
                   temperatures=by("temperature"), sales_units=by("sales_unit"))
        self._facets = (time.monotonic(), f)
        return f

    async def _validate(self, conn, c: Criteria) -> None:
        f = await self.facets(conn)
        known = {"claims_all": {x.code for x in f.claims}, "allergens_none": {x.code for x in f.allergens},
                 "category_codes": {x.code for x in f.categories}, "temperature": {x.code for x in f.temperatures}}
        bad = {k: sorted(set(getattr(c.must, k) or []) - v) for k, v in known.items()}
        bad = {k: v for k, v in bad.items() if v}
        if bad:
            raise SearchInputError(f"unknown codes (see get_facets): {json.dumps(bad)}")

    # ------------------------------------------------------------ search
    async def search(self, conn: psycopg.AsyncConnection, c: Criteria) -> tuple[SearchResult, SearchTrace]:
        tr, t0 = SearchTrace(), time.monotonic()
        lap = lambda name, t: tr.timings_ms.__setitem__(name, int((time.monotonic() - t) * 1000))

        await self._validate(conn, c)
        params = c.bind_params()
        required = set(template.must_params(params))
        allowed = set(params) | {"q_emb", "k"}

        t = time.monotonic()
        with span("search.embed"):
            params["q_emb"] = await self.llm.embed(params["q_text"])
        params["k"] = K
        lap("embed", t)
        flags = [] if params["q_emb"] else ["EMBED_UNAVAILABLE"]

        shape = c.shape()
        if self.use_llm_sql:
            await self._ensure_schema_card(conn)
        t = time.monotonic()
        with span("search.sqlgen"):
            sql, tr.sql_source = await self._sql_for(c, params, shape, allowed, required, tr)
        lap("sqlgen", t)

        t = time.monotonic()
        with span("search.execute"):
            try:
                rows = await self._run(conn, sql, params)
            except psycopg.Error as e:
                if tr.sql_source == "template":
                    raise
                tr.llm_error = f"execute: {type(e).__name__}"      # bad LLM SQL at runtime: forget it, use template
                self.sql_cache.pop(shape, None)
                sql, tr.sql_source = guard.check(template.build(c, params), allowed, required), "template"
                rows = await self._run(conn, sql, params)
        lap("execute", t)
        tr.sql = sql

        with span("search.postcheck"):
            hits, dropped = [], 0
            for r in rows:
                matched = self._postcheck(c, r)
                if matched is None:
                    dropped += 1
                else:
                    hits.append(ProductHit(**r, matched_must=matched))
            if dropped and tr.sql_source != "template":
                self.sql_cache.pop(shape, None)                   # LLM SQL broke a hard constraint
                flags.append("LLM_SQL_DROPPED")

        diagnosis = None
        if not hits and required:
            t = time.monotonic()
            diagnosis = await self._diagnose(conn, params, allowed, required)
            lap("diagnose", t)
        tr.timings_ms["total"] = int((time.monotonic() - t0) * 1000)
        return SearchResult(query_id=str(uuid.uuid4()), criteria_shape=shape, sql_source=tr.sql_source,
                            hits=hits[: c.limit], dropped_by_postcheck=dropped, diagnosis=diagnosis, flags=flags), tr

    async def _sql_for(self, c, params, shape, allowed, required, tr) -> tuple[str, str]:
        if not self.use_llm_sql:
            return guard.check(template.build(c, params), allowed, required), "template"
        if shape in self.sql_cache:
            return self.sql_cache[shape], "cache"
        error = None
        for _ in range(2):                                          # one retry with the guard's message
            try:
                out = await self.llm.chat_json(await self._prompt(c, params, error), SQL_SCHEMA, "LLM_MODEL_SQL")
                sql = guard.check(out["sql"], allowed, required)
                self.sql_cache[shape] = sql
                return sql, "llm"
            except guard.SqlRejected as e:
                error = str(e)
            except RuntimeError as e:                               # LLM unavailable
                error = str(e)
                break
        tr.llm_error = error
        return guard.check(template.build(c, params), allowed, required), "template"

    async def _prompt(self, c: Criteria, params: dict, error: str | None) -> str:
        names = [k for k in PARAM_DOCS if k in params]
        example_params = {"claims_all": ["X"], "unit_volume_min": 1, "q_text": "x", "limit": 5}
        example = template.build(Criteria(query_text="x", must={"claims_all": ["X"], "unit_volume_l": {"min": 1}}),
                                 example_params)
        return f"""Write ONE PostgreSQL SELECT for a product search.

SCHEMA (the only relations you may use):
{self._schema_card}

RULES
1. One SELECT only. Read only wholesaler.v_product_search (alias p) joined with wholesaler.hybrid_rank(:q_text, :q_emb, :k) h on h.product_id = p.product_id.
2. Select exactly: p.*, h.rrf_score
3. Values ONLY as named bind parameters (:name). Never write literal values or strings.
4. Use exactly these parameters, each hard-constraint parameter exactly once in WHERE, joined with AND: {", ".join(names)}
5. Array parameters are always wrapped as cast(:name as text[]).
6. :brands (if present) only in ORDER BY, never in WHERE.
7. Sort: {c.prefer.sort}. Relevance = h.rrf_score desc; delivery_reliability = p.delivery_reliability desc nulls last; co2 = p.co2_kg_per_kg asc nulls last.
8. End with LIMIT :limit.

PARAMETERS
{chr(10).join(f"- :{k} {PARAM_DOCS[k]}" for k in names)}

EXAMPLE (different parameters):
{example}
{f"{chr(10)}YOUR PREVIOUS SQL WAS REJECTED: {error}. Fix it." if error else ""}
Return JSON {{"sql": "..."}}."""

    async def _ensure_schema_card(self, conn) -> None:
        if self._schema_card:
            return
        rows = await (await conn.execute("""
            select a.attname, format_type(a.atttypid, a.atttypmod), coalesce(col_description(a.attrelid, a.attnum), '')
            from pg_attribute a where a.attrelid = 'wholesaler.v_product_search'::regclass and a.attnum > 0
            order by a.attnum""")).fetchall()
        fn = (await (await conn.execute(
            "select obj_description('wholesaler.hybrid_rank(text,text,int)'::regprocedure)")).fetchone())[0]
        self._schema_card = ("view wholesaler.v_product_search p:\n"
                             + "\n".join(f"  {n} {t}: {d}" for n, t, d in rows)
                             + f"\nfunction wholesaler.hybrid_rank(q_text text, q_emb text, k int) returns (product_id int, rrf_score float8): {fn}")

    async def _run(self, conn, sql: str, params: dict) -> list[dict]:
        async with conn.transaction():
            await conn.execute("set transaction read only")
            await conn.execute("set local role search_ro")
            await conn.execute(f"set local statement_timeout = '{SQL_TIMEOUT_S}s'")
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(sql, params)
            return await cur.fetchall()

    @staticmethod
    def _postcheck(c: Criteria, r: dict) -> list[str] | None:
        """Names of the hard constraints this row satisfies, or None if it breaks any."""
        m, ok = c.must, []
        def need(cond: bool, name: str) -> bool:
            if cond:
                ok.append(name)
            return cond
        def in_range(v, rng) -> bool:
            return v is not None and (rng.min is None or v >= rng.min) and (rng.max is None or v <= rng.max)
        checks = [
            (not m.claims_all) or need(set(m.claims_all) <= set(r.get("claims") or []), "claims_all"),
            (not m.allergens_none) or need(not set(m.allergens_none) & set(r.get("allergens_contains") or []), "allergens_none"),
            (not m.category_codes) or need(r.get("category_code") in m.category_codes, "category_codes"),
            (m.unit_volume_l is None) or need(in_range(r.get("unit_volume_l"), m.unit_volume_l), "unit_volume_l"),
            (m.unit_weight_kg is None) or need(in_range(r.get("unit_weight_kg"), m.unit_weight_kg), "unit_weight_kg"),
            (not m.temperature) or need(r.get("temperature_condition") in m.temperature, "temperature"),
            (not m.vendor_names) or need(r.get("vendor_name") in m.vendor_names, "vendor_names"),
        ]
        return ok if all(checks) else None

    async def _diagnose(self, conn, params, allowed, required) -> list[Relaxation]:
        out = []
        for name in template.must_params(params):
            sql = guard.check(template.count(params, skip=name), allowed, required - {name})
            n = (await self._run(conn, sql, params))[0]["n"]
            out.append(Relaxation(field=name, count_if_dropped=n))
        return sorted(out, key=lambda r: -r.count_if_dropped)
