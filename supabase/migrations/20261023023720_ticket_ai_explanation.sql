-- Optional writing enrichment. Existing reports remain unchanged, and the
-- existing tickets RLS policies and grants continue to apply.
alter table public.tickets
  add column if not exists ai_explanation text;

comment on column public.tickets.ai_explanation is
  'Optional explanation generated after AI routing; original_text preserves the submitted report.';
