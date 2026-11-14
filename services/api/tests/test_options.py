from decimal import Decimal

import pytest
from tta_contracts import Criteria, Prefer

from app.models import ParsedRequest
from app.options import build_options, units_needed
from tests.factories import DELIVERY, hit, offer


@pytest.mark.parametrize(
    "quantity,size,lot,expected",
    [
        (40, 1, 10, 40),
        (45, 1, 10, 50),
        (1, 0.3, 1, 4),
        (0.3, 0.1, 1, 3),
        (1, 0.5, None, 2),
        (3, 1, 5, 5),
    ],
)
def test_quantity_rounds_up_to_sales_units_and_lots(quantity, size, lot, expected):
    assert units_needed(Decimal(str(quantity)), Decimal(str(size)), lot) == expected


@pytest.mark.parametrize(
    "quantity,size,lot",
    [
        (0, 1, 1),
        (-1, 1, 1),
        (1, 0, 1),
        (1, -1, 1),
        (1, 1, 0),
        (1, 1, -1),
        ("NaN", 1, 1),
        (1, "Infinity", 1),
    ],
)
def test_invalid_measurements_are_not_guessed(quantity, size, lot):
    with pytest.raises(ValueError):
        units_needed(Decimal(str(quantity)), Decimal(str(size)), lot)


def request(quantity=40, unit="l", **kwargs):
    return ParsedRequest(
        criteria=Criteria(query_text="milk", **kwargs),
        quantity=quantity,
        quantity_unit=unit,
        delivery_date=DELIVERY,
    )


def test_ranking_produces_three_distinct_choices_and_does_not_use_llm():
    hits = [
        hit(1, rrf_score=0.8),
        hit(2, rrf_score=0.2),
        hit(3, rrf_score=0.1, delivery_reliability=0.99),
    ]
    offers = [offer(1), offer(2, unit_price_eur="0.80"), offer(3, unit_price_eur="1.50")]
    options = build_options(request(), hits, offers)
    assert [(o.option.label, o.option.lines[0].product_id) for o in options] == [
        ("best_match", 1),
        ("cheapest", 2),
        ("most_reliable", 3),
    ]
    assert options[1].total_eur == Decimal("32.00")


def test_duplicate_choices_are_removed():
    assert len(build_options(request(), [hit()], [offer()])) == 1


def test_cheapest_compares_price_per_requested_unit_not_price_per_carton():
    options = build_options(
        request(),
        [hit(1), hit(2, unit_volume_l=10)],
        [offer(1, unit_price_eur="1"), offer(2, unit_price_eur="5")],
    )
    cheapest = next(o for o in options if o.option.label == "cheapest")
    assert cheapest.option.lines[0].product_id == 2


def test_brand_breaks_a_relevance_tie_and_co2_breaks_a_reliability_tie():
    options = build_options(
        request(prefer=Prefer(brands=["Valio"])),
        [hit(1, brand="Other", co2_kg_per_kg=0.1), hit(2, co2_kg_per_kg=2)],
        [offer(1), offer(2, unit_price_eur="0.80")],
    )
    assert options[0].option.lines[0].product_id == 2
    reliable = next(o for o in options if o.option.label == "most_reliable")
    assert reliable.option.lines[0].product_id == 1


@pytest.mark.parametrize(
    "product,price",
    [
        (hit(unit_volume_l=None), offer()),
        (hit(), offer(unit_price_eur=None)),
        (hit(), offer(available_on_date=None)),
        (hit(), offer(sales_block="blocked")),
        (hit(), offer(error="upstream error")),
        (hit(allowed_lot_size=0), offer()),
        (hit(), offer(unit_price_eur="-1")),
    ],
)
def test_unknown_size_price_stock_and_blocked_offers_are_excluded(product, price):
    assert build_options(request(), [product], [price]) == []


def test_split_respects_stock_lots_and_fulfils_complete_quantity():
    options = build_options(
        request(45),
        [hit(1), hit(2)],
        [offer(1, available_on_date=25), offer(2, available_on_date=35)],
    )
    assert len(options) == 1
    assert options[0].option.label == "split_or_substitute"
    assert [(line.product_id, line.quantity_units) for line in options[0].option.lines] == [
        (1, 20),
        (2, 30),
    ]
    assert options[0].supplied_quantity == 50


def test_incomplete_split_is_never_submitted():
    assert (
        build_options(
            request(100),
            [hit(1), hit(2)],
            [offer(1, available_on_date=20), offer(2, available_on_date=20)],
        )
        == []
    )


def test_pcs_means_sales_units_even_without_volume_or_weight():
    options = build_options(request(5, "pcs"), [hit(unit_volume_l=None, unit_weight_kg=None)], [offer()])
    assert options[0].option.lines[0].quantity_units == 10
