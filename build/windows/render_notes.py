"""Render release notes from the tagged changelog entry and evergreen install text."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def render_notes(template: str, changelog: str, version: str, tag: str, sha256: str) -> str:
    if tag not in (f"v{version}", "main") or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None:
        raise ValueError("the release tag or executable SHA-256 is invalid")
    headings = list(re.finditer(r"^## \[([^\]]+)\] - [^\r\n]+$", changelog, re.MULTILINE))
    matches = [index for index, heading in enumerate(headings) if heading.group(1) == version]
    if len(matches) != 1:
        raise ValueError(f"CHANGELOG.md must contain exactly one entry for {version}")
    index = matches[0]
    end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
    changes = changelog[headings[index].end():end].strip()
    if not changes:
        raise ValueError(f"CHANGELOG.md has no changes for {version}")
    changes = re.sub(
        r"\]\((docs/[^)]+)\)",
        lambda match: f"](https://github.com/SuperDuperDave/system-sentinel/blob/{tag}/{match.group(1)})",
        changes,
    )
    notes = re.sub(r"^\s*<!--.*?-->\s*", "", template, count=1, flags=re.DOTALL)
    for marker, value in {"CHANGES": changes, "VERSION": version, "TAG": tag, "SHA256": sha256.lower()}.items():
        token = "{{" + marker + "}}"
        if marker == "CHANGES" and notes.count(token) != 1:
            raise ValueError("release notes template must contain exactly one {{CHANGES}}")
        if marker != "CHANGES" and token not in notes:
            raise ValueError(f"release notes template must contain {token}")
        notes = notes.replace(token, value)
    return notes.rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    result = render_notes(
        (root / "build/windows/notes.md").read_text(encoding="utf-8"),
        (root / "CHANGELOG.md").read_text(encoding="utf-8"),
        args.version, args.tag, args.sha256,
    )
    args.output.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()
