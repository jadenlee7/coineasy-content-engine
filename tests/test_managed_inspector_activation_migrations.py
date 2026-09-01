import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD_MIGRATION = (
    ROOT
    / "supabase/migrations/20260831180000_managed_auth_telegram_inspect.sql"
).read_text()
BOUNDARY_MIGRATION = (
    ROOT
    / "supabase/migrations/20260901120000_managed_inspector_role_boundary.sql"
).read_text()

TARGET_SIGNATURES = (
    "public.managed_telegram_inspect_context(uuid,text)",
    "public.register_managed_telegram_inspect_consent(uuid,jsonb,text)",
    "public.inspect_managed_telegram_delivery_unknown(uuid)",
)


def executable_grants(sql: str) -> list[tuple[str, str]]:
    return [
        (match.group("signature").strip(), match.group("role").lower())
        for match in re.finditer(
            r"grant\s+execute\s+on\s+function\s+"
            r"(?P<signature>[^;]+?)\s+to\s+(?P<role>[a-z_][a-z0-9_]*)\s*;",
            sql,
            flags=re.IGNORECASE,
        )
    ]


class ManagedInspectorActivationMigrationTests(unittest.TestCase):
    def test_build_migration_leaves_entry_rpcs_ungranted(self) -> None:
        grants = executable_grants(BUILD_MIGRATION)
        self.assertEqual(grants, [])
        for signature in TARGET_SIGNATURES:
            self.assertRegex(
                BUILD_MIGRATION,
                rf"revoke\s+all\s+on\s+function\s+{re.escape(signature)}\s+"
                r"from\s+public,\s*anon,\s*authenticated,\s*service_role,\s*"
                r"coineasy_telegram_resolution\s*;",
            )

    def test_boundary_migration_grants_only_the_dedicated_role(self) -> None:
        grants = executable_grants(BOUNDARY_MIGRATION)
        self.assertEqual(
            grants,
            [(signature, "coineasy_managed_inspector") for signature in TARGET_SIGNATURES],
        )
        self.assertNotIn("authenticated", {role for _, role in grants})
        self.assertNotIn("public", {role for _, role in grants})

    def test_boundary_migration_fixes_full_platform_role_paths(self) -> None:
        for path in (
            "array['coineasy_managed_inspector', 'authenticator', 'postgres']::text[]",
            "array['coineasy_managed_inspector', 'authenticator', "
            "'supabase_storage_admin']::text[]",
            "array['coineasy_managed_inspector', 'postgres', "
            "'cli_login_postgres']::text[]",
            "array['coineasy_managed_inspector', 'authenticator', 'postgres', "
            "'cli_login_postgres']::text[]",
        ):
            self.assertIn(path, BOUNDARY_MIGRATION)
        self.assertIn(
            "pg_catalog.to_regrole('cli_login_postgres') is not null",
            BOUNDARY_MIGRATION,
        )
        self.assertEqual(BOUNDARY_MIGRATION.lower().count("except all"), 2)
        self.assertIn("member_rolcanlogin", BOUNDARY_MIGRATION)
        self.assertIn("member_rolreplication", BOUNDARY_MIGRATION)
        self.assertIn("member_rolbypassrls", BOUNDARY_MIGRATION)


if __name__ == "__main__":
    unittest.main()
