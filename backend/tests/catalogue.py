"""The preference catalogue exactly as migration 0006 seeds it."""

import importlib.util
from pathlib import Path

from database.models import PreferenceDefinition

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic/versions/0006_preferences.py"
)
_spec = importlib.util.spec_from_file_location("migration_0006", _MIGRATION)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

DEFINITIONS: list[dict] = _module.DEFINITIONS


def definitions() -> list[PreferenceDefinition]:
    return [PreferenceDefinition(**row) for row in DEFINITIONS]
