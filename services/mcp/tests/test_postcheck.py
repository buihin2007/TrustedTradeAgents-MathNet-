from types import SimpleNamespace

from tta_contracts import Criteria, Must, ProductHit

from search.catalog import Catalog


async def test_catalog_postcheck_evicts_sql_that_returns_an_out_of_scope_row(monkeypatch):
    criteria = Criteria(query_text="milk", must={"claims_all": ["FREE_FROM_LACTOSE"]})

    async def embed(_):
        return None

    catalog = Catalog(SimpleNamespace(embed=embed))
    catalog.sql_cache[criteria.shape()] = "cached query"
    product = ProductHit(
        product_id=1,
        name_en="Milk",
        name_fi=None,
        brand=None,
        vendor_name=None,
        category_code=None,
        category_name=None,
        sales_unit="ST",
        unit_volume_l=1,
        unit_weight_kg=None,
        allowed_lot_size=1,
        temperature_condition=None,
        claims=["FREE_FROM_LACTOSE"],
        delivery_reliability=None,
        co2_kg_per_kg=None,
    ).model_dump()

    async def read(*_):
        return [product, {**product, "product_id": 2, "claims": []}]

    monkeypatch.setattr(catalog, "_read_only", read)
    result, _ = await catalog.search(None, criteria, Must())
    assert [hit.product_id for hit in result.hits] == [1]
    assert result.dropped_by_postcheck == 1
    assert criteria.shape() not in catalog.sql_cache
