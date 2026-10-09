"""Browser scripts owned by the UI, loaded from local assets."""

from pathlib import Path

ASSETS = Path(__file__).with_name("assets")


def browser_script(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")
