# Direction B: the machine's record on one time axis

Status: a working prototype for David's choice between three directions (phase 1 of the interface overhaul; the audit and the three theses are in [the audit](../../2026-09-26-interface-audit.md)). Branch `claude/2026-09-26-ui-B`, built in the real dashboard code. Every screen here was rendered by that code over the synthetic fixture server (`docs/screens/fixtures/fixture-server.py`); nothing comes from a real machine.

## The thesis

The product is a record, and a record is read along time. There is one time range, and every source the machine keeps is a row on it: unplanned stops, hardware error reports in the System log, Kernel-WHEA reports, program faults, the System log by level, changes (updates, drivers, installers), processor load and Windows' reliability record. Picking a moment, a mark or a dragged stretch fills an inspector beside the chart with what each source returned near it, and the chart does not move. Coverage is drawn as prominently as data: where a source read and found nothing, the paper is plain; where it did not read, the paper is hatched and says "not read"; a reading that failed hatches its whole row and says so. Lining up empty rows is never presented as agreement, and the inspector says "near", never "because".

## What a person understands at a glance

Open the dashboard and the first screen answers "what happened, when, and what was read around it" in sentences before any chart ([timeline-1440](timeline-1440.png), [timeline-390](timeline-390.png)):

- No unplanned stop in the last seven days; the latest on record is 12 September, before this range.
- 40 hardware error reports in the System log, all on one day; the Kernel-WHEA channel, read separately, returned 3.
- 6 program faults in what was read; 60 System log records, 2 of them critical, and the log reaches back only two days.
- Changes and the reliability record could not be read; processor load has no stored samples.

Each sentence is led by the same mark the chart and the day list use for that source. The chart under it shows where each of those facts sits and, just as visibly, where each source did not look. At 30 days ([timeline-30d-1440](timeline-30d-1440.png)) the four stops appear with the stretch before 2 September hatched, because the System log the stops are read from starts there.

## The screens

| State | Desktop 1440x900 | Phone 390x844 |
| --- | --- | --- |
| Home, last 7 days | [timeline-1440](timeline-1440.png) | [timeline-390](timeline-390.png) |
| Home, last 30 days (and the whole page) | [timeline-30d-1440](timeline-30d-1440.png), [full](timeline-30d-1440-full.png) | [timeline-30d-390](timeline-30d-390.png), [full](timeline-30d-390-full.png) |
| A stop picked | [timeline-stop-1440](timeline-stop-1440.png) | [timeline-stop-390](timeline-stop-390.png) (the inspector as a sheet) |
| A moment picked at 24 h | [timeline-moment-1440](timeline-moment-1440.png) | [timeline-moment-390](timeline-moment-390.png) |
| System log | [record-1440](record-1440.png) | [record-390](record-390.png) |
| A critical record picked | [record-open-1440](record-open-1440.png) | [record-open-390](record-open-390.png) |
| The records around a moment | [record-moment-1440](record-moment-1440.png) | [record-moment-390](record-moment-390.png) (the sheet lowered to a bar) |
| Crashes, last 30 days | [crashes-30d-1440](crashes-30d-1440.png) | [crashes-30d-390](crashes-30d-390.png) |
| A stop picked in Crashes | [crashes-open-1440](crashes-open-1440.png) | [crashes-open-390](crashes-open-390.png) |
| The dark appearance (the instrument field) | [timeline-stop-dark-1440](timeline-stop-dark-1440.png) | [timeline-stop-dark-390](timeline-stop-dark-390.png) |

The captures were reduced to 256 colours to keep the repository light. To take them again, start the fixture server as `docs/screens/README.md` describes and run `node docs/screens/fixtures/capture-views.cjs timeline timeline-30d timeline-stop timeline-moment record record-open record-moment crashes-30d crashes-open` (`COLOR_SCHEME=dark` for the dark appearance; the harness gained that option and these states on this branch).

## Decisions and why

**One range, in the address.** `range=7d` for "the last seven days", `range=<from>/<to>` (ISO 8601 interval form) for a fixed stretch, beside the `moment` the address already carried. The five window vocabularies the audit found collapse into one control in one place (30 d, 7 d, 24 h, 6 h, 1 h, Earlier, Later, Read again or Back to now). A copied link opens the same stretch.

**Every source a row, and every row draws its reach.** A row's reach comes from its own reading's coverage section: `covered_from` and `covered_until` for the log readings, the retained System log for stops, the three change sources' common reach, the stored samples for processor load. Where Windows did not say how far back a source reaches, the reach starts at the oldest row returned, never at the start of the range, and the row says so. A bucket of unknown coverage (`null` in `storms` and `whea_reports`) is hatched, not drawn as zero. One rule decides "read" for the chart, the captions, the ledger, the day list and the inspector: a stretch was read when no unread gap in it is as long as one bucket, the smallest thing the chart can show. The words and the drawing cannot disagree because they are computed by the same function (`unreadGaps` in `dashboard/src/timeline.ts`).

**Events and samples mean different things when empty.** An empty stretch inside a log's reach means nothing was recorded; an empty stretch of processor load means nothing was measured. The row type carries that difference, so the hatch on processor load reads "not measured".

**A stop keeps all of its evidence times.** The last System record before the restart, Windows' own stop estimate and the next start are drawn together: dotted from the last record to the estimate (the time the stop happened is uncertain there), solid from the estimate to the next start (the machine was down). The estimate is the stop's one address for sorting, the day it is filed under and the moment in the link; the other times stay labelled. Crashes gives each stop its own small axis at true spacing, so a gap between the last record and the estimate is visible rather than averaged ([crashes-30d-1440](crashes-30d-1440.png)). A restart Windows announced without a returned start (2 September in the fixture) is kept, on its announcement time.

**Inspect without navigating.** Picking fills the inspector and nothing else moves: the chart keeps its range, the list keeps its place, focus stays on the chart so the arrow keys keep working. Only the actions that say where they go move anything ("The System log around this", "This stop in Crashes", "Zoom to 4 days around it", "The records around this one"). At rest the inspector is the coverage ledger (what each source read); in every state its first line says how many sources read the whole range, so no pick is read without its reach.

**Near, counted honestly.** The near window snaps outward to whole buckets, so the stretch the question names and the stretch the counts cover are the same. Each source answers separately, never as one time-sorted list, which would read as a story. An empty answer says whether the whole window was read ("None returned, and this whole window was read"); an unread one says why, in the reading's own terms. A standing line says "Near in time is not a cause."

**Scales that do not shout.** Rows that count the same thing share a scale (both hardware rows count reports), every scale has a floor of ten so one report is a short bar, and the top of each scale is printed with its unit ("35 per 6 h"). Reports whose own header says the error happened in an earlier Windows session are drawn hollow, so a burst filed at boot is not read as a burst after a stop. The live burst lead is shown only as a sentence, from a separate minute-bucket reading, because asking `storms` for the chart's hour buckets would change what "burst" means.

**Columns start at local midnight.** Hardware counts are asked for in hours once a range is two days or longer and summed into columns that start at local midnight, so a day in the day list holds that day's reports. Windows' reliability record is kept by UTC day and says so.

**The phone is the day list.** Below 720 px the chart gives way to the same marks by day, newest first, each day stating which sources did not read it; runs of quiet days with the same coverage fold into one row. The inspector becomes a bottom sheet with Escape and focus return; an action that moves the list lowers the sheet to a bar, so the result is visible and the pick is one tap away. The day list is also the chart's text alternative on a desk.

**Recording paper, and the instrument kept.** The light appearance is new: a warm off-white field, a ruled grid, rows in ink, one exhibit red for stops and one ochre for hardware error traffic, and no other colour with meaning. The hatch is neutral on every row, so coverage never borrows data's colours. The dark appearance is the existing instrument field with the same roles ([timeline-stop-dark-1440](timeline-stop-dark-1440.png)); the system preference chooses unless a person picks Paper or Instrument in the rail. DM Sans carries text and headings; JetBrains Mono is kept for times and exact values. The inspector is the one raised sheet; the rest is flat. Nothing moves at rest, and there is no animation to reduce.

**Keyboard, targets and reduced motion.** The plot is one slider: arrows move a cursor by a bucket (Shift for ten), Enter picks, Page Up and Page Down step through the marks, Escape clears. Every button in the new views and the rail is at least 44 px in both directions, checked in the browser at both widths; the level of a log record is shape and word together (a filled triangle, a filled square, an open triangle, an open ring), never colour alone.

**Navigation by the person's situation, with the old views behind the shell.** The rail groups views as Along time (Timeline, System log, Crashes, Hardware errors, Performance, Signals), The machine (Machine, Space, Diagnostics) and Handing on (Stack, Agents). "Record" becomes "System log", its true name, because the record is now the whole timeline. The eight views not rebuilt keep their old layout under the new tokens (legible, but still monospace-heavy and boxed; see [the audit's captures](../../audit-2026-09-26/) for what they were).

## What it asks of the API

The prototype composes the timeline in the browser from eight existing readings (`crash`, `storms` twice, `whea_reports`, `faults`, `events`, `changes`, `performance_history`, `reliability`). That proves the model and shows its limits: the System log row is 2,000 capped rows counted in the browser, not a count; every source ends at its own query time; each reading states coverage in its own shape. The ask is one `timeline` reading, served as a route and an MCP tool with the same envelope:

- **Parameters:** `from`, `to` (or a live span), `bucket_seconds`, and a time zone or explicit bucket edges so days align with local midnight.
- **One query instant** for every source, so all rows end at the same moment.
- **Per source:** outcome, reach as spans, `not_read` spans each with its reason (retention, cap, failure, not asked), and cap facts (`available`, `returned`, `truncated`). Coverage per bucket is three-valued (read, not read, unknown); null never means "from the start".
- **Counts per bucket:** System log by level without a row cap, under a time budget that stops early with honest partial coverage rather than running unbounded through PowerShell; faults by kind and application; WHEA reports with the previous-session split and severity.
- **Marks with exact references:** stops with every evidence time and the anchor named by the server, changes, critical records, fault groups; each mark carrying the reading and parameters that reproduce its rows, so an agent can follow a mark exactly as a person opens one.
- **Exact counts for an arbitrary window**, so "near" is answered for the window asked, not in whole buckets.
- **The summary's facts as structured fields**, so the person's sentences and the agent's summary are the same data rendered twice.
- Later, a comparison of two ranges (Honeycomb's BubbleUp) ranked by count change with coverage attached.

Several of these series exist today (`storms` and `whea_reports` already carry null-for-unknown buckets and previous-session columns; `events`, `faults` and `changes` already carry coverage); the ask is mostly composition, plus the uncapped log count, which is new work in the collector.

## What carrying it across all 17 views costs

The new code is about 2,500 lines (`dashboard/src/time.ts`, `timeline.ts`, `chart/`, `views/Timeline.tsx`, `views/SystemLog.tsx`, `views/Stops.tsx` and their styles) plus changes to the shell, store and tokens. The remaining views sort into three kinds of work, roughly largest first:

- **Become rows and inspector lenses (large).** Hardware errors (`Errors.tsx`, 799 lines: the live lead, the Kernel-WHEA timeline, per-stretch report previews become the hardware rows plus an inspector lens), Kernel reports, Performance and Process pressure (the sample row plus an inspector lens at a sample; the collection controls move behind a disclosure), Signals (leads with times become marks; the rest a list), Reliability history (the reliability row, with the stability index as its line), Changes near stop (already reused inside the stop inspector).
- **Retire (medium).** The old Record view (672 lines: its frame, the Application reports either side of a moment and the Kernel-WHEA side) and the old Crashes page (dump inventory and dump header detail have no place yet in this prototype; they would move into the stop inspector and a dump list). Their rows in `docs/CLAIMS.md` describe code this branch no longer mounts and would be rewritten; the view-list row is already updated so the suite passes. The browser checks in `docs/screens/fixtures/check-*.cjs` that target the old Record and Crashes would be rewritten against the new views.
- **Restyle only (small).** Machine, Machine overview, Space, the PCIe and memory maps, Diagnostics, Stack and Agents: they are places, not times. The token remap already makes them legible on paper; they need the sans headings, fewer boxes and the shared controls.

The agent side (the audit's sixth cause) is not solved by this direction beyond the API ask; Stack and Agents would still need the work direction C describes.

## Honest weaknesses

- **It is an expert's instrument.** The sentences carry the first visit; a newcomer who skips them sees stripes. The sentences are the product's answer, and they are only as good as the counts beneath them.
- **Nearness will still be read as cause** by some people, however the inspector is composed.
- **On this fixture the default seven days holds no stop**, because the synthetic stops are two weeks older than the synthetic log. The home says so plainly, but David has to press 30 d to see the stops.
- **The System log row is capped.** On a busy machine 2,000 rows may be a few hours, so the log row will often be mostly hatched until the uncapped count exists. The home also takes eight readings on every range change; that cost was not measured on the Windows host.
- **Three stop times collapse at coarse zoom.** At 30 days, seconds apart are one pixel; the list, the Crashes axis and the inspector carry the times, and zooming separates them.
- **The log's stacked density is the most verdict-shaped mark on the chart.** Buddy argued for cutting it from the home or splitting it into per-level tick rows; it stayed, because it is the audit's "strongest summary in the app" carried onto the axis, and the per-row scale and floor keep it quiet. This is a judgment David may reverse.
- **Things this prototype does not keep:** a reload keeps a picked moment but turns a picked mark into a moment and drops a dragged stretch; dumps are reachable only through a stop; the phone sheet returns focus to what opened it but does not trap focus; the storms live lead shows only as a sentence; the reliability row and changes row have been seen only failing, because the fixture answers them that way.
- **Not verified:** on the Windows host with real data, with a screen reader, on a touch tablet (the chart takes a pointer drag with `touch-action: pan-y`), and against the old views' interaction checks.

## Challenge and review

- **Buddy (Muse)** critiqued the plan before the code existed. What changed: a coverage line pinned to the top of the inspector in every state; unread rows in "near" explain the reading attempt; empty rows say whether the whole window was read; words inside wide hatches; the stop anchored on Windows' estimate; the stop's sentence scoped to the part read. What did not: cutting the log density and processor load from the home, and a seven-day window forced on the phone (the shared range is the model; the phone opens on seven days anyway).
- **GPT-6 Sol** was asked to challenge the built direction and was refused before submission (Multithread reported its hooks were not ready for this checkout). **An independent Opus 5.5 review** stood in and read the code, the API contract and the captures. It found that the chart drew coverage it could not vouch for as "read, nothing returned" (a null reach start became the range start; the changes reading's per-source coverage was read as one object; unknown buckets inside the reach showed as paper), five different tolerances for "whole range read", WHEA reports filed at boot drawn as if new, days misattributed across UTC-aligned buckets, a live range that never re-read, a drag that could stick after a cancelled touch, and an overstating summary. All of those are fixed on this branch as described above; its API corrections are in the ask.
