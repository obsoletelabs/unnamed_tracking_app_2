"""The central environment-variable page must list every registry variable, visible or not."""

from pathlib import Path

from src.core.config_registry import CONFIG_REGISTRY

PAGE = Path(__file__).parents[2] / "wiki" / "docs" / "setup" / "environment-variables.md"


def test_every_registry_variable_is_listed_on_the_central_page() -> None:
    page = PAGE.read_text(encoding="utf-8")
    missing = [spec.name for spec in CONFIG_REGISTRY if f"`{spec.name}`" not in page]
    assert not missing, f"Add these to {PAGE.name}: {missing}"
