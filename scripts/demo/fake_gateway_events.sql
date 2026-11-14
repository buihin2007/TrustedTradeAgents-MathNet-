-- Fake gateway traffic so the dashboard has something to show before the Gate is done.
-- Run after seed. Delete with: truncate wholesaler.gateway_events;  (trigger blocks DELETE, not TRUNCATE)
insert into wholesaler.gateway_events
  (ts, tool, actor_kind, actor_id, org_id, intent_id, decision, reasons, jev_scores, row_count, latency_ms, trace_id)
select now() - (n || ' minutes')::interval * random() * 3,
       (array['search_products','get_price_availability','place_order','get_facets'])[1 + (n % 4)],
       'agent', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000000a', null,
       case when n % 11 = 0 then 'block' when n % 7 = 0 then 'ask_human' else 'allow' end,
       case when n % 11 = 0 then array['CAP_EXCEEDED']
            when n % 7 = 0 then array['OVER_AUTO_LIMIT'] else '{}'::text[] end,
       jsonb_build_object('serves_intent', round((0.6 + random() * 0.4)::numeric, 2)),
       (random() * 20)::int, 50 + (random() * 900)::int, md5(n::text)
from generate_series(1, 300) n;
