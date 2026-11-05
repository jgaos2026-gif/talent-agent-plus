-- ============================================================================
-- Talent Agent Plus — Supabase core schema (schema: tap)
-- ============================================================================
-- What this is: the Postgres persistence for the Talent Agent Plus Render
-- app, replacing its in-memory store. Apply with the Supabase SQL editor
-- or `supabase db push`. No secrets live in this file.
--
-- Keys (never in the repo, never in the browser):
--   SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY go in Render env vars.
--   The Python server holds the service key and is the ONLY database
--   client. Direct anon/authenticated access is revoked below, following
--   the brick1 pattern in Jays-Graphic-Arts/brick1/supabase/migrations/
--   001_brick1_operational.sql. Per-person isolation is enforced in the
--   app layer (each actor may act only for their own artist id; the
--   owner may act for any artist), exactly as the engine does today.
--   Phase 2 (optional, later): migrate sign-in to Supabase Auth and
--   replace the revoke-all posture with auth.uid() RLS policies.
--
-- Money rules enforced here, not just in the app:
--   * 11VT split: V1 15, V2 7.5, V3 3.5, V4 5, V5 2, V6 15, V7 residual,
--     V8 20, V9 10, V10 8, V11 2 (percent). V7 takes the remainder so the
--     split always sums exactly to the cleared amount.
--   * Cleared payments are idempotent on the Stripe object id: recording
--     the same Stripe payment twice returns the first row, never a double.
--   * Trial is one time per email, ever: tap.claim_trial() raises
--     'trial_already_claimed' on a second claim for the same email.
--   * Booking state machine is enforced by tap.transition_booking().
--   * Every money/state change appends to the hash-chained ledger.
-- Stripe stays TEST MODE until the owner finishes Dashboard verification.
-- ============================================================================

create extension if not exists pgcrypto;

create schema if not exists tap;

create type tap.tier as enum ('try', 'starter', 'pro', 'full_label');
create type tap.booking_state as enum ('DRAFT', 'OFFERED', 'APPROVED', 'CONFIRMED', 'COMPLETED', 'CANCELLED', 'REJECTED');
create type tap.opportunity_kind as enum ('gig', 'audition', 'casting', 'venue', 'producer', 'collaborator', 'theatre', 'dance', 'film', 'voice_acting', 'commercial');
create type tap.expense_category as enum ('travel', 'taxes', 'advertising', 'pay', 'wardrobe', 'cds', 'online');
create type tap.cleared_stage as enum ('gig-pay-cleared', 'merch-cleared', 'payout-cleared', 'stripe-cleared');
create type tap.design_order_state as enum ('REQUESTED', 'IN_DESIGN', 'PROOF_SENT', 'PROOF_APPROVED', 'DELIVERED', 'CANCELLED');

create table if not exists tap.artists (
  id uuid primary key default gen_random_uuid(),
  auth_user_id uuid unique,
  email text not null check (length(email) between 3 and 320),
  showcase_name text,
  skills text[] not null default '{}',
  location text,
  bio text,
  tier tap.tier not null default 'try',
  show_in_discover boolean not null default false,
  created_at timestamptz not null default now()
);
create unique index if not exists artists_email_ci on tap.artists (lower(email));

-- One-time-ever trial claim. A second claim for the same email is refused
-- by tap.claim_trial(); the row itself is the permanent record.
create table if not exists tap.trials (
  email text primary key check (length(email) between 3 and 320),
  artist_id uuid not null references tap.artists (id),
  claimed_at timestamptz not null default now()
);

create table if not exists tap.bookings (
  id uuid primary key default gen_random_uuid(),
  artist_id uuid not null references tap.artists (id),
  kind tap.opportunity_kind not null,
  state tap.booking_state not null default 'DRAFT',
  amount_cents bigint not null check (amount_cents > 0),
  cleared_stage tap.cleared_stage,
  cleared_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists tap.design_orders (
  id uuid primary key default gen_random_uuid(),
  artist_id uuid not null references tap.artists (id),
  service text not null check (length(service) between 1 and 120),
  brief text not null check (length(brief) between 1 and 12000),
  state tap.design_order_state not null default 'REQUESTED',
  price_cents bigint check (price_cents is null or price_cents > 0),
  paid_cents bigint not null default 0 check (paid_cents >= 0),
  proof_approved boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists tap.payments (
  id uuid primary key default gen_random_uuid(),
  artist_id uuid not null references tap.artists (id),
  booking_id uuid references tap.bookings (id),
  design_order_id uuid references tap.design_orders (id),
  stripe_object_id text not null unique,
  stage tap.cleared_stage not null,
  amount_cents bigint not null check (amount_cents > 0),
  currency text not null default 'USD',
  received_at timestamptz not null default now()
);

-- One row per 11VT bucket per cleared payment. V7 is the residual plug.
create table if not exists tap.allocations (
  id uuid primary key default gen_random_uuid(),
  payment_id uuid not null references tap.payments (id),
  bucket text not null check (bucket in ('V1','V2','V3','V4','V5','V6','V7','V8','V9','V10','V11')),
  basis_points integer not null check (basis_points between 0 and 10000),
  amount_cents bigint not null check (amount_cents >= 0),
  unique (payment_id, bucket)
);

create table if not exists tap.ava_threads (
  id uuid primary key default gen_random_uuid(),
  artist_id uuid not null references tap.artists (id),
  kind text not null default 'chat',
  created_at timestamptz not null default now()
);

create table if not exists tap.ava_messages (
  id uuid primary key default gen_random_uuid(),
  thread_id uuid not null references tap.ava_threads (id),
  role text not null check (role in ('artist', 'ava', 'owner')),
  body text not null check (length(body) between 1 and 4000),
  created_at timestamptz not null default now()
);

-- AVA braided memory: hash-linked links, written only through the BCT gate.
create table if not exists tap.ava_memories (
  id uuid primary key default gen_random_uuid(),
  artist_id uuid not null references tap.artists (id),
  link_type text not null check (link_type in ('fact', 'correction', 'decision', 'preference')),
  body text not null,
  link_hash text not null,
  prev_hash text,
  created_at timestamptz not null default now()
);

create table if not exists tap.referrals (
  id uuid primary key default gen_random_uuid(),
  referrer_artist_id uuid not null references tap.artists (id),
  referred_email text not null check (length(referred_email) between 3 and 320),
  referred_artist_id uuid references tap.artists (id),
  first_payment_at timestamptz,
  free_month_granted boolean not null default false,
  created_at timestamptz not null default now()
);

-- No OAuth tokens are stored here. Tokens live in Supabase Vault or the
-- server-side encrypted store; this table records only the connection.
create table if not exists tap.social_accounts (
  id uuid primary key default gen_random_uuid(),
  artist_id uuid not null references tap.artists (id),
  provider text not null check (provider in ('facebook', 'instagram', 'threads')),
  external_account_id text not null,
  connected_at timestamptz not null default now(),
  unique (artist_id, provider)
);

create table if not exists tap.addon_purchases (
  id uuid primary key default gen_random_uuid(),
  artist_id uuid not null references tap.artists (id),
  addon text not null check (addon in ('extra_post', 'extra_video', 'extra_campaign', 'extra_release_prep')),
  amount_cents bigint not null check (amount_cents > 0),
  stripe_object_id text,
  created_at timestamptz not null default now()
);

create table if not exists tap.ledger_events (
  seq bigint generated always as identity primary key,
  correlation_id uuid not null,
  artist_id uuid,
  event_type text not null,
  payload_hash text not null,
  predecessor_hash text,
  entry_hash text not null unique,
  created_at timestamptz not null default now()
);

-- Hash-chained evidence append (same construction as brick1.append_evidence).
create or replace function tap.append_evidence(
  p_correlation_id uuid,
  p_artist_id uuid,
  p_event_type text,
  p_payload jsonb
) returns text
language plpgsql
security definer
set search_path = tap, public
as $$
declare
  prev text;
  payload_digest text;
  digest text;
begin
  select entry_hash into prev from tap.ledger_events order by seq desc limit 1 for update;
  payload_digest := encode(digest(convert_to(coalesce(p_payload, '{}'::jsonb)::text, 'utf8'), 'sha256'), 'hex');
  digest := encode(digest(convert_to(coalesce(prev, 'GENESIS') || '|' || p_correlation_id::text || '|' || coalesce(p_artist_id::text, '') || '|' || p_event_type || '|' || payload_digest, 'utf8'), 'sha256'), 'hex');
  insert into tap.ledger_events (correlation_id, artist_id, event_type, payload_hash, predecessor_hash, entry_hash)
  values (p_correlation_id, p_artist_id, p_event_type, payload_digest, prev, digest);
  return digest;
end;
$$;

-- 11VT split. Fixed buckets take floor(amount * rate); V7 takes the
-- remainder, so the split always sums exactly to the cleared amount.
create or replace function tap.split_11vt(p_payment_id uuid, p_amount_cents bigint)
returns void
language plpgsql
security definer
set search_path = tap, public
as $$
declare
  buckets text[] := array['V1','V2','V3','V4','V5','V6','V8','V9','V10','V11'];
  pct integer[] := array[1500, 750, 350, 500, 200, 1500, 2000, 1000, 800, 200];
  i integer;
  allocated bigint := 0;
  amt bigint;
begin
  for i in 1..10 loop
    amt := floor(p_amount_cents * pct[i] / 10000.0)::bigint;
    allocated := allocated + amt;
    insert into tap.allocations (payment_id, bucket, basis_points, amount_cents)
    values (p_payment_id, buckets[i], pct[i], amt);
  end loop;
  insert into tap.allocations (payment_id, bucket, basis_points, amount_cents)
  values (p_payment_id, 'V7', 1200, p_amount_cents - allocated);
end;
$$;

-- Record a cleared payment once. Same Stripe object id twice returns the
-- first row (idempotent). Splits 11VT and appends ledger evidence.
create or replace function tap.record_cleared_payment(
  p_artist_id uuid,
  p_booking_id uuid,
  p_design_order_id uuid,
  p_stripe_object_id text,
  p_stage tap.cleared_stage,
  p_amount_cents bigint,
  p_actor text,
  p_correlation_id uuid default gen_random_uuid()
) returns uuid
language plpgsql
security definer
set search_path = tap, public
as $$
declare
  payment_id uuid;
begin
  select id into payment_id from tap.payments where stripe_object_id = p_stripe_object_id;
  if found then return payment_id; end if;
  insert into tap.payments (artist_id, booking_id, design_order_id, stripe_object_id, stage, amount_cents)
  values (p_artist_id, p_booking_id, p_design_order_id, p_stripe_object_id, p_stage, p_amount_cents)
  returning id into payment_id;
  perform tap.split_11vt(payment_id, p_amount_cents);
  perform tap.append_evidence(p_correlation_id, p_artist_id, 'PAYMENT_RECORDED',
    jsonb_build_object('payment', payment_id, 'stage', p_stage, 'amount_cents', p_amount_cents, 'stripe', p_stripe_object_id));
  return payment_id;
end;
$$;

-- One trial per email, ever. Second claim raises trial_already_claimed.
create or replace function tap.claim_trial(p_email text, p_artist_id uuid)
returns void
language plpgsql
security definer
set search_path = tap, public
as $$
begin
  insert into tap.trials (email, artist_id) values (lower(p_email), p_artist_id);
exception when unique_violation then
  raise exception 'trial_already_claimed';
end;
$$;

create or replace function tap.valid_booking_transition(p_from tap.booking_state, p_to tap.booking_state)
returns boolean language sql immutable as $$
  select (p_from, p_to) in (
    ('DRAFT', 'OFFERED'),
    ('OFFERED', 'APPROVED'),
    ('APPROVED', 'CONFIRMED'),
    ('CONFIRMED', 'COMPLETED')
  ) or (p_to = 'CANCELLED' and p_from not in ('COMPLETED', 'CANCELLED'))
    or (p_to = 'REJECTED' and p_from in ('DRAFT', 'OFFERED'));
$$;

create or replace function tap.transition_booking(
  p_booking_id uuid,
  p_to tap.booking_state,
  p_actor text,
  p_correlation_id uuid default gen_random_uuid()
) returns tap.bookings
language plpgsql
security definer
set search_path = tap, public
as $$
declare
  b tap.bookings;
begin
  select * into b from tap.bookings where id = p_booking_id for update;
  if not found then raise exception 'booking_not_found'; end if;
  if not tap.valid_booking_transition(b.state, p_to) then
    raise exception 'invalid_transition:%->%', b.state, p_to;
  end if;
  update tap.bookings set state = p_to, updated_at = now() where id = p_booking_id returning * into b;
  perform tap.append_evidence(p_correlation_id, b.artist_id, 'BOOKING_TRANSITION', jsonb_build_object('booking', p_booking_id, 'to', p_to));
  return b;
end;
$$;

-- Lockdown: RLS on, no direct access for anon/authenticated.
-- Only the server (service-role key) reaches these tables, through the
-- security-definer functions above for money and state changes.
alter table tap.artists enable row level security;
alter table tap.trials enable row level security;
alter table tap.bookings enable row level security;
alter table tap.design_orders enable row level security;
alter table tap.payments enable row level security;
alter table tap.allocations enable row level security;
alter table tap.ava_threads enable row level security;
alter table tap.ava_messages enable row level security;
alter table tap.ava_memories enable row level security;
alter table tap.referrals enable row level security;
alter table tap.social_accounts enable row level security;
alter table tap.addon_purchases enable row level security;
alter table tap.ledger_events enable row level security;

revoke all on schema tap from anon, authenticated;
revoke all on all tables in schema tap from anon, authenticated;
revoke all on all functions in schema tap from anon, authenticated;
