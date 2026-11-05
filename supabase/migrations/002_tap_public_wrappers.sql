-- ============================================================================
-- Talent Agent Plus — public RPC wrappers for the tap functions
-- ============================================================================
-- Apply AFTER 001_tap_core.sql, in the same project.
--
-- Why this file exists: PostgREST (the Supabase API) serves the `public`
-- schema by default. The tap.* security-definer functions live in the
-- `tap` schema, so the Python server cannot call them over HTTP until
-- they are wrapped here in `public` — the same pattern as
-- Jays-Graphic-Arts/brick1/supabase/migrations/002_intake_and_command_api.sql.
--
-- Lockdown, same as the brick1 pattern: every wrapper is revoked from
-- public, anon, and authenticated, and granted to service_role ONLY.
-- The Python server holds the service-role key and is the only caller.
-- Per-person isolation stays enforced in the app layer.
--
-- Also added here: public.tap_verify_evidence_chain(), mirroring
-- brick1's verifier, so the server can re-verify the whole tap ledger
-- chain in the database and report it on /api/store/verify.
--
-- No secrets in this file.
-- ============================================================================

create or replace function public.tap_claim_trial(p_email text, p_artist_id uuid)
returns void
language sql
security definer
set search_path = tap, public
as $$
  select tap.claim_trial(p_email, p_artist_id);
$$;

create or replace function public.tap_record_cleared_payment(
  p_artist_id uuid,
  p_booking_id uuid,
  p_design_order_id uuid,
  p_stripe_object_id text,
  p_stage tap.cleared_stage,
  p_amount_cents bigint,
  p_actor text,
  p_correlation_id uuid default gen_random_uuid()
) returns uuid
language sql
security definer
set search_path = tap, public
as $$
  select tap.record_cleared_payment(p_artist_id, p_booking_id, p_design_order_id,
    p_stripe_object_id, p_stage, p_amount_cents, p_actor, p_correlation_id);
$$;

create or replace function public.tap_transition_booking(
  p_booking_id uuid,
  p_to tap.booking_state,
  p_actor text,
  p_correlation_id uuid default gen_random_uuid()
) returns tap.bookings
language sql
security definer
set search_path = tap, public
as $$
  select tap.transition_booking(p_booking_id, p_to, p_actor, p_correlation_id);
$$;

create or replace function public.tap_append_evidence(
  p_correlation_id uuid,
  p_artist_id uuid,
  p_event_type text,
  p_payload jsonb
) returns text
language sql
security definer
set search_path = tap, public
as $$
  select tap.append_evidence(p_correlation_id, p_artist_id, p_event_type, p_payload);
$$;

create or replace function public.tap_verify_evidence_chain()
returns boolean
language plpgsql
security definer
set search_path = tap, public
as $$
declare
  r record;
  expected_prev text := null;
  expected_hash text;
begin
  for r in select * from tap.ledger_events order by seq loop
    if r.predecessor_hash is distinct from expected_prev then return false; end if;
    expected_hash := encode(digest(convert_to(coalesce(expected_prev,'GENESIS') || '|' || r.correlation_id::text || '|' || coalesce(r.artist_id::text,'') || '|' || r.event_type || '|' || r.payload_hash,'utf8'),'sha256'),'hex');
    if expected_hash <> r.entry_hash then return false; end if;
    expected_prev := r.entry_hash;
  end loop;
  return true;
end;
$$;

revoke all on function public.tap_claim_trial(text, uuid) from public, anon, authenticated;
revoke all on function public.tap_record_cleared_payment(uuid, uuid, uuid, text, tap.cleared_stage, bigint, text, uuid) from public, anon, authenticated;
revoke all on function public.tap_transition_booking(uuid, tap.booking_state, text, uuid) from public, anon, authenticated;
revoke all on function public.tap_append_evidence(uuid, uuid, text, jsonb) from public, anon, authenticated;
revoke all on function public.tap_verify_evidence_chain() from public, anon, authenticated;

grant execute on function public.tap_claim_trial(text, uuid) to service_role;
grant execute on function public.tap_record_cleared_payment(uuid, uuid, uuid, text, tap.cleared_stage, bigint, text, uuid) to service_role;
grant execute on function public.tap_transition_booking(uuid, tap.booking_state, text, uuid) to service_role;
grant execute on function public.tap_append_evidence(uuid, uuid, text, jsonb) to service_role;
grant execute on function public.tap_verify_evidence_chain() to service_role;
