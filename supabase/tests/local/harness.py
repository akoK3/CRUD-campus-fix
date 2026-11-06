"""Throwaway local PostgreSQL with every CampusFix migration applied.

Used by the database integration tests in this directory. Requires the optional
test-only packages in ``requirements.txt`` (an embedded PostgreSQL via ``pgserver``
and ``psycopg``). Nothing here touches a Supabase project.

The migrations are applied unmodified, with one documented exception: the single
``create extension ... pg_cron`` statement is skipped because pg_cron is not
available locally; ``supabase_shim.sql`` provides a recording ``cron.schedule``.
"""
from __future__ import annotations

import atexit
import os
from pathlib import Path
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MIGRATIONS = REPO / "supabase" / "migrations"
SHIM = HERE / "supabase_shim.sql"
PG_CRON_LINE = "create extension if not exists pg_cron with schema pg_catalog;"

ACCOUNTS = {
    # email: (role, department)
    "sys.admin@caldwell.edu": ("SYSTEM_ADMIN", None),
    "fac.admin@caldwell.edu": ("DEPARTMENT_ADMIN", "Facilities"),
    "fac.staff@caldwell.edu": ("DEPARTMENT_STAFF", "Facilities"),
    "fac.staff2@caldwell.edu": ("DEPARTMENT_STAFF", "Facilities"),
    "it.staff@caldwell.edu": ("DEPARTMENT_STAFF", "IT"),
    "student2@caldwell.edu": ("STUDENT", None),
}


def available() -> bool:
    try:
        import pgserver  # noqa: F401
        import psycopg  # noqa: F401
    except Exception:
        return False
    return True


_SERVER = None


def _server():
    global _SERVER
    if _SERVER is None:
        import pgserver
        data_dir = Path(tempfile.mkdtemp(prefix="campusfix_pg_"))
        _SERVER = pgserver.get_server(str(data_dir), cleanup_mode="delete")
        atexit.register(_SERVER.cleanup)
    return _SERVER


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.sql"))


def create_database() -> str:
    """Create a fresh database with shim + migrations + test accounts. Returns its URI."""
    import psycopg

    server = _server()
    admin_uri = server.get_uri()
    name = f"cf_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute(f'create database "{name}"')
    uri = admin_uri.rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(uri, autocommit=True) as conn:
        roles_exist = conn.execute("select count(*) from pg_roles where rolname = 'service_role'").fetchone()[0]
        shim = SHIM.read_text(encoding="utf-8")
        if roles_exist:  # roles are cluster-wide; create them only once
            shim = "\n".join(l for l in shim.splitlines() if not l.startswith("create role"))
        conn.execute(shim)
        for path in migration_files():
            sql = path.read_text(encoding="utf-8")
            if PG_CRON_LINE in sql:
                sql = sql.replace(PG_CRON_LINE, "-- (pg_cron extension skipped by local harness)")
            try:
                conn.execute(sql)
            except Exception as error:  # pragma: no cover - surfaces migration failures clearly
                raise RuntimeError(f"Migration {path.name} failed locally: {error}") from error
        # FIDELITY STEP (documented finding): no migration in supabase/migrations enables
        # row level security, yet the live project enforces it (CLAUDE.md "Verified Security
        # Behavior"); it was evidently enabled outside the migrations. Mirror that state by
        # enabling RLS on every table that has at least one policy.
        policy_tables = conn.execute("""
            select distinct n.nspname, c.relname from pg_policy p
            join pg_class c on c.oid = p.polrelid join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public'
        """).fetchall()
        for schema, table in policy_tables:
            conn.execute(f'alter table "{schema}"."{table}" enable row level security')
        # Supabase grants service_role full access to public objects.
        conn.execute("""
            grant usage on schema public to service_role;
            grant all on all tables in schema public to service_role;
            grant all on all sequences in schema public to service_role;
            grant usage on schema private to service_role;
        """)
        _seed_accounts(conn)
    return uri


def _seed_accounts(conn) -> None:
    for email, (role, dept) in ACCOUNTS.items():
        user_id = conn.execute(
            "insert into auth.users (email, raw_user_meta_data) values (%s, %s) returning id",
            (email, '{"first_name": "Test", "last_name": "User"}'),
        ).fetchone()[0]
        conn.execute(
            "update public.users set role_id = (select role_id from public.roles where name = %s) where user_id = %s",
            (role, user_id),
        )
        if dept:
            conn.execute(
                "insert into public.user_departments (user_id, department_id) "
                "select %s, department_id from public.departments where name = %s",
                (user_id, dept),
            )


def connect(uri: str, *, autocommit: bool = True):
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(uri, autocommit=autocommit, row_factory=dict_row)


def act_as(conn, email: str | None) -> None:
    """Switch the session to a simulated caller. None = service role (auth.uid() is null)."""
    conn.execute("reset role")
    if email is None:
        conn.execute("select set_config('request.jwt.claims', '', false)")
        conn.execute("set role service_role")
        return
    user_id = conn.execute("select user_id from public.users where email = %s", (email,)).fetchone()["user_id"]
    conn.execute("select set_config('request.jwt.claims', %s, false)",
                 (f'{{"sub": "{user_id}", "role": "authenticated"}}',))
    conn.execute("set role authenticated")


if __name__ == "__main__":
    print(create_database())
    if os.environ.get("KEEP"):
        input("Database running; press Enter to stop.")
