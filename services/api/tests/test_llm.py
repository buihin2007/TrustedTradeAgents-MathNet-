import json

import httpx
import pytest
from tta_contracts import Facets

from app.config import Settings
from app.llm import LLMUnavailable, OpenRouter
from app.options import build_options
from tests.factories import hit, offer, parsed

FACETS = Facets(claims=[], allergens=[], categories=[], temperatures=[], sales_units=[])


async def test_parse_uses_structured_output_and_validates_missing_fields():
    captured = []
    value = parsed().model_dump(mode="json")
    value["quantity"] = None

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenRouter(Settings(openrouter_api_key="secret", llm_model="test-model"), client)
        result = await llm.parse("milk", [], FACETS)
    assert result.quantity is None
    assert captured[0]["response_format"]["type"] == "json_schema"
    assert "tools" not in captured[0]


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        '{"unexpected": "field"}',
        json.dumps(
            {
                **parsed().model_dump(mode="json"),
                "criteria": {
                    "query_text": "milk",
                    "must": {"claims_all": ["INVENTED"]},
                },
            }
        ),
    ],
)
async def test_invalid_model_output_never_becomes_a_purchase(content):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": content}}]},
            )
        )
    ) as client:
        llm = OpenRouter(Settings(openrouter_api_key="secret", llm_model="test-model"), client)
        with pytest.raises(LLMUnavailable):
            await llm.parse("milk", [], FACETS)


async def test_upstream_error_is_sanitized_and_explanation_has_numeric_fallback():
    options = build_options(parsed(), [hit()], [offer()])
    before = [o.model_dump_json() for o in options]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                500,
                text="secret credential in upstream response",
            )
        )
    ) as client:
        llm = OpenRouter(Settings(openrouter_api_key="secret", llm_model="test-model"), client)
        with pytest.raises(LLMUnavailable) as error:
            await llm.parse("milk", [], FACETS)
        assert "secret" not in str(error.value)
        explanation = await llm.explain(options)
    assert "48.00" in explanation[0]
    assert [o.model_dump_json() for o in options] == before
