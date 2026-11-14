"""HTTP and database integration tests, using a disposable database."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from jwt.algorithms import ECAlgorithm
from psycopg import AsyncConnection, sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from tta_contracts import Offer

from app.auth import SupabaseJWT
from app.openrouter import OpenRouter
from app.server import Runtime, create_server
from search.catalog import Catalog

ROOT = Path(__file__).resolve().parents[3]
DSN = os.getenv("MCP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DSN, reason="Set MCP_TEST_DATABASE_URL to an empty test database"
)
ORG = "00000000-0000-0000-0000-00000000000a"
AGENT = "00000000-0000-0000-0000-0000000000a1"
USER = "40000000-0000-0000-0000-000000000001"
OUTSIDER = "40000000-0000-0000-0000-000000000002"
KEY = "tta_integration-test"
DAY = date.today() + timedelta(days=7)


class Pricing:
    price = Decimal("1.20")
    stock = 1000

    async def offers(self, ids, day):
        assert day == DAY
        return [
            Offer(
                product_id=product_id,
                unit_price_eur=self.price,
                unit_price_with_tax_eur=None,
                comparison_price_eur=None,
                comparison_unit=None,
                logistic_model="Stock",
                available_on_date=self.stock,
            )
            for product_id in ids
        ]


class Judge(OpenRouter):
    probability = 0.95
    fail = False
    searches = 0

    async def judge(self, tool, state):
        assert state["original_request"]
        if self.fail:
            raise TimeoutError
        assert state["previous_calls"] >= 0
        return {"serves_intent": self.probability}

    async def embed(self, query):
        self.searches += 1


class LocalTransport(StreamableHttpTransport):
    """Use the actual MCP HTTP stack and Bearer requests via an in-process ASGI transport."""

    def __init__(self, app, token):
        def client_factory(headers=None, timeout=None, auth=None, **kwargs):
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                headers=headers,
                timeout=timeout,
                auth=auth,
                **kwargs,
            )

        super().__init__(
            "http://localhost/mcp",
            headers={"Authorization": f"Bearer {token}"},
            httpx_client_factory=client_factory,
        )


@asynccontextmanager
async def disposable_db():
    name = "tta_mcp_test_" + uuid4().hex
    async with await AsyncConnection.connect(DSN, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield make_conninfo(DSN, dbname=name)
        finally:
            await admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@asynccontextmanager
async def gateway():
    async with (
        disposable_db() as test_dsn,
        await AsyncConnection.connect(test_dsn, autocommit=True) as setup,
    ):
        await setup.execute("create schema if not exists extensions")
        for migration in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
            text = migration.read_text(encoding="utf-8")
            # The Windows test server may lack pgvector; opt in to validating the full migration
            # by default. A plain Postgres run still exercises actual FTS, SQL guards and transactions.
            if migration.name.startswith("0001") and os.getenv("MCP_TEST_FTS_ONLY"):
                text = text.replace(
                    "create extension if not exists vector with schema extensions;", ""
                )
                text = text.replace("extensions.vector(1536)", "float8[]")
                text = text.replace("p.embedding <=> q_emb::vector", "0::float8")
            await setup.execute(text)
        seed = (ROOT / "supabase" / "seed.sql").read_text(encoding="utf-8")
        await setup.execute(
            seed.replace(":'user_id'", f"'{USER}'").replace(":'agent_key'", f"'{KEY}'")
        )
        await setup.execute("""
            insert into wholesaler.orgs (id, name) values ('00000000-0000-0000-0000-00000000000b', 'Other');
            insert into wholesaler.org_members (org_id, user_id)
              values ('00000000-0000-0000-0000-00000000000b', '40000000-0000-0000-0000-000000000002');
            insert into wholesaler.code_labels (kind, code, label) values
              ('claim', 'FREE_FROM_LACTOSE', 'Lactose free'), ('category', '10400', 'Milk');
            insert into wholesaler.products
              (id, name_en, sales_unit, allowed_lot_size, category_code, claims, unit_volume_l, gtin, search_text)
            values (1, 'Lactose-free milk', 'ST', 1, '10400', '{FREE_FROM_LACTOSE}', 1, '12345678', 'milk maito'),
                   (2, 'Ordinary milk', 'ST', 1, '10400', '{}', 1, null, 'milk maito'),
                   (3, 'Alternative milk', 'ST', 1, '10400', '{FREE_FROM_LACTOSE}', 1, null, 'milk maito');
            insert into wholesaler.product_substitutions values (1, 2, 1), (1, 3, 2)
            """)
        private = ec.generate_private_key(ec.SECP256R1())
        verifier = SupabaseJWT("https://project.test", "", None)
        key = jwt.PyJWK.from_dict(ECAlgorithm.to_jwk(private.public_key(), as_dict=True))
        verifier.jwks.get_signing_key_from_jwt = lambda _: key

        def token(user):
            return jwt.encode(
                {
                    "iss": "https://project.test/auth/v1",
                    "aud": "authenticated",
                    "role": "authenticated",
                    "sub": user,
                    "exp": 4102444800,
                },
                private,
                algorithm="ES256",
            )

        judge = Judge(None, "", "", "", "")
        pricing = Pricing()

        @asynccontextmanager
        async def runtime():
            async with AsyncConnectionPool(
                test_dsn,
                min_size=0,
                max_size=2,
                open=False,
                kwargs={"row_factory": dict_row},
            ) as pool:
                yield Runtime(pool, verifier, judge, pricing, Catalog(judge))

        server = create_server(runtime)
        app = server.http_app(path="/mcp", stateless_http=True)
        async with (
            app.router.lifespan_context(app),
            Client(LocalTransport(app, KEY)) as agent,
            Client(LocalTransport(app, token(USER))) as user,
            Client(LocalTransport(app, token(OUTSIDER))) as outsider,
        ):
            yield agent, user, outsider, pricing, judge, setup


def intent_request(quantity=300):
    return {
        "request_text": f"{quantity} litres of lactose-free milk",
        "criteria": {
            "query_text": "milk",
            "query_text_alt": "maito",
            "must": {"claims_all": ["FREE_FROM_LACTOSE"]},
        },
        "quantity": quantity,
        "quantity_unit": "l",
        "delivery_date": DAY.isoformat(),
    }


async def call(client, name, request=None, **kwargs):
    args = {"request": request, **kwargs} if request is not None else kwargs
    return (await client.call_tool(name, args)).data


async def prepare(agent, user, quantity=300, *, key="order-key-123"):
    intent = await call(user, "register_intent", intent_request(quantity))
    quote = await call(
        agent,
        "get_price_availability",
        {
            "intent_id": intent.intent_id,
            "delivery_date": DAY.isoformat(),
            "product_ids": [1],
        },
    )
    return intent, {
        "intent_id": intent.intent_id,
        "quote_id": quote.quote_id,
        "delivery_date": DAY.isoformat(),
        "options": [
            {"label": "best_match", "lines": [{"product_id": 1, "quantity_units": quantity}]}
        ],
        "idempotency_key": key,
    }


async def test_buyer_agent_flow():
    async with gateway() as clients:
        agent, user, outsider, _, judge, db = clients
        assert len(await agent.list_tools()) == 13
        intent, order_request = await prepare(agent, user)
        facets = await call(agent, "get_facets")
        assert facets.claims[0].code == "FREE_FROM_LACTOSE"
        rank = await call(
            agent,
            "search_products",
            {
                "intent_id": intent.intent_id,
                "criteria": intent_request()["criteria"],
            },
        )
        assert {hit.product_id for hit in rank.hits} == {1, 3}
        gtin = await call(
            agent,
            "search_products",
            {
                "intent_id": intent.intent_id,
                "criteria": {"query_text": "12345678"},
            },
        )
        assert gtin.sql_source == "gtin"
        assert [hit.product_id for hit in gtin.hits] == [1]
        assert judge.searches == 1
        substitutes = await call(
            agent,
            "get_substitutes",
            {
                "intent_id": intent.intent_id,
                "product_ids": [1],
            },
        )
        assert [hit.product_id for hit in substitutes[0].substitutes] == [3]
        first, retry = await asyncio.gather(
            *(call(agent, "place_order", order_request) for _ in range(2))
        )
        assert first.status == retry.status == "held"
        assert first.order_id == retry.order_id
        assert first.reasons == ["OVER_AUTO_LIMIT"]
        changed = {
            **order_request,
            "options": [{"label": "cheapest", "lines": [{"product_id": 1, "quantity_units": 1}]}],
        }
        with pytest.raises(ToolError, match="Idempotency key"):
            await call(agent, "place_order", changed)
        with pytest.raises(ToolError, match="FORBIDDEN"):
            await call(outsider, "get_order", order_id=first.order_id)
        with pytest.raises(ToolError, match="FORBIDDEN"):
            await call(agent, "approve_order", order_id=first.order_id, option_index=0)
        placed = await call(user, "approve_order", order_id=first.order_id, option_index=0)
        assert placed.status == "placed"
        assert Decimal(placed.total_eur) == 360
        rows = await (
            await db.execute(
                "select quantity_units from wholesaler.order_lines where order_id = %s",
                (UUID(first.order_id),),
            )
        ).fetchall()
        assert rows == [(300,)]
        assert (await call(outsider, "list_gateway_events", limit=2))[0].reasons == ["FORBIDDEN"]
        events = await call(user, "list_gateway_events", limit=3)
        assert len(events) == 3
        next_events = await call(user, "list_gateway_events", limit=3, before_id=events[-1].id)
        assert all(event.id < events[-1].id for event in next_events)


async def test_policy_refresh_and_pause():
    async with gateway() as clients:
        agent, user, _, pricing, judge, _ = clients
        _, order_request = await prepare(agent, user, 40)
        judge.fail = True
        held = await call(agent, "place_order", order_request)
        assert held.status == "held" and held.reasons == ["JEV_UNAVAILABLE"]
        pricing.price = Decimal("1.40")
        request = {"order_id": held.order_id, "option_index": 0}
        review = await call(user, "approve_order", **request)
        assert review.status == "held" and review.reasons == ["PRICE_MOVED"]
        assert Decimal(review.options[0].total_eur) == 56
        assert (await call(user, "approve_order", **request)).status == "held"
        pricing.stock = 39
        review = await call(user, "approve_order", **request)
        assert review.status == "held" and review.reasons == ["PARTIAL_AVAILABILITY", "PRICE_MOVED"]
        pricing.stock = 40
        assert (
            await call(user, "approve_order", **request, expected_total_eur="56.00")
        ).status == "placed"
        update = await call(user, "update_mandate", agent_id=AGENT, order_limit_eur="450")
        assert update.version == 2
        assert (await call(user, "get_mandate", agent_id=AGENT)).version == 2
        await call(user, "set_agent_status", {"agent_id": AGENT, "status": "paused"})
        with pytest.raises(ToolError, match="AGENT_PAUSED"):
            await call(agent, "get_facets")
        await call(user, "set_agent_status", {"agent_id": AGENT, "status": "active"})
        assert await call(agent, "get_facets")


async def test_caps_rate_limits_and_jev_block():
    async with gateway() as clients:
        agent, user, _, _, judge, db = clients
        intent, order_request = await prepare(agent, user, 40)
        quote_args = {
            "intent_id": intent.intent_id,
            "delivery_date": DAY.isoformat(),
            "product_ids": [1],
        }
        with pytest.raises(ToolError, match="CAP_EXCEEDED"):
            await call(agent, "get_price_availability", {**quote_args, "product_ids": [1] * 51})
        await db.execute(
            """
            update wholesaler.intents
            set touched_product_ids = array(select generate_series(10, 209))
            where id = %s
            """,
            (UUID(intent.intent_id),),
        )
        with pytest.raises(ToolError, match="CAP_EXCEEDED"):
            await call(agent, "get_price_availability", quote_args)
        await db.execute(
            "update wholesaler.intents set touched_product_ids = '{}' where id = %s",
            (UUID(intent.intent_id),),
        )
        judge.probability = 0.1
        with pytest.raises(ToolError, match="JEV_BLOCK"):
            await call(agent, "get_price_availability", quote_args)
        blocked = await call(agent, "place_order", order_request)
        assert blocked.status == "blocked" and blocked.reasons == ["JEV_BLOCK"]
        await db.execute(
            """
            insert into wholesaler.gateway_events (tool, actor_kind, actor_id, org_id, decision, latency_ms)
            select 'get_facets', 'agent', %s, %s, 'allow', 0 from generate_series(1, 60)
            """,
            (AGENT, UUID(ORG)),
        )
        with pytest.raises(ToolError, match="RATE_LIMIT"):
            await call(agent, "get_facets")
        assert (await call(user, "list_gateway_events", limit=1))[0].reasons == ["RATE_LIMIT"]


async def test_concurrent_orders_respect_org_budget_and_can_reject_held():
    async with gateway() as clients:
        agent, user, _, _, judge, _ = clients
        await call(
            user,
            "update_mandate",
            agent_id=AGENT,
            auto_limit_eur="50",
            order_limit_eur="60",
            weekly_budget_eur="75",
        )
        _, first = await prepare(agent, user, 40, key="first-order")
        _, second = await prepare(agent, user, 40, key="second-order")
        orders = await asyncio.gather(
            call(agent, "place_order", first),
            call(agent, "place_order", second),
        )
        assert sorted(order.status for order in orders) == ["blocked", "placed"]
        assert next(order for order in orders if order.status == "blocked").reasons == [
            "OVER_WEEKLY_BUDGET"
        ]
        judge.probability = 0.5
        _, third = await prepare(agent, user, 10, key="third-order")
        held = await call(agent, "place_order", third)
        assert held.status == "held" and held.reasons == ["JEV_UNSURE"]
        rejected = await call(
            user, "reject_order", {"order_id": held.order_id, "note": "Cancelled"}
        )
        assert rejected.status == "rejected"


@pytest.mark.skipif(os.getenv("MCP_TEST_FTS_ONLY"), reason="Requires the real pgvector extension")
async def test_semantic_match_with_no_full_text_overlap(monkeypatch):
    async with gateway() as clients:
        agent, user, _, _, judge, db = clients
        vector = "[" + ",".join(["1"] + ["0"] * 1023) + "]"

        async def embed(_):
            return vector

        monkeypatch.setattr(judge, "embed", embed)
        await db.execute("update wholesaler.products set embedding = %s where id = 1", (vector,))
        intent = await call(user, "register_intent", intent_request(40))
        result = await call(
            agent,
            "search_products",
            {
                "intent_id": intent.intent_id,
                "criteria": {"query_text": "nolexicalmatch"},
            },
        )
        assert [product.product_id for product in result.hits] == [1]
        assert result.hits[0].rrf_score == pytest.approx(1 / 51)
