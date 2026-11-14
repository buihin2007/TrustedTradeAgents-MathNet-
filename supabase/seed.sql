-- Demo seed. Run AFTER 0001_init.sql and AFTER creating the demo user in Supabase
-- (Dashboard -> Authentication -> Add user, e.g. manager@demo-hotel.example).
-- Replace the two placeholders before running:
--   :'user_id'   -> the demo user's UUID from the dashboard
--   :'agent_key' -> the same value as AGENT_KEY in .env
-- psql: psql "$DATABASE_URL" -v user_id=<uuid> -v agent_key=<key> -f supabase/seed.sql

insert into wholesaler.orgs (id, name)
values ('00000000-0000-0000-0000-00000000000a', 'Demo Hotel Group')
on conflict (id) do nothing;

insert into wholesaler.agents (id, org_id, name, key_hash)
values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000000a',
        'Demo purchasing agent', encode(sha256(convert_to(:'agent_key', 'UTF8')), 'hex'))
on conflict (id) do update set key_hash = excluded.key_hash;

insert into wholesaler.org_members (org_id, user_id, role)
values ('00000000-0000-0000-0000-00000000000a', :'user_id'::uuid, 'signer')
on conflict do nothing;

insert into wholesaler.mandates (org_id, agent_id, version, signed_by)
values ('00000000-0000-0000-0000-00000000000a', '00000000-0000-0000-0000-0000000000a1', 1, :'user_id'::uuid)
on conflict (agent_id, version) do nothing;

insert into buyer.profiles (user_id, org_id, agent_id, display_name)
values (:'user_id'::uuid, '00000000-0000-0000-0000-00000000000a', '00000000-0000-0000-0000-0000000000a1', 'F&B manager')
on conflict (user_id) do nothing;
