import pytest
from tta_contracts import Criteria

from search.catalog import RANKED, template_for
from search.sql_guard import checked_sql

APPROVED = template_for(
    Criteria(query_text="milk", must={"claims_all": ["FREE_FROM_LACTOSE"]}),
    RANKED,
    "h.rrf_score",
)


def test_valid_structure_is_canonicalized():
    assert checked_sql(APPROVED.replace("SELECT", "select"), APPROVED) == APPROVED


@pytest.mark.parametrize(
    "candidate",
    [
        "DELETE FROM wholesaler.products",
        APPROVED + "; SELECT 1",
        APPROVED.replace("v_product_search", "agents"),
        APPROVED.replace("LIMIT :limit", ""),
        APPROVED.replace(":limit", "10"),
        APPROVED.replace("p.*", "pg_sleep(:limit)"),
        APPROVED.replace("p.claims @>", "p.claims &&"),
    ],
)
def test_unapproved_sql_is_rejected(candidate):
    with pytest.raises(ValueError):
        checked_sql(candidate, APPROVED)
