from pathlib import Path
from types import SimpleNamespace

from service import managers
from service.managers import ShiftManager, choose_manager, store_managers

TEAM = [
    ShiftManager("ananya", "Ananya Rao", "SHIFT-MOR", "Morning", "06:00", "14:00"),
    ShiftManager("karthik", "Karthik Reddy", "SHIFT-EVE", "Evening", "14:00", "22:00"),
]


def test_store_managers_map_rows_in_query_order(monkeypatch) -> None:
    rows = [
        SimpleNamespace(
            manager_id="ananya",
            name="Ananya Rao",
            shift_id="SHIFT-MOR",
            shift_name="Morning",
            shift_start="06:00",
            shift_end="14:00",
        ),
    ]
    seen = []

    def list_managers(store_id, engine=None):
        seen.append(store_id)
        return rows

    monkeypatch.setattr(managers, "list_managers", list_managers)
    [manager] = store_managers()
    assert seen == ["DS-BLR-014"]
    assert manager == TEAM[0]
    assert manager.shift == "Morning shift, 06:00 to 14:00"


def test_choose_manager_falls_back_safely() -> None:
    assert choose_manager("ananya", TEAM) == TEAM[0]
    assert choose_manager("nobody", TEAM) == TEAM[1]
    assert choose_manager(None, TEAM) == TEAM[1]
    assert choose_manager("nobody", TEAM[:1]) == TEAM[0]
    assert choose_manager("karthik", []) is None


def test_seeded_managers_have_unique_shifts() -> None:
    import importlib.util

    path = Path(__file__).parents[1] / "alembic" / "versions" / "0010_managers.py"
    spec = importlib.util.spec_from_file_location("managers_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    shift_ids = [row["shift_id"] for row in migration.MANAGERS]
    assert len(shift_ids) == len(set(shift_ids)) == 3
    assert "karthik" in {row["manager_id"] for row in migration.MANAGERS}
