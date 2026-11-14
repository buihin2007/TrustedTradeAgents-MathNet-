"""Facets, structured filters, guarded hybrid search and zero-hit diagnostics."""

import re
from collections import defaultdict
from math import isfinite
from time import monotonic
from uuid import uuid4

from fastmcp.exceptions import ToolError
from fastmcp.telemetry import get_tracer
from psycopg.errors import QueryCanceled
from tta_contracts import CodeLabel, Criteria, Facets, Must, ProductHit, Relaxation, SearchResult

from app.db import all_rows
from app.openrouter import MODEL_ERRORS, OpenRouter
from app.policy import mismatches

from .sql_guard import checked_sql

FACET_FIELDS = {
    "claim": "claims",
    "allergen": "allergens",
    "category": "categories",
    "temperature": "temperatures",
    "sales_unit": "sales_units",
}
PREDICATES = {
    "claims_all": "p.claims @> CAST(:claims_all AS text[])",
    "allergens_none": "NOT (p.allergens_contains && CAST(:allergens_none AS text[]))",
    "category_codes": "p.category_code = ANY(CAST(:category_codes AS text[]))",
    "temperature": "p.temperature_condition = ANY(CAST(:temperature AS text[]))",
    "vendor_names": "p.vendor_name = ANY(CAST(:vendor_names AS text[]))",
}
ORDER_BY = {
    "relevance": "h.rrf_score DESC",
    "delivery_reliability": "p.delivery_reliability DESC NULLS LAST, h.rrf_score DESC",
    "co2": "p.co2_kg_per_kg ASC NULLS LAST, h.rrf_score DESC",
}
RANKED = """FROM wholesaler.v_product_search AS p
JOIN wholesaler.hybrid_rank(:q_text, :q_emb, :k) AS h ON h.product_id = p.product_id"""
PARAMETER = re.compile(r"(?<!:):([a-z_]+)")


def conditions(criteria: Criteria) -> dict[str, str]:
    must = criteria.must
    predicates = {field: sql for field, sql in PREDICATES.items() if getattr(must, field)}
    for field, stem in (("unit_volume_l", "unit_volume"), ("unit_weight_kg", "unit_weight")):
        bounds = getattr(must, field)
        if bounds and bounds.min is not None and bounds.max is not None:
            predicates[field] = f"p.{field} BETWEEN :{stem}_min AND :{stem}_max"
        elif bounds and bounds.min is not None:
            predicates[field] = f"p.{field} >= :{stem}_min"
        elif bounds and bounds.max is not None:
            predicates[field] = f"p.{field} <= :{stem}_max"
    return predicates


def filters(predicates) -> str:
    return "WHERE " + " AND ".join(predicates) if predicates else ""


def template_for(criteria: Criteria, source: str, score: str) -> str:
    order = ORDER_BY[criteria.prefer.sort].replace("h.rrf_score", score)
    if criteria.prefer.brands:
        order = f"(p.brand = ANY(CAST(:brands AS text[]))) DESC NULLS LAST, {order}"
    return f"""SELECT p.*, {score} AS rrf_score
{source}
{filters(list(conditions(criteria).values()))}
ORDER BY {order}, p.product_id
LIMIT :limit"""


def validate_size(criteria: Criteria) -> None:
    for field in (*PREDICATES, "brands"):
        values = criteria.prefer.brands if field == "brands" else getattr(criteria.must, field)
        if values and (
            len(values) > 100 or any(not v or len(v) > 200 or "\x00" in v for v in values)
        ):
            raise ToolError(f"Invalid criteria field: {field}")
    for field in ("unit_volume_l", "unit_weight_kg"):
        bounds = getattr(criteria.must, field)
        if bounds and any(
            v is not None and (not isfinite(v) or v < 0) for v in (bounds.min, bounds.max)
        ):
            raise ToolError(f"Invalid criteria field: {field}")
    if not criteria.query_text.strip() or any(
        "\x00" in value for value in (criteria.query_text, criteria.query_text_alt or "")
    ):
        raise ToolError("Invalid query text")


class Catalog:
    def __init__(self, router: OpenRouter):
        self.router = router
        self.facet_cache: tuple[float, Facets] | None = None
        self.sql_cache: dict[str, str] = {}
        self.schema: str | None = None

    async def facets(self, conn) -> Facets:
        if self.facet_cache and self.facet_cache[0] > monotonic():
            return self.facet_cache[1]
        rows = await all_rows(
            conn,
            """
            select kind, code, label, derived from wholesaler.code_labels order by kind, code
            """,
        )
        groups = defaultdict(list)
        for row in rows:
            groups[FACET_FIELDS[row["kind"]]].append(CodeLabel.model_validate(row))
        facets = Facets(**{field: groups[field] for field in FACET_FIELDS.values()})
        self.facet_cache = monotonic() + 600, facets
        return facets

    async def validate(self, conn, criteria: Criteria) -> None:
        validate_size(criteria)
        facets = await self.facets(conn)
        for field, valid in (
            ("claims_all", facets.claims),
            ("allergens_none", facets.allergens),
            ("category_codes", facets.categories),
            ("temperature", facets.temperatures),
        ):
            values = getattr(criteria.must, field)
            if values and not set(values).issubset({entry.code for entry in valid}):
                raise ToolError(f"Unknown facet code in {field}")

    async def _schema_card(self, conn) -> str:
        if self.schema is None:
            rows = await all_rows(
                conn,
                """
              select attname, col_description(attrelid, attnum) as description
              from pg_catalog.pg_attribute
              where attrelid = 'wholesaler.v_product_search'::regclass
                and attnum > 0 and not attisdropped order by attnum
              """,
            )
            self.schema = "\n".join(f"{row['attname']}: {row['description'] or ''}" for row in rows)
        return self.schema

    async def _query(self, conn, criteria: Criteria, template: str) -> tuple[str, str]:
        shape = criteria.shape()
        if shape in self.sql_cache:
            return self.sql_cache[shape], "cache"
        if not self.router.llm_model or not self.router.key:
            return template, "template"
        schema = await self._schema_card(conn)
        prompt = (
            "Return a JSON object with only a sql string. Use this read-only PostgreSQL "
            "SELECT structure and preserve every named parameter. Never embed values or literals. "
            f"Catalog view columns:\n{schema}\nApproved query:\n{template}"
        )
        with get_tracer().start_as_current_span("search.sqlgen", record_exception=False):
            for attempt in range(2):
                try:
                    candidate = await self.router.generate_sql(prompt)
                    with get_tracer().start_as_current_span("search.guard", record_exception=False):
                        guarded = checked_sql(candidate, template)
                    if len(self.sql_cache) >= 256:
                        self.sql_cache.pop(next(iter(self.sql_cache)))
                    self.sql_cache[shape] = guarded
                    return guarded, "llm"
                except MODEL_ERRORS:
                    if attempt == 0:
                        prompt += "\nThe last response failed validation. Return the approved query unchanged."
        return template, "template"

    async def search(self, conn, criteria: Criteria, intent_must: Must) -> tuple[SearchResult, str]:
        shape = criteria.shape()
        params = criteria.bind_params()
        gtin = re.fullmatch(r"[0-9]{8,14}", criteria.query_text.strip())
        if gtin:
            sql = template_for(
                criteria,
                "FROM wholesaler.v_product_search AS p JOIN wholesaler.products AS source "
                "ON source.id = p.product_id AND (:gtin IN (source.gtin, source.sales_unit_gtin))",
                "NULL::float8",
            )
            params["gtin"] = gtin.group()
            source = "gtin"
        else:
            sql, source = await self._query(
                conn, criteria, template_for(criteria, RANKED, "h.rrf_score")
            )
            with get_tracer().start_as_current_span("search.embed", record_exception=False):
                params["q_emb"] = await self.router.embed(params["q_text"])
            params["k"] = 300

        try:
            with get_tracer().start_as_current_span("search.execute", record_exception=False):
                if gtin:
                    rows = await all_rows(conn, PARAMETER.sub(r"%(\1)s", sql), params)
                else:
                    rows = await self._read_only(conn, sql, params)
        except QueryCanceled as exc:
            raise ToolError("Catalog search timed out") from exc

        products = [ProductHit.model_validate(row) for row in rows]
        hits = []
        with get_tracer().start_as_current_span("search.postcheck"):
            for item in products:
                if not mismatches(item, criteria.must) and not mismatches(item, intent_must):
                    item.matched_must = list(conditions(criteria))
                    hits.append(item)
        dropped = len(products) - len(hits)
        if dropped:
            self.sql_cache.pop(shape, None)
        diagnosis = None
        if not hits and not gtin and conditions(criteria):
            diagnosis = await self._diagnose(conn, criteria, params)
        return SearchResult(
            query_id=str(uuid4()),
            criteria_shape=shape,
            hits=hits,
            sql_source=source,
            dropped_by_postcheck=dropped,
            diagnosis=diagnosis,
        ), sql

    async def _read_only(self, conn, sql: str, params: dict) -> list[dict]:
        # Always roll back the savepoint to restore the role, read-only flag and timeout.
        async with conn.transaction(force_rollback=True):
            await conn.execute("SET LOCAL transaction_read_only = on")
            await conn.execute("SET LOCAL ROLE search_ro")
            await conn.execute("SET LOCAL search_path = pg_catalog")
            await conn.execute("SET LOCAL statement_timeout = '2s'")
            return await all_rows(conn, PARAMETER.sub(r"%(\1)s", sql), params)

    async def _diagnose(self, conn, criteria: Criteria, params: dict) -> list[Relaxation] | None:
        predicates = conditions(criteria)
        # One aggregate query gives a count per omitted constraint without returning catalog rows.
        counts = []
        for index, key in enumerate(predicates):
            other = [predicate for field, predicate in predicates.items() if field != key]
            clause = f" FILTER (WHERE {' AND '.join(other)})" if other else ""
            counts.append(f"COUNT(*){clause} AS count_{index}")
        try:
            [row] = await self._read_only(conn, f"SELECT {', '.join(counts)} {RANKED}", params)
        except QueryCanceled:
            return None
        return [
            Relaxation(field=field, count_if_dropped=row[f"count_{i}"])
            for i, field in enumerate(predicates)
        ]


async def substitutes(conn, ids: list[int]) -> list[tuple[int, ProductHit]]:
    rows = await all_rows(
        conn,
        """
        select s.product_id as original_id, p.*
        from wholesaler.product_substitutions as s
        join wholesaler.v_product_search as p on p.product_id = s.substitute_id
        where s.product_id = any(%s)
        order by s.product_id, s.rank nulls last, p.product_id
        """,
        (ids,),
    )
    return [(row["original_id"], ProductHit.model_validate(row)) for row in rows]
