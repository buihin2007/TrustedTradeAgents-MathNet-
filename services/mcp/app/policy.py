"""Order rules over gateway-verified catalog, pricing and stock."""

from collections import Counter
from decimal import Decimal

from tta_contracts import (
    Intent,
    MandateLimits,
    Must,
    Offer,
    OrderOption,
    OrderStatus,
    PricedLine,
    PricedOption,
    ProductHit,
)
from tta_contracts import (
    ReasonCode as Reason,
)

BLOCK_REASONS = {
    Reason.MUST_VIOLATED,
    Reason.OVER_ORDER_LIMIT,
    Reason.OVER_WEEKLY_BUDGET,
    Reason.LOT_SIZE,
    Reason.JEV_BLOCK,
}


def mismatches(product: ProductHit, must: Must) -> list[str]:
    failures = []
    if not set(must.claims_all).issubset(product.claims):
        failures.append("claims_all")
    if set(must.allergens_none).intersection(product.allergens_contains):
        failures.append("allergens_none")
    for field, value in (
        ("category_codes", product.category_code),
        ("temperature", product.temperature_condition),
        ("vendor_names", product.vendor_name),
    ):
        accepted = getattr(must, field)
        if accepted and value not in accepted:
            failures.append(field)
    for field in ("unit_volume_l", "unit_weight_kg"):
        bounds = getattr(must, field)
        if bounds is None or (bounds.min is None and bounds.max is None):
            continue
        value = getattr(product, field)
        if (
            value is None
            or (bounds.min is not None and value < bounds.min)
            or (bounds.max is not None and value > bounds.max)
        ):
            failures.append(field)
    return failures


def in_scope(product: ProductHit, intent: Intent, mandate: MandateLimits) -> bool:
    categories = mandate.allowed_category_codes
    return not mismatches(product, intent.criteria.must) and (
        categories is None or product.category_code in categories
    )


def evaluate_option(
    option: OrderOption,
    intent: Intent,
    mandate: MandateLimits,
    products: dict[int, ProductHit],
    offers: dict[int, Offer],
    reference_prices: dict[int, Decimal],
    weekly_spend: Decimal,
    substitutions: set[tuple[int, int]],
) -> PricedOption:
    reasons: set[Reason] = set()
    quantities: Counter[int] = Counter()
    lines = []
    total = Decimal(0)
    for line in option.lines:
        quantities[line.product_id] += line.quantity_units
        # quantity_units and allowed_lot_size are floats (lot 0.01); compare as decimals, 2.0 % 0.01 != 0 in float
        quantity = Decimal(str(line.quantity_units))
        product = products.get(line.product_id)
        if product is None or not in_scope(product, intent, mandate):
            reasons.add(Reason.MUST_VIOLATED)
        if product and (
            quantity > 2_147_483_647
            or quantity % Decimal(str(product.allowed_lot_size or 1))
        ):
            reasons.add(Reason.LOT_SIZE)
        if line.substitute_for is not None:
            original = products.get(line.substitute_for)
            if (
                not mandate.allow_substitution
                or original is None
                or not in_scope(original, intent, mandate)
                or (line.substitute_for, line.product_id) not in substitutions
            ):
                reasons.add(Reason.MUST_VIOLATED)
            reasons.add(Reason.SUBSTITUTION)

        offer = offers.get(line.product_id)
        if not offer or offer.unit_price_eur is None or offer.error:
            reasons.add(Reason.PARTIAL_AVAILABILITY)
            continue
        amount = (offer.unit_price_eur * quantity).quantize(Decimal("0.01"))
        prior = reference_prices.get(line.product_id)
        if (
            prior is None
            or abs(offer.unit_price_eur - prior) * 100 > prior * mandate.price_tolerance_pct
        ):
            reasons.add(Reason.PRICE_MOVED)
        lines.append(
            PricedLine(
                product_id=line.product_id,
                name_en=product.name_en if product else None,
                quantity_units=line.quantity_units,
                unit_price_eur=offer.unit_price_eur,
                line_total_eur=amount,
                available_units=offer.available_on_date,
                substitute_for=line.substitute_for,
            )
        )
        total += amount

    for product_id, quantity in quantities.items():
        offer = offers.get(product_id)
        if not offer or (offer.available_on_date or 0) < quantity:
            reasons.add(Reason.PARTIAL_AVAILABILITY)

    if total > mandate.order_limit_eur:
        reasons.add(Reason.OVER_ORDER_LIMIT)
    if weekly_spend + total > mandate.weekly_budget_eur:
        reasons.add(Reason.OVER_WEEKLY_BUDGET)
    if total > mandate.auto_limit_eur:
        reasons.add(Reason.OVER_AUTO_LIMIT)
    return PricedOption(
        label=option.label,
        lines=lines,
        total_eur=total,
        explanation=option.explanation,
        reasons=sorted(reasons),
    )


def order_status(reasons: set[Reason]) -> OrderStatus:
    if reasons & BLOCK_REASONS:
        return OrderStatus.BLOCKED
    if reasons:
        return OrderStatus.HELD
    return OrderStatus.PLACED
