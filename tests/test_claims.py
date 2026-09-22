"""The prose about the catalog, read back from the catalog.

A count written into a sentence is a claim about the code, and the code is the place it can be
checked against. These tests read the counts out of ``docs/CLAIMS.md``, the reading names out of
``README.md`` and the view list out of the dashboard's own source, and fail on the day one of them
stops being true — which is the day a reading or a tool is added, not the day somebody notices.

When one of these fails, the code is right and the sentence is stale: change the sentence.
"""

from __future__ import annotations

import re
from pathlib import Path

import sentinel.readings  # noqa: F401  importing the package is what registers the catalog
from sentinel.mcp_server import tools
from sentinel.reading import REGISTRY
from sentinel.stack import PRESET_PROMPTS

ROOT = Path(__file__).resolve().parent.parent

_UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}

#: The numbers this project writes out in words, both ways round. The prose says "twenty-two"; the
#: registry says 22; a failure has to be able to name the word the sentence should now use.
NUMBERS: dict[str, int] = {word: value for value, word in enumerate(_UNITS)}
for _word, _ten in _TENS.items():
    NUMBERS[_word] = _ten
    NUMBERS.update({f"{_word}-{unit}": _ten + n for n, unit in enumerate(_UNITS[1:10], start=1)})
IN_WORDS = {value: word for word, value in NUMBERS.items()}


def line_with(path: str, anchor: str) -> str:
    """The one line of a file that carries a claim, found by the part of it that is not the number."""
    text = (ROOT / path).read_text(encoding="utf-8")
    found = [line for line in text.splitlines() if anchor in line]
    assert len(found) == 1, f"{path}: expected exactly one line containing {anchor!r}, found {len(found)}"
    return found[0]


def counted(line: str, pattern: str) -> int:
    """The number a sentence states, as a number."""
    match = re.search(pattern, line, re.I)
    assert match, f"no {pattern!r} in this line, so the claim it carried has been reworded:\n    {line.strip()}"
    word = match.group(1).lower()
    assert word in NUMBERS, f"{word!r} is not a number this file knows how to read"
    return NUMBERS[word]


def stale(path: str, line: str, now: int) -> str:
    return f"{path} has gone stale: the code now says {now} ({IN_WORDS.get(now, now)}). The line to change:\n    {line.strip()}"


def test_the_claims_note_counts_the_readings_the_registry_holds():
    line = line_with("docs/CLAIMS.md", "(the catalog)")
    assert counted(line, r"\|\s*([a-z-]+) readings\b") == len(REGISTRY), stale("docs/CLAIMS.md", line, len(REGISTRY))


def test_the_claims_note_counts_the_tools_the_mcp_server_lists():
    """The total, the readings inside it, and the families the sentence breaks the rest into.

    The breakdown is read as whatever the sentence says it is — ``eight stack tools and two capture
    tools`` — so a family added to the projection can be named in prose without rewriting this test,
    but cannot be left out of the arithmetic.
    """
    line = line_with("docs/CLAIMS.md", "MCP tools:")
    head, _, breakdown = line.partition(":")
    every, readings = len(tools()), len(REGISTRY)
    assert counted(head, r"([a-z-]+) MCP tools\b") == every, stale("docs/CLAIMS.md", line, every)
    assert counted(breakdown, r"([a-z-]+) readings\b") == readings, stale("docs/CLAIMS.md", line, readings)
    families = [NUMBERS[word] for word in re.findall(r"\b([a-z-]+) [a-z]+ tools\b", breakdown.lower()) if word in NUMBERS]
    assert sum(families) + readings == every, (
        f"docs/CLAIMS.md breaks {every} tools into {readings} readings and {families}, which do not add up. "
        f"The server lists {sorted(t.name for t in tools() if t.name not in REGISTRY and t.name.replace('_', '.') not in REGISTRY)} beside the readings."
    )


def test_the_claims_note_counts_the_prompt_presets_the_stack_seeds():
    line = line_with("docs/CLAIMS.md", "prompt presets by name")
    assert counted(line, r"\|\s*([a-z-]+) prompt presets\b") == len(PRESET_PROMPTS), stale("docs/CLAIMS.md", line, len(PRESET_PROMPTS))


def test_the_claims_note_names_the_views_the_dashboard_has():
    """The count and the names both: a view renamed in the source is drift a count would not catch."""
    source = (ROOT / "dashboard" / "src" / "store.ts").read_text(encoding="utf-8")
    views = re.findall(r"\{\s*id:\s*'[a-z]+',\s*label:\s*'([^']+)'(?:,\s*group:\s*'[^']+')?\s*\}", source)
    assert views, "dashboard/src/store.ts no longer declares VIEWS in the shape this test reads"
    line = line_with("docs/CLAIMS.md", "views: ")
    assert counted(line, r"\|\s*([a-z-]+) views\b") == len(views), stale("docs/CLAIMS.md", line, len(views))
    named = [v.strip() for v in line.split("views: ", 1)[1].split("|", 1)[0].split(",")]
    assert named == views, f"docs/CLAIMS.md names the views as {named}; dashboard/src/store.ts has {views}"


def test_the_readme_names_only_readings_that_exist():
    """Every reading the front page names is one the catalog registers, whatever it was renamed to."""
    section = re.search(r"^## What it reads$(.+?)^## ", (ROOT / "README.md").read_text(encoding="utf-8"), re.S | re.M)
    assert section, "README.md no longer has a 'What it reads' section"
    named: list[str] = []
    for row in section.group(1).splitlines():
        if not row.startswith("|"):
            continue
        # `.gpu` and its kin are the table's shorthand for the `hardware.` family the row just named.
        named += [f"hardware{n}" if n.startswith(".") else n for n in re.findall(r"`([a-z][a-z.]*)`", row.split("|")[1])]
    assert named, "the 'What it reads' table names no readings, which is drift of its own"
    unknown = [n for n in named if n not in REGISTRY]
    assert not unknown, f"README.md names {unknown}, which the catalog does not register; the readings are {sorted(REGISTRY)}"
