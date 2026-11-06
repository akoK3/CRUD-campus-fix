# Local database integration tests

These tests apply **every migration in `supabase/migrations/`** to a throwaway local PostgreSQL and test the database rules for real: RLS, grants, triggers, row locks, and concurrent connections. Nothing here touches a Supabase project.

| File | Purpose |
|---|---|
| `supabase_shim.sql` | Minimal stand-ins for the Supabase platform objects the migrations reference (roles, `auth.users` / `auth.uid()`, `storage.*`, `cron.schedule`). **Test use only.** |
| `harness.py` | Starts an embedded PostgreSQL, creates a fresh database, applies the shim and the migrations, and seeds test accounts |
| `test_ai_orchestration_db.py` | Tests for `20261105120000_ai_orchestration_safety.sql`, plus end-to-end tests of the real backend worker code against the database |
| `requirements.txt` | Test-only packages |

## Run

Use a separate virtual environment, never `backend/.venv`:

```powershell
python -m venv .venv-dbtest
.venv-dbtest\Scripts\python -m pip install -r supabase/tests/local/requirements.txt
.venv-dbtest\Scripts\python -m pip install -r backend/requirements.txt -c backend/requirements.lock.txt   # end-to-end tests
.venv-dbtest\Scripts\python -m unittest discover -s supabase/tests/local -v
```

The tests skip themselves when the packages are not installed, so they are not part of the default CI job.

## Fidelity notes (read before trusting a result)

- **Migrations are applied unmodified**, except that the single `create extension pg_cron` statement is skipped.
- **RLS is not enabled by any migration.** The live project enforces it (CLAUDE.md, "Verified Security Behavior"), so it was evidently enabled outside the migrations. The harness mirrors the live state by enabling RLS on every table that has policies. The project's own `supabase/tests/workflow_and_security.sql` passes 28/28 against this harness.
- PostgreSQL here is 16.x (embedded). Supabase may run a different major version.
- Test calls cast arguments to each function's exact signature. PostgREST resolves the named JSON arguments the backend sends; that path is **not** exercised here and needs a check against a real Supabase instance.
