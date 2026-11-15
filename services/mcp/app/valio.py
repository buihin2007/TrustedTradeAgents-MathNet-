"""Valio customer-specific price and availability."""

import asyncio
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from zoneinfo import ZoneInfo

import httpx
from fastmcp.telemetry import get_tracer
from tta_contracts import Offer, ScheduleLine

HELSINKI = ZoneInfo("Europe/Helsinki")


class ValioUnavailable(Exception):
    """A reliable upstream response could not be obtained."""


def money(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid amount")
    return amount


def local_day(value: str) -> date:
    """A schedule date as a Helsinki calendar day; UTC timestamps of local midnight fall on the day before."""
    moment = datetime.fromisoformat(value)
    return (moment.astimezone(HELSINKI) if moment.tzinfo else moment).date()


def parse_offer(product_id: int, day: date, price: dict, stock: dict, now: datetime) -> Offer:
    catalog = price.get("catalogPrice")
    if not catalog or price.get("error") is not False or price.get("quantity") != 1:
        raise ValueError("Price missing")
    if catalog.get("currency") != "EUR" or type(stock["salesBlock"]) is not bool:
        raise ValueError("Invalid Valio response")
    schedule = []
    for line in stock["scheduleLines"]:
        amount = float(line["availableAmount"])
        if not isfinite(amount) or amount < 0 or type(line["salesBlock"]) is not bool:
            raise ValueError("Invalid schedule line")
        schedule.append(
            ScheduleLine(
                available_amount=amount,
                available_date=local_day(line["availableDate"]),
                sales_unit=line["salesUnit"],
                sales_block="blocked" if line["salesBlock"] else None,
            )
        )

    closing = stock.get("closingTime")
    cutoff = datetime.fromisoformat(closing) if closing else None
    if cutoff is not None and cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=HELSINKI)
    late = cutoff is not None and now >= cutoff
    blocked = stock["salesBlock"] or late
    # A schedule line says how much is available FROM its date: the latest line on or before the
    # delivery day applies (exact-date matching reported 0 for everything).
    current = max(
        (line for line in schedule if line.available_date <= day),
        key=lambda line: line.available_date,
        default=None,
    )
    available = current.available_amount if current is not None and current.sales_block is None else 0
    comparison = price.get("comparisonPrice")
    with_tax = price.get("priceWithTax")
    return Offer(
        product_id=product_id,
        unit_price_eur=money(catalog["value"]),
        unit_price_with_tax_eur=money(with_tax["value"] if with_tax else catalog["valueWithTax"]),
        comparison_price_eur=money(comparison["value"]) if comparison else None,
        comparison_unit=price.get("comparisonUnit"),
        logistic_model=stock.get("logisticModel"),
        available_on_date=0 if blocked else available,
        schedule=schedule,
        sales_block="blocked" if stock["salesBlock"] else "cutoff" if late else None,
        closing_time=cutoff,
    )


class ValioClient:
    def __init__(self, http: httpx.AsyncClient, base: str, key: str, customer: str):
        self.http = http
        self.base = base.rstrip("/")
        self.key = key
        self.customer = customer

    async def _fetch(self, endpoint: str, payload: dict) -> dict:
        with get_tracer().start_as_current_span(f"upstream.{endpoint}", record_exception=False):
            response = await self.http.post(
                f"{self.base}/api/{endpoint}",
                json=payload,
                headers={"X-API-Key": self.key},
            )
            response.raise_for_status()
            return response.json(parse_float=Decimal)

    async def offers(self, product_ids: list[int], day: date) -> list[Offer]:
        if not self.base or not self.key or not self.customer:
            raise ValioUnavailable("Valio credentials are missing")
        payload = {
            "customer": self.customer,
            "requestedDate": day.isoformat(),
            "products": [str(product_id) for product_id in product_ids],
        }
        try:
            async with asyncio.timeout(6):
                price_data, stock_data = await asyncio.gather(
                    self._fetch("price", payload),
                    self._fetch("availability", payload),
                )
            if price_data["currency"] != "EUR":
                raise ValueError("Unexpected currency")
            if any(
                datetime.fromisoformat(value).date() != day
                for value in (
                    price_data["pricingDate"],
                    stock_data["requestedDate"],
                )
            ):
                raise ValueError("Unexpected delivery date")
            prices = {int(item["productId"]): item for item in price_data["products"]}
            stock = {int(item["productId"]): item for item in stock_data["products"]}
        except (
            httpx.HTTPError,
            TimeoutError,
            ValueError,
            KeyError,
            AttributeError,
            TypeError,
        ) as exc:
            raise ValioUnavailable("Valio price or availability lookup failed") from exc
        now = datetime.now(HELSINKI)
        offers = []
        for product_id in product_ids:
            try:
                offers.append(
                    parse_offer(product_id, day, prices[product_id], stock[product_id], now)
                )
            except (ValueError, KeyError, AttributeError, TypeError):
                offers.append(
                    Offer(
                        product_id=product_id,
                        unit_price_eur=None,
                        unit_price_with_tax_eur=None,
                        comparison_price_eur=None,
                        comparison_unit=None,
                        logistic_model=None,
                        available_on_date=None,
                        error="Price or availability unavailable for this product",
                    )
                )
        return offers
