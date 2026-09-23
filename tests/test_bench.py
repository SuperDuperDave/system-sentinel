"""The bench: the three numbers are right, a failure is never a time, and the document says nothing private."""

import asyncio
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest

import sentinel.bridge
from sentinel import bench, cli, readings  # noqa: F401 - importing readings fills the registry
from sentinel.bench import Attempt, Report, Row
from sentinel.bridge import BridgeResult
from sentinel.reading import from_bridge
from tests.conftest import FakeBridge, LogBridge

# The shapes CI greps for in tracked files, written out here rather than imported: a guard that
# checked itself with its own pattern would pass whatever it was changed to.
CI_GREP = re.compile(r"[a-z0-9-]+\.ts\.net|C:\\Users\\[A-Za-z]|/home/[a-z][a-z0-9_-]*/")


@pytest.fixture(autouse=True)
def transport_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The transport lives in a variable and in a module attribute, and both are global.

    Every test here is allowed to set them because monkeypatch puts them back: a bench test that
    left the pool switched off would silently change what every later test measured.
    """
    monkeypatch.setenv(bench.SESSIONS_ENV, "")
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", sentinel.bridge.DEFAULT_POOL_SIZE)


def attempts(*pairs: tuple[str, int | None]) -> tuple[Attempt, ...]:
    return tuple(Attempt(outcome, took_ms) for outcome, took_ms in pairs)


def a_report(rows: tuple[Row, ...], *, transport: str = "session", machine: str | None = "Microsoft Windows 11 Pro, build 26100, 64-bit") -> Report:
    return Report(
        transport=transport,
        runs=3,
        taken_on="2026-09-21",
        version="1.1.0",
        rows=rows,
        floor=Row(bench.FLOOR_NAME, attempts(("ok", 9), ("ok", 11), ("ok", 10))),
        machine=machine,
        pool={"transport": transport, "size": 4 if transport == "session" else 0, "answered": 70, "fell_back": 0},
    )


# ---------------------------------------------------------------------------
# The statistics
# ---------------------------------------------------------------------------


def test_the_percentile_is_nearest_rank_and_interpolates_nothing():
    assert bench.percentile(list(range(1, 101)), 0.95) == 95
    assert bench.percentile([10, 20], 0.95) == 20
    assert bench.percentile([7], 0.95) == 7
    with pytest.raises(ValueError):
        bench.percentile([], 0.95)


def test_the_three_numbers_come_from_the_observed_runs():
    row = Row("events", attempts(("ok", 40), ("ok", 10), ("ok", 30), ("ok", 20)))
    assert (row.min_ms, row.median_ms, row.p95_ms) == (10, 25, 40)
    assert row.observed == 4 and row.outcome == "ok"


def test_an_envelope_that_was_not_observed_is_an_outcome_and_never_a_time():
    slow_failure = from_bridge("events", {}, "q", BridgeResult("failed", took_ms=900, error="boom"))
    row = Row(
        "events",
        (
            Attempt.of(from_bridge("events", {}, "q", BridgeResult("ok", items=[{"Id": 1}], took_ms=10))),
            Attempt.of(slow_failure),
            Attempt.of(from_bridge("events", {}, "q", BridgeResult("empty", took_ms=20))),
        ),
    )
    assert slow_failure.took_ms == 900, "the envelope still carries what the failed call cost"
    assert row.times == [10, 20], "the failure's 900 ms is not a measurement of anything"
    assert (row.min_ms, row.median_ms, row.p95_ms) == (10, 15, 20)
    assert row.observed == 3 - 1 and row.outcome == "ok, failed, empty"


def test_a_reading_never_observed_has_no_numbers_at_all():
    row = Row("whea", attempts(("denied", None), ("denied", None)))
    assert row.times == [] and row.observed == 0
    assert (row.min_ms, row.median_ms, row.p95_ms) == (None, None, None)
    assert row.outcome == "denied"
    assert "| whea | 2 | — | — | — | denied |" in bench.table([row])


def test_a_reading_the_catalog_could_not_take_is_kept_apart_from_the_six_outcomes():
    row = Row("record", (), note="could not be taken: parameter 'before' is required")
    assert row.outcome == "not taken" and row.times == []
    assert row.to_dict()["note"].startswith("could not be taken")
    assert "| record | 0 | — | — | — | not taken |" in bench.table([row])


# ---------------------------------------------------------------------------
# The selection
# ---------------------------------------------------------------------------


def test_nothing_named_measures_each_reading_that_needs_no_selection():
    from sentinel.reading import REGISTRY

    automatic = [name for name, spec in REGISTRY.items() if not spec.requires_selection]
    assert bench.select("") == automatic
    assert bench.select(None) == automatic
    assert bench.select("whea_record") == ["whea_record"]


def test_the_narrowing_flag_selects_in_catalog_order():
    from sentinel.reading import REGISTRY

    order = [name for name in REGISTRY if name in ("system", "events", "health")]
    assert bench.select("events, health,system") == order
    assert bench.select(["health", "health"]) == ["health"]


def test_an_unknown_reading_is_refused_rather_than_skipped():
    with pytest.raises(ValueError, match="unknown reading"):
        bench.select("events,nosuchreading")


# ---------------------------------------------------------------------------
# The transport
# ---------------------------------------------------------------------------


def test_the_transport_is_set_where_the_pool_actually_reads_it(monkeypatch: pytest.MonkeyPatch):
    """The variable alone would not do it: the pool reads it once, at import, before any flag."""
    bench.apply_transport("one-shot")
    assert os.environ[bench.SESSIONS_ENV] == "0" and sentinel.bridge.POOL_SIZE == 0
    bench.apply_transport("session")
    assert os.environ[bench.SESSIONS_ENV] == str(sentinel.bridge.DEFAULT_POOL_SIZE)
    assert sentinel.bridge.POOL_SIZE == sentinel.bridge.DEFAULT_POOL_SIZE
    monkeypatch.setenv(bench.SESSIONS_ENV, "1")
    bench.apply_transport("session")
    assert sentinel.bridge.POOL_SIZE == 1, "a pool of one is a pool: an operator's own size stands"
    with pytest.raises(ValueError):
        bench.apply_transport("carrier-pigeon")


def test_switching_transport_ends_the_sessions_already_running(monkeypatch: pytest.MonkeyPatch):
    reset_sizes: list[int] = []
    monkeypatch.setattr(sentinel.bridge, "reset_sessions", lambda size: reset_sizes.append(size))
    bench.apply_transport("one-shot")
    assert reset_sizes == [0], "a pool of four cannot be left answering a one-shot run's questions"


# ---------------------------------------------------------------------------
# Taking the measurement
# ---------------------------------------------------------------------------


def test_every_reading_is_taken_the_named_number_of_times_after_one_discarded_question(monkeypatch: pytest.MonkeyPatch):
    fake = FakeBridge()
    report = asyncio.run(bench.measure(fake, names=["health", "system"], runs=2, transport="one-shot"))

    assert [row.reading for row in report.rows] == ["health", "system"]
    assert all(len(row.attempts) == 2 for row in report.rows)
    assert report.runs == 2 and report.transport == "one-shot" and report.observed
    floor_questions = [script for script in fake.scripts if script == bench.FLOOR_SCRIPT]
    assert len(floor_questions) == 3, "two measured, one discarded so the first reading is not charged for the session"
    assert report.floor is not None and len(report.floor.attempts) == 2
    assert report.pool is not None and report.pool["transport"] == "one-shot", "read back from the bridge, not from the flag"


def test_a_reading_that_raises_is_reported_and_does_not_end_the_bench(monkeypatch: pytest.MonkeyPatch):
    real_take = bench.take

    async def refuse(name, bridge, params=None):
        if name == "health":
            raise ValueError("parameter 'before' is required")
        return await real_take(name, bridge, params)

    monkeypatch.setattr(bench, "take", refuse)
    report = asyncio.run(bench.measure(FakeBridge(), names=["health", "system"], runs=2, transport="one-shot"))
    refused, taken = report.rows
    assert refused.outcome == "not taken" and refused.note is not None and refused.times == []
    assert taken.observed == 2, "the bench kept going"


def test_the_moment_a_record_needs_is_supplied_so_the_reading_can_be_taken():
    record = {"RecordId": 1, "Id": 41, "TimeCreated": "2026-09-20T00:00:00.000Z"}
    report = asyncio.run(bench.measure(LogBridge(BridgeResult("ok", items=[record])), names=["record"], runs=1, transport="one-shot"))
    assert report.rows[0].outcome == "ok" and report.rows[0].note is None


def test_an_explicit_selection_reading_has_no_synthetic_failure_or_measurement():
    fake = FakeBridge()
    report = asyncio.run(bench.measure(fake, names=["whea_record", "system"], runs=1, transport="one-shot"))
    selected, automatic = report.rows
    assert selected.outcome == "not taken" and selected.note == "requires an exact selection; this bench has no reference to measure"
    assert automatic.observed == 1
    assert not any("EventRecordID=" in script for script in fake.scripts)


def test_the_json_carries_the_samples_beside_the_numbers_derived_from_them():
    row = Row("events", attempts(("ok", 10), ("failed", None)))
    body = a_report((row,)).to_dict()
    assert body["readings"][0]["samples"] == [{"outcome": "ok", "took_ms": 10}, {"outcome": "failed", "took_ms": None}]
    assert body["readings"][0]["median_ms"] == 10 and body["readings"][0]["observed"] == 1
    assert body["transport"] == "session" and body["taken_on"] == "2026-09-21"
    assert body["floor"]["median_ms"] == 10


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def test_the_section_carries_the_date_the_transport_the_floor_and_a_row_per_reading():
    text = bench.section(a_report((Row("events", attempts(("ok", 12), ("ok", 18))), Row("whea", attempts(("denied", None),)))))
    assert text.startswith("## Session transport")
    assert "**2026-09-21**" in text and "Microsoft Windows 11 Pro" in text
    assert "Transport: **session**, a pool of 4; 70 question(s) answered through sessions, 0 fell back to a launch." in text
    assert "Bridge floor (a script that reads nothing): min 9 ms, median 10 ms, p95 11 ms over 3 run(s)." in text
    assert "| events | 2 | 12 | 15 | 18 | ok |" in text
    assert "| whea | 1 | — | — | — | denied |" in text


def test_a_question_that_fell_back_to_a_launch_is_counted_on_the_line():
    measured = a_report((Row("events", attempts(("ok", 12),)),))
    report = replace(measured, pool={**(measured.pool or {}), "fell_back": 3})
    assert "3 fell back to a launch." in bench.transport_line(report)


def test_the_line_names_the_transport_that_carried_the_questions_not_the_one_asked_for():
    """A session that would not start is a one-shot run wearing the other label, and the table
    would be read as evidence either way."""
    measured = a_report((Row("events", attempts(("ok", 200),)),))
    report = replace(measured, pool={**(measured.pool or {}), "transport": "one-shot"})
    assert bench.transport_line(report) == "Transport: **one-shot**, a process per question — the bridge reported `one-shot`."


def test_a_machine_that_did_not_answer_is_said_so_rather_than_left_blank():
    text = bench.section(a_report((Row("events", attempts(("ok", 12),)),), machine=None))
    assert "Machine: not stated" in text


def test_a_run_rewrites_its_own_transport_and_keeps_the_other_one():
    first = bench.document(a_report((Row("events", attempts(("ok", 200),)),), transport="one-shot"))
    second = bench.document(a_report((Row("events", attempts(("ok", 12),)),), transport="session"), first)
    assert "| events | 1 | 200 | 200 | 200 | ok |" in second, "the one-shot section survived the session run"
    assert "| events | 1 | 12 | 12 | 12 | ok |" in second
    assert second.index("## Session transport") < second.index("## One-shot transport")
    assert second.count("## Session transport") == 1 and second.count("## One-shot transport") == 1
    assert "_Not measured yet" not in second


def test_a_document_with_nothing_measured_shows_the_table_and_the_command():
    empty = bench.document()
    assert empty.count(bench.TABLE_HEAD) == 2
    assert "system-sentinel bench --transport session --out docs/measurements/latency.md" in empty
    assert "_Not measured yet. Run the command above with `--transport one-shot` on the machine._" in empty


def test_the_committed_document_is_this_modules_prose_around_whatever_was_measured():
    """The document in the repository carries this module's own preamble, so a run on the machine
    is a diff of numbers rather than a diff of prose. Its sections are not asserted against the
    placeholder: once a machine has been measured they hold that machine's numbers, which is the
    document doing its job."""
    committed = (Path(__file__).resolve().parent.parent / bench.DOC_PATH).read_text(encoding="utf-8")
    preamble = bench.document().split("##", 1)[0]
    assert committed.startswith(preamble)
    assert CI_GREP.search(committed) is None  # whatever was measured, it names no machine and no person


# ---------------------------------------------------------------------------
# What may never reach the document
# ---------------------------------------------------------------------------


def test_nothing_in_the_rendered_document_matches_the_privacy_patterns():
    rows = tuple(Row(name, attempts(("ok", 11), ("ok", 12), ("empty", 9))) for name in bench.select(""))
    text = bench.document(a_report(rows))
    assert CI_GREP.search(text) is None
    assert CI_GREP.search(bench.as_json(a_report(rows))) is None
    assert CI_GREP.search(bench.document()) is None


@pytest.mark.parametrize("planted", ["workbench.ts.net", r"C:\Users\someone\AppData", "/home/someone/repos"])
def test_a_host_a_user_or_a_path_is_refused_rather_than_written(tmp_path: Path, planted: str):
    report = a_report((Row("events", attempts(("ok", 12),)),), machine=planted)
    assert CI_GREP.search(planted) is not None, "the planted string is one CI would catch"
    with pytest.raises(bench.Leak):
        bench.check_clean(bench.section(report))
    path = tmp_path / "latency.md"
    with pytest.raises(bench.Leak):
        bench.write(report, path)
    assert not path.exists(), "a document that would have leaked was not written at all"


def test_the_machine_shape_takes_only_general_terms_from_the_system_reading():
    snapshot = {
        "os_caption": "Microsoft Windows 11 Pro",
        "os_version": "10.0.26100",
        "os_build": 26100,
        "architecture": "64-bit",
        "boot_time": "2026-09-20T09:00:00Z",
        "uptime_seconds": 90000,
    }
    reading = from_bridge("system", {}, "q", BridgeResult("ok", items=[snapshot]), section="snapshot", shape="object")
    assert bench.machine_shape(reading) == "Microsoft Windows 11 Pro, build 26100, 64-bit"


def test_a_field_that_carries_an_identifier_drops_out_of_the_shape():
    for value in (r"Windows on C:\Users\someone", "/home/someone/build", "WORKBENCH-01\\someone", "x" * 80):
        snapshot = {"os_caption": value, "os_build": 26100, "architecture": "64-bit"}
        reading = from_bridge("system", {}, "q", BridgeResult("ok", items=[snapshot]), section="snapshot", shape="object")
        assert bench.machine_shape(reading) == "build 26100, 64-bit"


def test_a_system_reading_that_did_not_answer_yields_no_shape():
    assert bench.machine_shape(None) is None
    assert bench.machine_shape(from_bridge("system", {}, "q", BridgeResult("failed", error="boom"))) is None


# ---------------------------------------------------------------------------
# The subcommand
# ---------------------------------------------------------------------------


def test_the_subcommand_narrows_writes_and_merges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End to end through `main`, with a bridge that never touches a machine."""
    monkeypatch.setattr(cli, "Bridge", _FakeLocator)
    out = tmp_path / "latency.md"

    assert cli.main(["bench", "--readings", "health,system", "--runs", "2", "--transport", "one-shot", "--out", str(out)]) == 0
    written = out.read_text(encoding="utf-8")
    assert "## One-shot transport" in written and "| health | 2 |" in written and "| events |" not in written
    assert "_Not measured yet. Run the command above with `--transport session` on the machine._" in written

    assert cli.main(["bench", "--readings", "health", "--runs", "1", "--transport", "session", "--out", str(out)]) == 0
    merged = out.read_text(encoding="utf-8")
    assert merged.count("| health | 2 |") == 1 and "| health | 1 |" in merged, "both transports are in one document"
    assert CI_GREP.search(merged) is None


def test_the_subcommand_refuses_a_bad_selection_and_a_run_count_below_one(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(cli, "Bridge", _FakeLocator)
    assert cli.main(["bench", "--readings", "nosuchreading"]) == 2
    assert cli.main(["bench", "--runs", "0"]) == 2
    assert "unknown reading" in capsys.readouterr().err


def test_the_subcommand_prints_json_for_a_machine(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    import json

    monkeypatch.setattr(cli, "Bridge", _FakeLocator)
    assert cli.main(["bench", "--readings", "health", "--runs", "1", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["tool"] == "system-sentinel" and body["readings"][0]["reading"] == "health"
    assert body["readings"][0]["samples"][0]["outcome"] == "ok"


def test_the_subcommand_exits_non_zero_when_the_machine_was_never_observed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "Bridge", _FakeSilentLocator)
    assert cli.main(["bench", "--readings", "health", "--runs", "1"]) == 1


class _FakeLocator:
    @staticmethod
    def locate() -> FakeBridge:
        return FakeBridge()


class _FakeSilentLocator:
    @staticmethod
    def locate() -> FakeBridge:
        return FakeBridge(result=BridgeResult("unavailable", error="powershell.exe was not found"))
