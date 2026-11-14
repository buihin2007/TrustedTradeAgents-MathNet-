import json

import httpx
import pytest
from tta_contracts import ReasonCode

from app.openrouter import OpenRouter, jev_reason


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.1, ReasonCode.JEV_BLOCK),
        (0.1001, ReasonCode.JEV_UNSURE),
        (0.899, ReasonCode.JEV_UNSURE),
        (0.9, None),
    ],
)
def test_thresholds(probability, expected):
    assert jev_reason({"serves_intent": probability, "matches_request": 0.98}) == expected


async def test_jev_checks_payload_and_requires_all_answers():
    calls = 0

    def response(request):
        nonlocal calls
        assert request.url.path == "/api/alpha/decisions"
        payload = json.loads(request.content)
        assert payload["state"] == {"test": "state"}
        assert set(payload["questions"]) == {"serves_intent", "matches_request", "nothing_extra"}
        answers = {name: {"type": "noul", "noul": 0.95} for name in payload["questions"]}
        calls += 1
        if calls > 1:
            answers.pop("nothing_extra")
        return httpx.Response(200, json={"answers": answers})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        router = OpenRouter(http, "test", "model", "", "")
        assert jev_reason(await router.judge("place_order", {"test": "state"})) is None
        with pytest.raises(KeyError):
            await router.judge("place_order", {"test": "state"})
