-- 0005: demo mandate limits that match the challenge's example request.
-- "1,000 litres of lactose-free milk by Monday" costs about 1,200 EUR. With the old limits
-- (auto 150 / order 600 / week 2,000 EUR) the gateway blocked it; the challenge expects the agent to
-- order within its authority or send the order to a human. With auto 300 / order 3,000 / week 10,000:
--   small orders (< 300 EUR) are placed, the example is held for approval, > 3,000 EUR is blocked.
-- Signs a new mandate version for the demo agent; does nothing if its limits are already higher.
do $$
declare
  cur wholesaler.mandates;
begin
  select * into cur from wholesaler.mandates
  where agent_id = '00000000-0000-0000-0000-0000000000a1' and is_current;
  if found and cur.order_limit_eur < 3000 then
    update wholesaler.mandates set is_current = false where id = cur.id;
    insert into wholesaler.mandates
      (org_id, agent_id, version, auto_limit_eur, order_limit_eur, weekly_budget_eur,
       price_tolerance_pct, allow_substitution, allowed_category_codes, signed_by)
    values
      (cur.org_id, cur.agent_id, cur.version + 1, 300, 3000, 10000,
       cur.price_tolerance_pct, cur.allow_substitution, cur.allowed_category_codes, cur.signed_by);
  end if;
end $$;
