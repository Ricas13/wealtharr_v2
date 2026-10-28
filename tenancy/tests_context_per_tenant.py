from django.test import SimpleTestCase, override_settings

from .context import NoTenantContext, per_tenant, require_tenant_id


@override_settings(RLS_ENABLED=False)
class PerTenantTests(SimpleTestCase):
    def test_each_item_runs_in_its_owners_context_and_resets(self):
        class Item:
            def __init__(self, owner_id):
                self.owner_id = owner_id

        seen = [require_tenant_id() for _ in per_tenant([Item(1), Item(2)])]
        self.assertEqual(seen, [1, 2])
        with self.assertRaises(NoTenantContext):
            require_tenant_id()
