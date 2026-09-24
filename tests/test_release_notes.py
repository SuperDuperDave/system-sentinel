"""The draft must describe the tagged version, not an older hand-written feature list."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from sentinel import __version__

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("render_notes", ROOT / "build/windows/render_notes.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_release_notes_take_only_the_current_changelog_entry() -> None:
    rendered = MODULE.render_notes(
        (ROOT / "build/windows/notes.md").read_text(encoding="utf-8"),
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        __version__, f"v{__version__}", "a" * 64,
    )
    assert f"## New in {__version__}" in rendered
    assert "{{" not in rendered
    assert "a" * 64 in rendered


def test_release_notes_exclude_older_changes_and_link_to_the_tag() -> None:
    template = "<!-- internal -->\n## New in {{VERSION}}\n\n{{CHANGES}}\n\n{{TAG}} {{SHA256}}"
    changelog = "# Changelog\n\n## [2.0.0] - 2026-09-24\n\n### Changed\n- New feature; see [API](docs/API.md).\n\n## [1.9.0] - 2026-09-23\n\n- Old feature.\n"
    rendered = MODULE.render_notes(template, changelog, "2.0.0", "v2.0.0", "a" * 64)
    assert "New feature" in rendered and "Old feature" not in rendered
    assert "https://github.com/SuperDuperDave/system-sentinel/blob/v2.0.0/docs/API.md" in rendered


@pytest.mark.parametrize("changelog", ["# Changelog\n", "## [1.8.0] - 2026-09-24\n\n## [1.7.0] - 2026-09-24\n"])
def test_missing_or_empty_changelog_entry_fails(changelog: str) -> None:
    with pytest.raises(ValueError, match="entry|no changes"):
        MODULE.render_notes("{{CHANGES}} {{VERSION}} {{TAG}} {{SHA256}}", changelog, "1.8.0", "v1.8.0", "a" * 64)
