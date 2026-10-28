"""Optional PostgreSQL row-level security, a second wall behind the tenant-scoped queries.

Off unless ``RLS_ENABLED=1``. When on, every tenant context tells the database whose data this
connection may see (``app.tenant_id``) or that it is trusted platform code (``app.bypass``).
The policies themselves are installed by ``manage.py rls_apply`` and only bind a database role
that does not own the tables, so migrations (run as the owner) are unaffected.
"""
from django.conf import settings
from django.db import connection

SQL_FUNCTIONS = (
    "CREATE OR REPLACE FUNCTION app_tenant() RETURNS bigint LANGUAGE sql STABLE AS "
    "$$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::bigint $$",
    "CREATE OR REPLACE FUNCTION app_bypass() RETURNS boolean LANGUAGE sql STABLE AS "
    "$$ SELECT coalesce(current_setting('app.bypass', true), '') = 'on' $$",
)
POLICY = "wealtharr_tenant"


def enabled():
    return bool(getattr(settings, "RLS_ENABLED", False)) and connection.vendor == "postgresql"


def _set(tenant_id, bypass):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', %s, false), set_config('app.bypass', %s, false)",
                       ["" if tenant_id is None else str(int(tenant_id)), "on" if bypass else ""])


def push(tenant_id, bypass):
    """Apply a tenant to this connection; returns what to restore. No-op when disabled."""
    if not enabled():
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.tenant_id', true), current_setting('app.bypass', true)")
        previous = cursor.fetchone()
    _set(tenant_id, bypass)
    return previous


def pop(previous):
    if previous is None or not enabled():
        return
    tenant, bypass = previous
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', %s, false), set_config('app.bypass', %s, false)",
                       [tenant or "", bypass or ""])


# ---- policy generation (pure introspection; the commands run the SQL) ---------------------

def _predicate(model):
    names = {f.name: f for f in model._meta.concrete_fields}
    if "owner" in names:
        return "owner_id = app_tenant()"
    for link in ("strategy", "account"):
        field = names.get(link)
        if field is not None and field.remote_field is not None:
            target = field.remote_field.model._meta.db_table
            return f"{field.column} IN (SELECT id FROM {target})"
    return None


def tracker_models():
    from django.apps import apps
    return list(apps.get_app_config("tracker").get_models())


def plan():
    """([(table, predicate)], [tables with no owner link])."""
    protected, open_tables = [], []
    for model in tracker_models():
        predicate = _predicate(model)
        (protected if predicate else open_tables).append(
            (model._meta.db_table, predicate) if predicate else model._meta.db_table)
    return protected, open_tables


def apply_statements(role, force=False):
    qn = connection.ops.quote_name
    statements = list(SQL_FUNCTIONS)
    protected, _ = plan()
    for table, predicate in protected:
        clause = f"app_bypass() OR {predicate}"
        statements += [
            f"ALTER TABLE {qn(table)} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {qn(table)} {'FORCE' if force else 'NO FORCE'} ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {POLICY} ON {qn(table)}",
            f"CREATE POLICY {POLICY} ON {qn(table)} USING ({clause}) WITH CHECK ({clause})",
        ]
    if role:
        statements += [
            f"GRANT USAGE ON SCHEMA public TO {qn(role)}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {qn(role)}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {qn(role)}",
            f"GRANT EXECUTE ON FUNCTION app_tenant(), app_bypass() TO {qn(role)}",
        ]
    return statements


def remove_statements():
    qn = connection.ops.quote_name
    protected, _ = plan()
    out = []
    for table, _predicate_text in protected:
        out += [f"DROP POLICY IF EXISTS {POLICY} ON {qn(table)}",
                f"ALTER TABLE {qn(table)} DISABLE ROW LEVEL SECURITY"]
    return out
