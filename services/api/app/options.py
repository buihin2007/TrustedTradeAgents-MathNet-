"""Deterministic quantity conversion and proposal construction; no policy decisions."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from math import isfinite

from tta_contracts import Offer, OrderLineIn, OrderOption, ProductHit

from .models import OptionPreview, ParsedRequest


def units_needed(quantity: Decimal, size: Decimal, lot: int | None) -> int:
    if not quantity.is_finite() or not size.is_finite() or quantity <= 0 or size <= 0:
        raise ValueError("Quantity and sales-unit size must be positive and finite")
    if lot is not None and lot <= 0:
        raise ValueError("Invalid lot size")
    step = lot or 1
    return int((quantity / size / step).to_integral_value(rounding=ROUND_CEILING)) * step


def unit_size(product: ProductHit, unit: str) -> Decimal | None:
    # pcs means sales units: no inference of pieces inside a carton is made.
    value = 1 if unit == "pcs" else (product.unit_volume_l if unit == "l" else product.unit_weight_kg)
    if value is None or not isfinite(value) or value <= 0:
        return None
    return Decimal(str(value))


@dataclass
class Candidate:
    product: ProductHit
    offer: Offer
    size: Decimal
    units: int
    enough: bool

    @property
    def total(self) -> Decimal:
        return self.offer.unit_price_eur * self.units


def candidates(parsed: ParsedRequest, hits: list[ProductHit], offers: list[Offer]) -> list[Candidate]:
    if parsed.quantity is None or parsed.quantity_unit is None:
        raise ValueError("Quantity and unit are required")
    prices = {o.product_id: o for o in offers}
    result = []
    for product in hits:
        offer = prices.get(product.product_id)
        size = unit_size(product, parsed.quantity_unit)
        if (
            offer is None
            or offer.error
            or offer.sales_block
            or size is None
            or offer.unit_price_eur is None
            or not offer.unit_price_eur.is_finite()
            or offer.unit_price_eur < 0
            or product.allowed_lot_size == 0
            or (product.allowed_lot_size is not None and product.allowed_lot_size < 0)
            or offer.available_on_date is None
            or not isfinite(offer.available_on_date)
            or offer.available_on_date < 0
        ):
            continue
        units = units_needed(Decimal(str(parsed.quantity)), size, product.allowed_lot_size)
        result.append(Candidate(product, offer, size, units, offer.available_on_date >= units))
    return result


def build_options(parsed: ParsedRequest, hits: list[ProductHit], offers: list[Offer]) -> list[OptionPreview]:
    items = candidates(parsed, hits, offers)
    full = [item for item in items if item.enough]
    results = []
    seen = set()

    def add(label, lines, total, quantity):
        signature = tuple(sorted((line.product_id, line.quantity_units) for line in lines))
        if signature in seen:
            return
        seen.add(signature)
        results.append(
            OptionPreview(
                option=OrderOption(label=label, lines=lines),
                total_eur=total,
                supplied_quantity=quantity,
                available=True,
            )
        )

    if full:
        preferred = {brand.casefold() for brand in parsed.criteria.prefer.brands}
        best = min(
            full,
            key=lambda c: (
                -(c.product.rrf_score or 0),
                -(bool(c.product.brand) and c.product.brand.casefold() in preferred),
                c.product.product_id,
            ),
        )
        cheapest = min(
            full,
            key=lambda c: (
                c.offer.unit_price_eur / c.size,
                c.total,
                c.product.product_id,
            ),
        )
        reliable = min(
            full,
            key=lambda c: (
                -(c.product.delivery_reliability if c.product.delivery_reliability is not None else -1),
                c.product.co2_kg_per_kg if c.product.co2_kg_per_kg is not None else float("inf"),
                c.product.product_id,
            ),
        )
        for label, item in (
            ("best_match", best),
            ("cheapest", cheapest),
            ("most_reliable", reliable),
        ):
            add(
                label,
                [OrderLineIn(product_id=item.product.product_id, quantity_units=item.units)],
                item.total,
                item.size * item.units,
            )
        return results

    # Deterministic bounded split, limited to the contract's 10 lines. Never
    # submit a knowingly incomplete proposal or claim unknown stock is available.
    remaining = Decimal(str(parsed.quantity))
    lines = []
    total = Decimal("0")
    supplied = Decimal("0")
    for item in sorted(items, key=lambda c: (c.offer.unit_price_eur / c.size, c.product.product_id)):
        lot = item.product.allowed_lot_size or 1
        capacity = int((Decimal(str(item.offer.available_on_date)) / lot).to_integral_value(rounding=ROUND_FLOOR)) * lot
        units = min(capacity, units_needed(remaining, item.size, lot))
        if units <= 0:
            continue
        lines.append(OrderLineIn(product_id=item.product.product_id, quantity_units=units))
        total += item.offer.unit_price_eur * units
        supplied += item.size * units
        remaining -= item.size * units
        if remaining <= 0:
            add("split_or_substitute", lines, total, supplied)
            break
        if len(lines) == 10:
            break
    return results
