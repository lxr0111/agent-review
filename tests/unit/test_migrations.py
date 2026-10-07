from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import call, patch


def test_webhook_event_downgrade_drops_table_before_parent_metadata() -> None:
    migration_path = (
        Path(__file__).parents[2] / "alembic" / "versions" / "20260904_0002_add_webhook_events.py"
    )
    spec = spec_from_file_location("webhook_event_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    with patch.object(migration, "op") as operation:
        migration.downgrade()

    assert operation.method_calls == [
        call.drop_table("webhook_events"),
        call.drop_index("ix_review_tasks_delivery_id", table_name="review_tasks"),
        call.drop_column("review_tasks", "delivery_id"),
        call.drop_column("review_tasks", "base_sha"),
        call.drop_column("review_tasks", "installation_id"),
        call.drop_column("review_tasks", "github_repository_id"),
    ]
