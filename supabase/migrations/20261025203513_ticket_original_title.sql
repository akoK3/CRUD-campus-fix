-- Keep the submitted title separately from the AI-polished display title.
-- No backfill: historical titles already changed by AI cannot be reconstructed.
alter table public.tickets
  add column if not exists original_title text;

comment on column public.tickets.original_title is
  'Student title captured at submission before enrichment; null for legacy tickets whose original title is unavailable.';

-- Capture titles from older clients too, and protect the snapshot on every
-- update, including service-role enrichment. No routing fields are touched.
create or replace function public.preserve_ticket_original_title()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.original_title := coalesce(new.original_title, new.title);
  else
    new.original_title := old.original_title;
  end if;
  return new;
end;
$$;

create or replace trigger preserve_ticket_original_title
  before insert or update on public.tickets
  for each row execute function public.preserve_ticket_original_title();

revoke execute on function public.preserve_ticket_original_title()
  from public, anon, authenticated;
