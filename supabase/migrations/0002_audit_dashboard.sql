-- 0002_audit_dashboard: append-only gateway log + read-only views for the Valio Aimo Grafana dashboard.
-- Run after 0001. Then, once, in the SQL editor (password is NOT stored in git):
--   alter role dashboard_ro with login password '<GRAFANA_DB_PASSWORD from .env>';

-- ---------------------------------------------------------------- append-only gateway log
create or replace function wholesaler.append_only() returns trigger language plpgsql as $$
begin
  raise exception '% is append-only', tg_table_name;
end $$;

create trigger gateway_events_append_only before update or delete on wholesaler.gateway_events
  for each row execute function wholesaler.append_only();

-- which option the agent preferred; needed to show the amount of a held order
alter table wholesaler.orders add column if not exists preferred_index int not null default 0;

-- ---------------------------------------------------------------- views the dashboard reads
create or replace view wholesaler.v_audit_gateway as
select g.id, g.ts, g.tool, g.actor_kind, g.actor_id, a.name as agent_name, g.org_id, g.intent_id,
       g.decision, g.reasons, g.jev_scores,
       (select min(v::float8) from jsonb_each_text(coalesce(g.jev_scores, '{}')) as j(k, v)) as jev_min,
       g.sql, g.row_count, g.latency_ms, g.trace_id, g.span_id
from wholesaler.gateway_events g
left join wholesaler.agents a on g.actor_kind = 'agent' and a.id::text = g.actor_id;
comment on view wholesaler.v_audit_gateway is 'One row per MCP tool call, with agent name and lowest Jev score. Dashboard source.';

create or replace view wholesaler.v_audit_orders as
select o.id as order_id, o.org_id, org.name as org_name, o.agent_id, a.name as agent_name, o.intent_id,
       o.status, o.reasons,
       coalesce(o.total_eur, (o.options -> o.preferred_index ->> 'total_eur')::numeric) as amount_eur,
       jsonb_array_length(o.options) as option_count,
       o.delivery_date, o.created_at, o.decided_at, o.decided_by,
       extract(epoch from (o.decided_at - o.created_at))::int as seconds_to_decision,
       o.trace_id
from wholesaler.orders o
join wholesaler.orgs org on org.id = o.org_id
join wholesaler.agents a on a.id = o.agent_id;
comment on view wholesaler.v_audit_orders is 'Orders with the amount to show (chosen total, or preferred option for held/blocked).';

create or replace view wholesaler.v_audit_intents as
select i.id as intent_id, i.org_id, i.request_text, i.created_at, i.expires_at,
       cardinality(i.touched_product_ids) as distinct_products
from wholesaler.intents i;
comment on view wholesaler.v_audit_intents is 'Intents with how many distinct products the agent has touched (cap 200).';

create or replace view wholesaler.v_audit_agents as
select a.id as agent_id, a.org_id, a.name, a.status, a.created_at,
       m.version as mandate_version, m.auto_limit_eur, m.order_limit_eur, m.weekly_budget_eur, m.signed_at
from wholesaler.agents a
left join wholesaler.mandates m on m.agent_id = a.id and m.is_current;
comment on view wholesaler.v_audit_agents is 'Agents with their current mandate. Never exposes key_hash.';

-- ---------------------------------------------------------------- read-only role for Grafana
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'dashboard_ro') then
    create role dashboard_ro nologin;
  end if;
end $$;
grant usage on schema wholesaler to dashboard_ro;
grant select on wholesaler.v_audit_gateway, wholesaler.v_audit_orders,
                wholesaler.v_audit_intents, wholesaler.v_audit_agents to dashboard_ro;
alter role dashboard_ro set statement_timeout = '5s';
alter role dashboard_ro set default_transaction_read_only = on;
