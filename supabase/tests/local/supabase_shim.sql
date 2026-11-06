-- Minimal stand-in for the Supabase platform objects the migrations reference,
-- so the real migrations can be applied to a throwaway local PostgreSQL.
--
-- TEST USE ONLY. Never apply this to a Supabase project: the real platform
-- already provides these objects with stricter implementations.
--
-- Fidelity notes:
--   * auth.uid() reads request.jwt.claims -> sub, exactly as PostgREST sets it
--     and as supabase/tests/workflow_and_security.sql simulates it.
--   * service_role has BYPASSRLS and full table privileges, matching Supabase.
--   * storage.* is reduced to the columns the migrations touch.
--   * cron.schedule is a recording no-op (pg_cron is not available locally).

create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;

create schema auth;
grant usage on schema auth to anon, authenticated, service_role;

create table auth.users (
  id uuid primary key default gen_random_uuid(),
  email text,
  raw_user_meta_data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create function auth.uid() returns uuid
language sql stable
as $$
  select nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub', '')::uuid
$$;
grant execute on function auth.uid() to anon, authenticated, service_role;

create schema storage;
grant usage on schema storage to anon, authenticated, service_role;
create table storage.buckets (
  id text primary key,
  name text not null,
  public boolean default false,
  file_size_limit bigint,
  allowed_mime_types text[]
);
create table storage.objects (
  id uuid primary key default gen_random_uuid(),
  bucket_id text references storage.buckets(id),
  name text,
  owner_id text,
  created_at timestamptz default now()
);
alter table storage.objects enable row level security;

create schema cron;
create table cron.job (jobid bigserial primary key, jobname text, schedule text, command text);
create function cron.schedule(p_name text, p_schedule text, p_command text) returns bigint
language sql
as $$ insert into cron.job (jobname, schedule, command) values (p_name, p_schedule, p_command) returning jobid $$;
