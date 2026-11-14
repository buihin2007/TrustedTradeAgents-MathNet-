"""Search layer tests. Guard tests need nothing; DB tests need TEST_DATABASE_URL with imported products.

    cd services/mcp && uv run --with pytest pytest search/tests -q
"""
import asyncio
import os

import pytest

from search import SearchEngine, SearchInputError, SqlRejected
from search import guard, template
from tta_contracts import Criteria

MILK = Criteria(query_text="lactose free milk 1 l", query_text_alt="laktoositon maito 1 l",
                must={"claims_all": ["FREE_FROM_LACTOSE"], "unit_volume_l": {"min": 0.9, "max": 1.1}},
                prefer={"brands": ["Valio"]}, limit=10)


def _check(sql: str, c: Criteria = MILK):
    params = c.bind_params()
    return guard.check(sql, set(params) | {"q_emb", "k"}, set(template.must_params(params)))


# ---------------------------------------------------------------- guard (no database)
def test_template_passes_guard():
    assert "%(claims_all)s" in _check(template.build(MILK, MILK.bind_params()))


@pytest.mark.parametrize("sql, reason", [
    ("delete from wholesaler.v_product_search", "only SELECT"),
    ("select 1; select 2", "exactly 1 statement"),
    ("select p.* from wholesaler.products p join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h on true "
     "where p.claims @> cast(:claims_all as text[]) and p.unit_volume_l between :unit_volume_min and :unit_volume_max limit :limit",
     "relation not allowed"),
    ("select p.*, pg_sleep(10) from wholesaler.v_product_search p join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h "
     "on h.product_id = p.product_id where p.claims @> cast(:claims_all as text[]) "
     "and p.unit_volume_l between :unit_volume_min and :unit_volume_max limit :limit", "function not allowed"),
    ("select p.* from wholesaler.v_product_search p join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h "
     "on h.product_id = p.product_id where p.claims @> array['FREE_FROM_LACTOSE'] "
     "and p.unit_volume_l between :unit_volume_min and :unit_volume_max limit :limit", "string literals"),
    ("select p.* from wholesaler.v_product_search p join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h "
     "on h.product_id = p.product_id where p.unit_volume_l between :unit_volume_min and :unit_volume_max limit :limit",
     "parameters not used"),
    ("select p.* from wholesaler.v_product_search p join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h "
     "on h.product_id = p.product_id where p.claims @> cast(:claims_all as text[]) "
     "and p.unit_volume_l between :unit_volume_min and :unit_volume_max", "LIMIT is required"),
    ("select p.* from wholesaler.v_product_search p join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h "
     "on h.product_id = p.product_id where p.claims @> cast(:claims_all as text[]) "
     "and p.unit_volume_l between :unit_volume_min and :unit_volume_max limit 5000", "LIMIT must be"),
    ("select p.* from wholesaler.v_product_search p join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h "
     "on h.product_id = p.product_id where p.claims @> cast(:claims_all as text[]) and p.vendor_name = :evil "
     "and p.unit_volume_l between :unit_volume_min and :unit_volume_max limit :limit", "unknown parameters"),
])
def test_guard_rejects(sql, reason):
    with pytest.raises(SqlRejected, match=reason):
        _check(sql)


# ---------------------------------------------------------------- database
DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL not set")


class FakeLLM:
    def __init__(self, sql=None, embedding=None):
        self.sql, self.embedding, self.chat_calls = sql, embedding, 0

    async def embed(self, text):
        return self.embedding

    async def chat_json(self, prompt, schema, model_env="LLM_MODEL"):
        self.chat_calls += 1
        if self.sql is None:
            raise RuntimeError("LLM down")
        return {"sql": self.sql(prompt) if callable(self.sql) else self.sql}


def run(engine, c):
    import psycopg

    async def go():
        async with await psycopg.AsyncConnection.connect(DB, prepare_threshold=None) as conn:
            return await engine.search(conn, c)
    return asyncio.run(go())


@needs_db
def test_template_search_finds_lactose_free_milk():
    res, tr = run(SearchEngine(FakeLLM(), use_llm_sql=False), MILK)
    assert tr.sql_source == "template" and res.hits
    assert all("FREE_FROM_LACTOSE" in h.claims and 0.9 <= h.unit_volume_l <= 1.1 for h in res.hits)
    assert res.hits[0].brand == "Valio"                     # preferred brand ranks first


@needs_db
def test_llm_sql_then_cache():
    good = template.build(MILK, MILK.bind_params())
    llm = FakeLLM(sql=good)
    engine = SearchEngine(llm)
    r1, t1 = run(engine, MILK)
    r2, t2 = run(engine, Criteria.model_validate(MILK.model_dump() | {"query_text": "lactose free milk drink"}))
    assert (t1.sql_source, t2.sql_source, llm.chat_calls) == ("llm", "cache", 1)


@needs_db
def test_bad_llm_sql_falls_back_to_template():
    llm = FakeLLM(sql="drop table wholesaler.products")
    res, tr = run(SearchEngine(llm), MILK)
    assert tr.sql_source == "template" and "only SELECT" in tr.llm_error and llm.chat_calls == 2 and res.hits


@needs_db
def test_postcheck_drops_rows_from_wrong_llm_sql():
    # valid shape, wrong meaning: overlaps (&&) instead of contains (@>) and the volume range ignored
    wrong = ("select p.*, h.rrf_score from wholesaler.v_product_search p join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h "
             "on h.product_id = p.product_id where p.claims && cast(:claims_all as text[]) "
             "and (p.unit_volume_l >= :unit_volume_min or p.unit_volume_l <= :unit_volume_max or true) "
             "order by h.rrf_score desc limit :limit")
    engine = SearchEngine(FakeLLM(sql=wrong))
    res, tr = run(engine, MILK)
    assert all(0.9 <= h.unit_volume_l <= 1.1 for h in res.hits)
    assert res.dropped_by_postcheck > 0 and "LLM_SQL_DROPPED" in res.flags and not engine.sql_cache


@needs_db
def test_zero_results_explains_which_constraint_to_relax():
    c = Criteria.model_validate(MILK.model_dump() | {"must": MILK.must.model_dump() | {"unit_volume_l": {"min": 0.01, "max": 0.02}}})
    res, _ = run(SearchEngine(FakeLLM(), use_llm_sql=False), c)
    assert not res.hits and res.diagnosis
    assert res.diagnosis[0].field.startswith("unit_volume") and res.diagnosis[0].count_if_dropped > 0


@needs_db
def test_unknown_codes_are_rejected():
    c = Criteria.model_validate(MILK.model_dump() | {"must": MILK.must.model_dump() | {"claims_all": ["FREE_FROM_EVERYTHING"]}})
    with pytest.raises(SearchInputError, match="FREE_FROM_EVERYTHING"):
        run(SearchEngine(FakeLLM(), use_llm_sql=False), c)
