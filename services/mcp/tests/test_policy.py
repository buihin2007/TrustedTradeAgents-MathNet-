from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from tta_contracts import (
    Criteria,
    Intent,
    MandateLimits,
    Offer,
    OrderOption,
    ProductHit,
    ReasonCode,
)

from app.policy import evaluate_option, order_status


@pytest.mark.parametrize(
    ("qty", "updates", "expected", "reason"),
    [
        (40, {}, "placed", None),
        (300, {}, "held", "OVER_AUTO_LIMIT"),
        (600, {}, "blocked", "OVER_ORDER_LIMIT"),
        (40, {"weekly_spend": Decimal(1980)}, "blocked", "OVER_WEEKLY_BUDGET"),
        (41, {"lot": 2}, "blocked", "LOT_SIZE"),
        (40, {"claims": []}, "blocked", "MUST_VIOLATED"),
        (40, {"stock": 39}, "held", "PARTIAL_AVAILABILITY"),
        (40, {"prior": Decimal(1)}, "held", "PRICE_MOVED"),
        (40, {"substitute": True}, "held", "SUBSTITUTION"),
    ],
)
def test_policy(qty, updates, expected, reason):
    now = datetime.now(UTC)
    intent = Intent(
        intent_id="intent",
        org_id="org",
        user_id="user",
        request_text="Milk",
        quantity=qty,
        quantity_unit="l",
        delivery_date=date.today(),
        created_at=now,
        expires_at=now,
        criteria=Criteria(query_text="milk", must={"claims_all": ["FREE_FROM_LACTOSE"]}),
    )
    product = ProductHit(
        product_id=1,
        name_en="Milk",
        name_fi=None,
        brand=None,
        vendor_name=None,
        sales_unit="ST",
        category_code="10400",
        category_name=None,
        unit_volume_l=1,
        unit_weight_kg=None,
        temperature_condition=None,
        claims=updates.get("claims", ["FREE_FROM_LACTOSE"]),
        allowed_lot_size=updates.get("lot", 1),
        delivery_reliability=None,
        co2_kg_per_kg=None,
    )
    offer = Offer(
        product_id=1,
        unit_price_eur=Decimal("1.20"),
        unit_price_with_tax_eur=None,
        comparison_price_eur=None,
        comparison_unit=None,
        logistic_model="Stock",
        available_on_date=updates.get("stock", 1000),
    )
    option = OrderOption(
        label="best_match",
        lines=[
            {
                "product_id": 1,
                "quantity_units": qty,
                "substitute_for": 2 if updates.get("substitute") else None,
            }
        ],
    )
    result = evaluate_option(
        option,
        intent,
        MandateLimits(),
        {1: product, 2: product},
        {1: offer},
        {1: updates.get("prior", Decimal("1.20"))},
        updates.get("weekly_spend", Decimal(0)),
        {(2, 1)},
    )
    assert result.total_eur == qty * Decimal("1.20")
    assert order_status(set(result.reasons)) == expected
    if reason:
        assert ReasonCode(reason) in result.reasons
