-- Layer 7 Phase 1: AI orchestration safety (docs/LAYER7_ORCHESTRATION_AUDIT.md F-1..F-4).
--
-- 1. Atomic claims with leases, so two workers (or a worker and a manual request)
--    never process the same ticket, and a crashed worker's claim expires.       (F-3)
-- 2. A conditional commit that never overwrites a human routing decision and
--    refuses results computed from a stale snapshot.                            (F-1)
-- 3. Deduplicated staff paging for AI-classified URGENT tickets, using the same
--    audience as the existing emergency page (active members of the owning
--    department). Emergency tickets were already paged at insert and are skipped. (F-2)
-- 4. Server-enforced cooldown and per-ticket limits on user-triggered runs.    (F-4)
--
-- Leases and cooldowns compare against clock_timestamp(), not now(): now() is the
-- transaction start, so a call that waited for a row lock would judge time from
-- before it acquired the lock.
--
-- All functions are SECURITY DEFINER for the backend's service role only:
-- EXECUTE is revoked from public, anon, and authenticated (CLAUDE.md: every
-- public function is otherwise a callable RPC endpoint).

-- ---------------------------------------------------------------------------
-- Job state. Kept out of public.tickets so claim bookkeeping never bumps the
-- ticket's updated_at, never writes history, and is not writable by clients.
-- ---------------------------------------------------------------------------

create table public.ticket_ai_jobs (
  ticket_id                  bigint primary key references public.tickets(ticket_id),
  claim_token                uuid,
  claimed_by                 text,
  claimed_until              timestamptz,
  snapshot_updated_at        timestamptz,
  attempts                   integer not null default 0 check (attempts >= 0),
  last_attempt_at            timestamptz,
  last_result                text,
  last_error                 text,
  -- History rows at or below this id predate an explicit staff reclassification
  -- request and no longer block the AI from routing.
  human_baseline_history_id  bigint not null default 0,
  reclassify_requested_by    uuid references public.users(user_id),
  reclassify_requested_at    timestamptz,
  requester_manual_requests  integer not null default 0 check (requester_manual_requests >= 0),
  staff_manual_requests      integer not null default 0 check (staff_manual_requests >= 0),
  last_manual_request_at     timestamptz,
  urgent_notified_at         timestamptz,
  created_at                 timestamptz not null default now(),
  updated_at                 timestamptz not null default now(),
  constraint ticket_ai_jobs_claim_consistent
    check ((claim_token is null) = (claimed_until is null))
);

comment on table public.ticket_ai_jobs is
  'AI classification job state: lease, attempts, manual-request limits. Written only by service-role RPCs.';

create trigger set_updated_at before update on public.ticket_ai_jobs
  for each row execute function public.set_updated_at();

alter table public.ticket_ai_jobs enable row level security;

-- Staff who can see a ticket may see its AI job state (diagnostics). Nobody but
-- the service role writes it.
revoke all on public.ticket_ai_jobs from anon, authenticated;
grant select on public.ticket_ai_jobs to authenticated;
grant all on public.ticket_ai_jobs to service_role;

create policy staff_read_ticket_ai_jobs on public.ticket_ai_jobs
  for select to authenticated
  using (private.is_staff() and private.can_access_ticket(ticket_id));

-- ---------------------------------------------------------------------------
-- Deduplication guarantee for AI urgent pages: at most one per ticket per user.
-- ---------------------------------------------------------------------------

create unique index ux_notifications_ai_urgent_once
  on public.notifications (ticket_id, user_id)
  where type = 'AI_URGENT';

-- ---------------------------------------------------------------------------
-- History: record issue-type changes too, so a staff member's category choice is
-- detectable as a human routing decision (previously unlogged; audit F-10).
-- Body identical to 20260928211144 except the ISSUE_TYPE_CHANGED block.
-- ---------------------------------------------------------------------------

create or replace function public.log_ticket_changes()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := (select auth.uid());
begin
  if new.status_id is distinct from old.status_id then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'STATUS_CHANGED', 'status_id',
      (select name from public.statuses where status_id = old.status_id),
      (select name from public.statuses where status_id = new.status_id));
  end if;

  if new.priority_id is distinct from old.priority_id then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'PRIORITY_CHANGED', 'priority_id',
      (select name from public.priorities where priority_id = old.priority_id),
      (select name from public.priorities where priority_id = new.priority_id));
  end if;

  if new.department_id is distinct from old.department_id then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'DEPARTMENT_CHANGED', 'department_id',
      (select name from public.departments where department_id = old.department_id),
      (select name from public.departments where department_id = new.department_id));
  end if;

  if new.issue_type_id is distinct from old.issue_type_id then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'ISSUE_TYPE_CHANGED', 'issue_type_id',
      (select name from public.issue_types where issue_type_id = old.issue_type_id),
      (select name from public.issue_types where issue_type_id = new.issue_type_id));
  end if;

  if new.ai_routing_status is distinct from old.ai_routing_status then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'AI_ROUTING', 'ai_routing_status',
            old.ai_routing_status, new.ai_routing_status);
  end if;

  if new.resolved_at is distinct from old.resolved_at and new.resolved_at is not null then
    insert into public.ticket_history (ticket_id, changed_by, action, new_value)
    values (new.ticket_id, v_actor, 'RESOLVED', new.resolution_notes);
  end if;

  if new.deleted_at is distinct from old.deleted_at and new.deleted_at is not null then
    insert into public.ticket_history (ticket_id, changed_by, action)
    values (new.ticket_id, v_actor, 'WITHDRAWN');
  end if;

  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- True when a person (not the backend or cron, whose changes carry no user) has
-- made a routing decision on the ticket after the given history id.
create or replace function private.ai_human_routing_after(p_ticket_id bigint, p_baseline bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.ticket_history h
    where h.ticket_id = p_ticket_id
      and h.history_id > coalesce(p_baseline, 0)
      and h.changed_by is not null
      and h.action in ('STATUS_CHANGED', 'PRIORITY_CHANGED', 'DEPARTMENT_CHANGED',
                       'ISSUE_TYPE_CHANGED', 'ASSIGNED', 'UNASSIGNED', 'AI_ROUTING')
  );
$$;

-- True while the ticket is still at the queue stage, where AI routing applies:
-- not yet being worked on and nobody assigned.
create or replace function private.ai_routing_stage_open(p_ticket_id bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce((
    select s.name in ('NEW', 'AI_PROCESSING', 'NEEDS_REVIEW', 'ASSIGNED')
       and not exists (select 1 from public.ticket_assignments a
                       where a.ticket_id = t.ticket_id and a.unassigned_at is null)
    from public.tickets t join public.statuses s on s.status_id = t.status_id
    where t.ticket_id = p_ticket_id
  ), false);
$$;

create or replace function private.ai_clear_claim(p_ticket_id bigint, p_result text, p_error text default null)
returns void
language sql
security definer
set search_path = ''
as $$
  update public.ticket_ai_jobs
     set claim_token = null, claimed_by = null, claimed_until = null,
         last_result = p_result, last_error = p_error
   where ticket_id = p_ticket_id;
$$;

-- ---------------------------------------------------------------------------
-- Claim
-- ---------------------------------------------------------------------------

create or replace function public.ai_claim_ticket(
  p_ticket_id bigint,
  p_worker text,
  p_lease_seconds integer,
  p_allow_failed boolean default false,
  p_max_attempts integer default 3)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  t public.tickets;
  j public.ticket_ai_jobs;
  v_token uuid := gen_random_uuid();
begin
  if p_lease_seconds is null or p_lease_seconds < 1 or p_max_attempts is null or p_max_attempts < 1 then
    raise exception 'Lease and attempt limit must be positive';
  end if;

  -- Lock order everywhere: ticket row, then job row.
  select * into t from public.tickets where ticket_id = p_ticket_id for update;
  if not found then
    return jsonb_build_object('claimed', false, 'reason', 'not_found');
  end if;
  insert into public.ticket_ai_jobs (ticket_id) values (p_ticket_id) on conflict (ticket_id) do nothing;
  select * into j from public.ticket_ai_jobs where ticket_id = p_ticket_id for update;

  if t.deleted_at is not null then
    return jsonb_build_object('claimed', false, 'reason', 'withdrawn');
  end if;
  if not (t.ai_routing_status = 'PENDING' or (p_allow_failed and t.ai_routing_status = 'FAILED')) then
    return jsonb_build_object('claimed', false, 'reason', 'not_pending',
                              'ai_routing_status', t.ai_routing_status);
  end if;
  if j.claimed_until is not null and j.claimed_until > clock_timestamp() then
    return jsonb_build_object('claimed', false, 'reason', 'in_progress');
  end if;

  -- A person already routed this ticket. Their decision stands; take the ticket
  -- out of the pending queue so no worker keeps retrying it.
  if private.ai_human_routing_after(p_ticket_id, j.human_baseline_history_id)
     or not private.ai_routing_stage_open(p_ticket_id) then
    update public.tickets set ai_routing_status = 'OVERRIDDEN' where ticket_id = p_ticket_id;
    perform private.ai_clear_claim(p_ticket_id, 'SUPERSEDED_BY_HUMAN');
    return jsonb_build_object('claimed', false, 'reason', 'superseded_by_human');
  end if;

  -- Crash loops and repeated stale snapshots end in human review, never in an
  -- endlessly pending ticket.
  if j.attempts >= p_max_attempts then
    update public.tickets
       set ai_routing_status = 'FAILED',
           status_id = case when is_emergency then status_id
                            else (select status_id from public.statuses where name = 'NEEDS_REVIEW') end
     where ticket_id = p_ticket_id;
    perform private.ai_clear_claim(p_ticket_id, 'ATTEMPTS_EXHAUSTED');
    return jsonb_build_object('claimed', false, 'reason', 'attempts_exhausted', 'attempts', j.attempts);
  end if;

  update public.ticket_ai_jobs
     set claim_token = v_token,
         claimed_by = left(coalesce(p_worker, 'unknown'), 200),
         claimed_until = clock_timestamp() + make_interval(secs => p_lease_seconds),
         attempts = attempts + 1,
         last_attempt_at = clock_timestamp(),
         snapshot_updated_at = t.updated_at,
         last_error = null
   where ticket_id = p_ticket_id;

  return jsonb_build_object('claimed', true, 'claim_token', v_token,
                            'attempts', j.attempts + 1, 'ticket', to_jsonb(t));
end;
$$;

-- ---------------------------------------------------------------------------
-- Commit
-- ---------------------------------------------------------------------------

create or replace function public.ai_commit_classification(
  p_ticket_id bigint,
  p_claim_token uuid,
  p_outcome text,               -- 'ROUTED' | 'REVIEW' | 'FAILED'
  p_department_id integer default null,
  p_issue_type_id integer default null,
  p_priority_id integer default null,
  p_confidence numeric default null)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  t public.tickets;
  j public.ticket_ai_jobs;
  v_outcome text := p_outcome;
  v_result text;
  v_review_status integer := (select status_id from public.statuses where name = 'NEEDS_REVIEW');
  v_assigned_status integer := (select status_id from public.statuses where name = 'ASSIGNED');
  v_urgent integer := (select priority_id from public.priorities where name = 'URGENT');
  v_staff uuid;
  v_rows integer;
  v_notified integer := 0;
  v_notify_error text;
begin
  if p_outcome is null or p_outcome not in ('ROUTED', 'REVIEW', 'FAILED') then
    raise exception 'Unknown AI outcome %', p_outcome;
  end if;

  select * into t from public.tickets where ticket_id = p_ticket_id for update;
  if not found then
    return jsonb_build_object('result', 'NOT_FOUND');
  end if;
  select * into j from public.ticket_ai_jobs where ticket_id = p_ticket_id for update;

  -- Only the current lease holder may write. An expired lease may already have
  -- been taken over by another worker.
  if not found or j.claim_token is distinct from p_claim_token or j.claimed_until <= clock_timestamp() then
    return jsonb_build_object('result', 'LEASE_LOST');
  end if;

  if t.deleted_at is not null then
    v_result := 'WITHDRAWN';
  elsif t.ai_routing_status not in ('PENDING', 'FAILED') then
    v_result := 'ALREADY_FINAL';
  elsif private.ai_human_routing_after(p_ticket_id, j.human_baseline_history_id)
        or not private.ai_routing_stage_open(p_ticket_id) then
    -- Never overwrite a human decision. Keep the suggestion for audit only.
    update public.tickets
       set ai_suggested_department_id = case when v_outcome = 'FAILED' then ai_suggested_department_id else p_department_id end,
           ai_suggested_issue_type_id = case when v_outcome = 'FAILED' then ai_suggested_issue_type_id else p_issue_type_id end,
           ai_suggested_priority_id   = case when v_outcome = 'FAILED' then ai_suggested_priority_id else p_priority_id end,
           ai_confidence              = case when v_outcome = 'FAILED' then ai_confidence else round(p_confidence, 4) end,
           ai_routing_status          = 'OVERRIDDEN'
     where ticket_id = p_ticket_id;
    v_result := 'SUPERSEDED_BY_HUMAN';
  elsif t.updated_at is distinct from j.snapshot_updated_at then
    -- Something changed while the model ran. Discard; the ticket stays pending
    -- and is re-read on the next attempt.
    v_result := 'STALE_SNAPSHOT';
  else
    if v_outcome = 'ROUTED' and not coalesce(
         (select d.active from public.departments d where d.department_id = p_department_id), false) then
      v_outcome := 'REVIEW';   -- department missing or deactivated since the cache was built
    end if;

    if v_outcome = 'FAILED' then
      update public.tickets
         set ai_routing_status = 'FAILED',
             status_id = case when is_emergency then status_id else v_review_status end
       where ticket_id = p_ticket_id;
      v_result := 'COMMITTED_FAILED';
    elsif v_outcome = 'REVIEW' then
      update public.tickets
         set ai_suggested_department_id = p_department_id,
             ai_suggested_issue_type_id = p_issue_type_id,
             ai_suggested_priority_id   = p_priority_id,
             ai_confidence              = round(p_confidence, 4),
             ai_routing_status          = 'NEEDS_REVIEW',
             status_id = case when is_emergency then status_id else v_review_status end
       where ticket_id = p_ticket_id;
      v_result := 'COMMITTED_NEEDS_REVIEW';
    else
      -- An emergency ticket is never re-routed away from Campus Safety by the model.
      update public.tickets
         set ai_suggested_department_id = p_department_id,
             ai_suggested_issue_type_id = p_issue_type_id,
             ai_suggested_priority_id   = p_priority_id,
             ai_confidence              = round(p_confidence, 4),
             ai_routing_status          = 'AUTO_ROUTED',
             issue_type_id = coalesce(p_issue_type_id, issue_type_id),
             department_id = case when is_emergency then department_id else p_department_id end,
             priority_id   = case when is_emergency then priority_id else p_priority_id end,
             status_id     = case when is_emergency then status_id else v_assigned_status end
       where ticket_id = p_ticket_id;
      v_result := 'COMMITTED_AUTO_ROUTED';

      -- Page the owning department once per person. Emergency tickets were paged
      -- at insert (notify_ticket_created) and are not paged again. A failure here
      -- never rolls back the routing (CLAUDE.md: notification isolation).
      if not t.is_emergency and p_priority_id = v_urgent then
        begin
          for v_staff in
            select ud.user_id from public.user_departments ud
            join public.users u on u.user_id = ud.user_id and u.active
            where ud.department_id = p_department_id
          loop
            insert into public.notifications (user_id, ticket_id, type, message)
            values (v_staff, p_ticket_id, 'AI_URGENT',
                    'Ticket ' || private.ticket_label(p_ticket_id)
                    || ' was classified URGENT by automatic routing. Please review it now.')
            on conflict (ticket_id, user_id) where type = 'AI_URGENT' do nothing;
            get diagnostics v_rows = row_count;
            v_notified := v_notified + v_rows;
          end loop;
          update public.ticket_ai_jobs
             set urgent_notified_at = coalesce(urgent_notified_at, now())
           where ticket_id = p_ticket_id;
        exception when others then
          v_notify_error := sqlerrm;
          raise warning 'AI urgent notification for ticket % failed: %', p_ticket_id, sqlerrm;
        end;
      end if;
    end if;
  end if;

  perform private.ai_clear_claim(p_ticket_id, v_result, v_notify_error);
  return jsonb_build_object('result', v_result, 'outcome', v_outcome,
                            'urgent_notifications', v_notified,
                            'notification_error', v_notify_error);
end;
$$;

-- ---------------------------------------------------------------------------
-- Release after a handled error (a crash simply lets the lease expire).
-- ---------------------------------------------------------------------------

create or replace function public.ai_release_claim(p_ticket_id bigint, p_claim_token uuid, p_error text default null)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.ticket_ai_jobs
     set claim_token = null, claimed_by = null, claimed_until = null,
         last_result = 'RELEASED_AFTER_ERROR', last_error = left(p_error, 1000)
   where ticket_id = p_ticket_id and claim_token = p_claim_token;
  return found;
end;
$$;

-- ---------------------------------------------------------------------------
-- Staff reclassification request. The backend authorizes the staff member first;
-- the request becomes the new baseline, so earlier human routing no longer blocks
-- the AI, but anything a person does after the request still does.
-- ---------------------------------------------------------------------------

create or replace function public.ai_request_reclassification(p_ticket_id bigint, p_requested_by uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  t public.tickets;
  j public.ticket_ai_jobs;
begin
  select * into t from public.tickets where ticket_id = p_ticket_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'not_found');
  end if;
  if t.deleted_at is not null then
    return jsonb_build_object('ok', false, 'reason', 'withdrawn');
  end if;
  insert into public.ticket_ai_jobs (ticket_id) values (p_ticket_id) on conflict (ticket_id) do nothing;
  select * into j from public.ticket_ai_jobs where ticket_id = p_ticket_id for update;
  if j.claimed_until is not null and j.claimed_until > clock_timestamp() then
    return jsonb_build_object('ok', false, 'reason', 'in_progress');
  end if;

  update public.tickets set ai_routing_status = 'PENDING'
   where ticket_id = p_ticket_id and ai_routing_status <> 'PENDING';

  update public.ticket_ai_jobs
     set attempts = 0,
         human_baseline_history_id =
           (select coalesce(max(h.history_id), 0) from public.ticket_history h where h.ticket_id = p_ticket_id),
         reclassify_requested_by = p_requested_by,
         reclassify_requested_at = clock_timestamp(),
         last_result = 'RECLASSIFY_REQUESTED',
         last_error = null
   where ticket_id = p_ticket_id;
  return jsonb_build_object('ok', true);
end;
$$;

-- ---------------------------------------------------------------------------
-- Manual (user-triggered) run limits: a per-ticket cooldown for everyone and a
-- lifetime cap for requesters. Denied requests consume nothing.
-- ---------------------------------------------------------------------------

create or replace function public.ai_register_manual_request(
  p_ticket_id bigint,
  p_is_staff boolean,
  p_cooldown_seconds integer,
  p_requester_limit integer)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  j public.ticket_ai_jobs;
  v_ready_at timestamptz;
begin
  if p_cooldown_seconds is null or p_cooldown_seconds < 0 or p_requester_limit is null or p_requester_limit < 0 then
    raise exception 'Cooldown and limit must be non-negative';
  end if;
  perform 1 from public.tickets where ticket_id = p_ticket_id for update;
  if not found then
    return jsonb_build_object('allowed', false, 'reason', 'not_found');
  end if;
  insert into public.ticket_ai_jobs (ticket_id) values (p_ticket_id) on conflict (ticket_id) do nothing;
  select * into j from public.ticket_ai_jobs where ticket_id = p_ticket_id for update;

  v_ready_at := j.last_manual_request_at + make_interval(secs => p_cooldown_seconds);
  if j.last_manual_request_at is not null and v_ready_at > clock_timestamp() then
    return jsonb_build_object('allowed', false, 'reason', 'cooldown',
                              'retry_after_seconds', greatest(1, ceil(extract(epoch from v_ready_at - clock_timestamp()))::integer));
  end if;
  if not coalesce(p_is_staff, false) and j.requester_manual_requests >= p_requester_limit then
    return jsonb_build_object('allowed', false, 'reason', 'limit_reached');
  end if;

  update public.ticket_ai_jobs
     set requester_manual_requests = requester_manual_requests + case when p_is_staff then 0 else 1 end,
         staff_manual_requests     = staff_manual_requests + case when p_is_staff then 1 else 0 end,
         last_manual_request_at    = clock_timestamp()
   where ticket_id = p_ticket_id;
  return jsonb_build_object('allowed', true);
end;
$$;

-- ---------------------------------------------------------------------------
-- Privileges: service role only.
-- ---------------------------------------------------------------------------

revoke execute on function private.ai_human_routing_after(bigint, bigint) from public, anon, authenticated;
revoke execute on function private.ai_routing_stage_open(bigint) from public, anon, authenticated;
revoke execute on function private.ai_clear_claim(bigint, text, text) from public, anon, authenticated;

revoke execute on function public.ai_claim_ticket(bigint, text, integer, boolean, integer) from public, anon, authenticated;
revoke execute on function public.ai_commit_classification(bigint, uuid, text, integer, integer, integer, numeric) from public, anon, authenticated;
revoke execute on function public.ai_release_claim(bigint, uuid, text) from public, anon, authenticated;
revoke execute on function public.ai_request_reclassification(bigint, uuid) from public, anon, authenticated;
revoke execute on function public.ai_register_manual_request(bigint, boolean, integer, integer) from public, anon, authenticated;

grant execute on function public.ai_claim_ticket(bigint, text, integer, boolean, integer) to service_role;
grant execute on function public.ai_commit_classification(bigint, uuid, text, integer, integer, integer, numeric) to service_role;
grant execute on function public.ai_release_claim(bigint, uuid, text) to service_role;
grant execute on function public.ai_request_reclassification(bigint, uuid) to service_role;
grant execute on function public.ai_register_manual_request(bigint, boolean, integer, integer) to service_role;
