import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from app.valio import ValioClient, parse_offer

DAY = date(2099, 1, 1)
PRICE = {
    "productId": "1",
    "error": False,
    "quantity": 1,
    "catalogPrice": {"value": 1.20, "valueWithTax": 1.34, "currency": "EUR"},
}
STOCK = {
    "productId": "1",
    "salesBlock": False,
    "logisticModel": "Stock",
    "scheduleLines": [
        {
            "availableAmount": 40,
            "availableDate": "2099-01-01",
            "salesUnit": "ST",
            "salesBlock": False,
        }
    ],
}


async def test_valio_batches_and_normalizes():
    def response(request):
        assert request.headers["X-API-Key"] == "test"
        assert json.loads(request.content) == {
            "customer": "c",
            "requestedDate": DAY.isoformat(),
            "products": ["1", "2"],
        }
        if request.url.path == "/api/price":
            return httpx.Response(
                200,
                json={
                    "currency": "EUR",
                    "pricingDate": DAY.isoformat(),
                    "products": [PRICE],
                },
            )
        assert request.url.path == "/api/availability"
        return httpx.Response(200, json={"requestedDate": DAY.isoformat(), "products": [STOCK]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        first, second = await ValioClient(http, "https://valio.test", "test", "c").offers(
            [1, 2],
            DAY,
        )
    assert first.unit_price_eur == Decimal("1.20")
    assert first.available_on_date == 40
    assert second.error
    assert second.unit_price_eur is None


def test_past_cutoff_and_wrong_date_are_unavailable():
    now = datetime(2098, 1, 1, tzinfo=UTC)
    assert (
        parse_offer(1, DAY, PRICE, {**STOCK, "closingTime": "2097-01-01"}, now).available_on_date
        == 0
    )
    assert parse_offer(1, date(2099, 1, 2), PRICE, STOCK, now).available_on_date == 0
