# Direction A: Situations

The machine answered by what the person came with. A working prototype in the real dashboard, on branch `claude/2026-09-26-ui-A`, built over the synthetic fixture server. Nothing on these screens came from a real machine.

Read the [interface audit](../../2026-09-26-interface-audit.md) first; this is its direction A, built.

## The thesis

People arrive with a complaint, not a reading: it stopped or restarted, a program crashed, it feels slow, the disk is filling up, the hardware reported errors, I want my agent to look. The home is those six doors. Each door carries one observed fact with its time and the reading it came from ("15 days ago, since the last unplanned stop, by Windows' estimate · `crash` · 5 stops returned"), never a score and never "fine". Behind a door is a fixed composition of existing readings in a set order, and the same composition, with the page's values filled in, is what an agent is handed. The person's door and the agent's call are one object, defined once in [`situations.ts`](../../../../dashboard/src/situations.ts).

The composition behind the stop door is the order the project's own crash procedure already reads a stop in: the stop, what the System log held before the next start, what changed in the week before Windows' stop estimate, and hardware error reports from an hour before the stop to an hour after the start. The home is the same shape as the diagnostic sweep's closing evidence block, where every line names the reading it came from. The doors were found in the product, not invented for it.

## What a person understands at a glance now

| Question from the audit | Before | Now |
| --- | --- | --- |
| Where am I? | Record, a System-log browser, grouped under *Evidence / Interpret / Carry* | A home that asks *Start with what you noticed*, and a rail of doors in the person's words above a short list of places |
| Is there something to look at? | Nowhere on the first screen | Each door states its fact and when; a door with a stop or reports newer than the person's last visit to it says **New stop** or **New reports**, in word, glyph and hue |
| What changed? | No view compared now with before | The mark means exactly that: newer than when you last opened this door here. Opening the door acknowledges it ([home](home-1440.png), then [home-after](home-after-1440.png)) |
| What should I do? | Instrument verbs (*Take again*, *Method*, *Basis*) | Numbered steps that each answer a question, then *When this evidence runs out* with the further readings |
| What does my agent see? | A setup page | *Your agent can ask for exactly this* under every situation: the same calls, in order, with the chosen stop's times filled in, copyable |

## Screens

All at 1440x900 and 390x844 (`-1440`, `-390`); `-full` is the whole page. Reduced to 256 colours.

- [home-1440](home-1440.png), [home-390-full](home-390-full.png): the six doors on a first visit, every door with a record marked new.
- [home-after-1440](home-after-1440.png): the same home after opening the stop door. Its mark is acknowledged; its fact stands.
- [home-gaps-1440](home-gaps-1440.png), [home-gaps-390](home-gaps-390.png): every outcome shape at once, from `SENTINEL_FIXTURE_HOME_GAPS=1`: none returned (stops), timed out (programs), refused (slow), failed (disk), observed (hardware), none returned (handoff).
- [stopped-1440](stopped-1440.png), [stopped-1440-full](stopped-1440-full.png), [stopped-390-full](stopped-390-full.png): the stop situation, newest stop composed on arrival.
- [stopped-older-1440](stopped-older-1440.png): another stop chosen; the list stays, the composition changes.
- [stopped-changes-1440](stopped-changes-1440.png): step 3 read on request. The fixture answers `changes` as failed, and the step says so in its own shape.
- [programs-1440](programs-1440.png), [programs-open-1440-full](programs-open-1440-full.png): the program situation with one report open in place.
- [record-1440](record-1440.png), [record-open-1440-full](record-open-1440-full.png): the System log restyled; a row open with the record before it.
- [nav-open-390](nav-open-390.png): the phone's door list, each door with its fact.

## The decisions, and why

**Doors in the person's words, in a fixed order.** A door names a complaint, never a cause, so a person who chose the wrong one loses nothing: every door is one step from every other, in the rail on a desk and under *All doors* on a phone. The order never changes with the data, so a door is always where it was.

**The rail keeps each door's fact.** The glance survives on every page: *Stopped or restarted 15 d*, *Disk filling up 233 GB*. Every persistent pixel in the rail does a job (orientation and memory), which the old rail's method groups did not.

**Two channels that never borrow from each other.** Shape and word carry what Sentinel knows about a reading: observed (filled dot), none returned (ring), not asked yet (dotted ring), not reached or timed out (dashed ring), refused (ring with a bar), failed (slashed ring). Each has its own word; a door that did not read gives up its surface and shows an outline, so an unanswered question can no longer wear an answer's face. Hue carries attention only, and only beside its glyph and word: coral triangle for a new stop, amber diamond for new reports. Windows' event levels lost their hue for the same reason: a level is Windows' label, not something the person has yet to see. Levels are now three small bars filled by rank (Critical three, Error two, Warning one, Information none), a shape family of their own, so a level can never be mistaken for an outcome or a mark, and Error and Information no longer differ by the size of a dot.

**Attention means new since you last looked, not a verdict.** The first build marked any stop or report from the last 30 days. Buddy's challenge was right that a cutoff the evidence cannot see is a verdict wearing attention's clothes, and that a permanent mark on every page teaches a person to ignore it. The rule now: a door is marked when its newest stop or report is newer than the one it pointed at the last time this person opened that door on this browser. Opening it acknowledges it, the way a clinical monitor's alarm is acknowledged. Age is always in words. Slow and disk never take a mark, because Sentinel has no normal range for them. The slow door also gives up the big number: processor load at the moment of reading includes the dashboard itself, so it is said at the size of a sentence.

**Disclosure runs in the identity note's order.** Question, then finding, then exact evidence, then method. A step says what it found with its outcome mark, names its reading in the agent's vocabulary (`crash`, `record`, `whea_window`) and its class in words (*composed by Sentinel*, *as Windows returned it*), keeps the rule one disclosure down, and puts *taken 15:23 · 9 ms · Take again · Method* after the finding. The uppercase class badges are gone; the class is still on every step.

**Choosing a stop inspects; it does not navigate.** The stop list stays put while the composition beside it changes. The newest stop is composed on arrival so the page answers before it is asked; a stop the person chose stays chosen across retakes, and a chosen stop that a retake no longer returns is said to be missing rather than silently replaced. `changes` takes seconds on a real machine, so step 3 waits in the *not asked yet* shape with one button.

**The stop's evidence times on one line, placed by when each was written.** The last System record, Windows' stop estimate and the next start are drawn proportionally, so a last record written after the estimate shows as that, not tidied away. The caption says the times sit near each other and do not establish a cause.

**A failed retake never reads as current.** When a take fails and an earlier observation is still on screen, the outcome is the failure's (*Timed out*, *Refused*), the earlier evidence stays visible and says when it was read, and the door gives up its surface for an outline. The hardware step reads each log's coverage and cap: an empty window that its log does not fully cover is not called quiet, and a capped window says *at least*.

**Empty is scoped.** When the record before a stop is empty because the System log's retention begins after it, the step says *the System log's oldest retained record is from Thu, Sep 24, after this stop. This is the log's reach, not a quiet machine.* The fixture happens to show exactly this, because its crash sessions are older than its System log.

**One voice per job.** DM Sans for questions, titles, labels and controls; Azeret Mono's tabular figures only for a door's one number; JetBrains Mono only for exact values (times, codes, reading names). Controls are surfaces that look pressable, at 44 px. The dark instrument field and the phosphor stay; the phosphor marks focus, the next start on the time line and the settle under a door's number when its fact changes. Depth comes from the raised surface and the inspection well; the partitioning borders in the rebuilt views are gone.

**Motion only when a fact changes.** A door whose fact changes on a retake draws one settle of the light under its number; nothing moves at rest, and Reduced Motion removes it.

**Crashes split in two.** The old Crashes view mixed two complaints: the whole machine stopped, and a program crashed while the machine kept running. They are now two doors. Dump files and Windows' reliability record moved behind *When this evidence runs out* in both, read only when opened. `?view=crashes` still lands on the stop door.

## For the agent

The agent already reads every reading on these screens through MCP. What changes is that the composition becomes visible and exact: under each situation, the calls in order with the page's values filled in, and a *Copy for an agent* that produces text like this (from [stopped-1440-full](stopped-1440-full.png)):

```
1. crash {"count":5}
2. record {"before":"2026-09-12T06:14:58.000Z","count":25}
3. changes {"before":"2026-09-12T06:11:02.000Z","hours":168,"count":100}
4. whea_window {"source":"system","since":"2026-09-12T05:11:02.000Z","before":"2026-09-12T07:14:58.000Z","order":"oldest","count":250}
5. whea_window {"source":"kernel_whea", ...same window}
```

The view's own requests are made from the same values, so what the person sees and what the agent is told to read cannot drift apart inside the dashboard.

## What it asks of the API

1. **Situations as named server recipes.** `GET /api/situations` lists the doors and their recipes; `GET /api/situations/{id}` (with an anchor such as a stop's identity) returns the door's fact, the rule behind any mark, and the composed envelopes in order. One MCP tool and one MCP prompt per situation. `situations.ts` is written as pure functions over envelopes so it can move to the server as a move, not a rewrite. Today the door facts are computed in the dashboard, so an agent gets the same envelopes but not the same one-line fact.
2. **A read ledger.** Each recipe invocation returns a bundle identifier and is recorded as a read (*an agent read the stop recipe at 14:02, covering 5 stops*). That is the visible *what did my agent see* the audit found missing, and the precondition for direction C's proposals without C's storage. This was Buddy's strongest idea and it belongs to the API, not the page.
3. **One placed time per stop.** The door and the stop list place a stop at Windows' estimate, else its next start. A `crash` field naming the placed time would keep every client consistent.
4. **Acknowledgement the agent can read.** The new-since mark is per browser today. The cheapest step is to carry each door's acknowledged cursor in the handoff the person already sends; a per-client acknowledgement on the server would then let the phone and the desktop agree, and let an agent say *this is new since you last looked*.
5. **Nothing new for free space.** The disk door reads `hardware.storage`, whose derived volumes already carry `free_gb` and `used_percent`. The draft's ask for a cheap free-space figure outside the Space walk is already met; a lighter `volumes` projection would only make the home cheaper.
6. **Rename the prompt presets.** The handoff door leads to the Stack, whose presets (*Preventative Oracle*, *Quantum Diagnostician*) promise prediction, and agents receive them through `prompts_list`.

## What carrying it across all 17 views costs

Built here: the shell and rail, Home, the stop situation (from Crashes and ChangesNearStop), the program situation (from Crashes), Record's log and frame restyled, and the shared marks, steps, recipe block, buttons and segmented choices. The type and label changes in `identity.css` already reach every view, so the old views read closer than before.

| Remaining | Becomes | Estimate |
| --- | --- | --- |
| Errors, KernelReports | The hardware door's composition: `whea`, then `storms` traffic with its lead and basis, then the Kernel-WHEA channel by report time | 1.5 days (Errors is the longest view, and its four paragraphs of derivation move to the rule disclosure) |
| Performance, ProcessPressure | The slow door: now, who is using it, the retained samples and their gaps | 1 day |
| Space | The disk door's first step is the volumes' free space; the Space walk and its lab stay as the place behind it | 0.5 day |
| Stack, Agents | The handoff door: the composed handoff first, the prompt library after it, setup as a place | 1 day, plus the preset renaming in the API |
| Machine, MachineOverview, MemoryMap, PcieMap | Places, restyled: surfaces for boxes, the display face for the one number, jump links that fit a phone | 1 day |
| Signals, Diagnostics | Places; each lead links to the door it speaks to; Diagnostics are the further readings situations offer | 1 day |
| ReliabilityHistory, Record's nearby sections | Restyled inside the situations that open them | 0.5 day |
| The remaining partition borders and the studio page's view names | Cleanup, and the view list on mainthread.ai copies these names | 0.5 day |

About seven to eight focused days for one builder, with the tokens and components already in place, plus the server recipes if David chooses this direction (roughly two days with tests).

## Honest weaknesses

- **A fixed set of complaints.** Network, audio, battery and *it will not start* have no door and fall to the places. Six doors will be under pressure to become ten, which is how this turns into dashboardification.
- **The idea is proven for two doors.** Slow, disk, hardware and handoff open their old views behind the new shell.
- **No time spine.** Each situation still owns its window. Direction B's shared range answers *when did this start* better than any door does.
- **The home costs five readings on every open** (`crash`, `faults`, `whea`, `system`, `hardware.storage`). Measured only on fixtures; `hardware.storage` in particular has not been timed on a real machine as a home reading.
- **The mark is per browser.** A first visit marks every door that has any record; clearing site data does the same; the phone and the desktop disagree until the API holds acknowledgement.
- **The slow door says little about the past.** Processor load now does not answer *it was slow an hour ago*; that needs performance history, which is off by default.
- **Door facts live in the dashboard** until the API serves the recipes, so today the agent's parity is in the envelopes, not in the sentence on the door.
- **Fixture limits.** The crash sessions are older than the System log fixture, so step 2 shows its empty, scoped state rather than rows, and its *oldest retained record is from Sep 24* sits beside the crash reading's own *reaches back to Sep 2*: two fixtures with different retention, which a real machine would not show. `changes` answers failed; reliability, PCIe, constraints and performance history answer empty or failed.
- **The agent cannot see the mark.** The acknowledgement lives in the browser, so an agent cannot tell which records the person has already opened. Buddy's suggestion, folding each door's acknowledged cursor into the handoff the person sends, is the cheapest fix and is listed with the API asks.
- **Older screen checks still name Crashes.** `docs/screens/fixtures/check-*.cjs` and `capture.cjs` drive the old view names and would need the new ones.

## Challenge and review

- **Buddy** (Muse, through the bridge) challenged the build plan before the code was finished. Acted on: the 30-day cutoff replaced by acknowledgement; four ways of not knowing given their own words and shapes; hue removed from event levels so hue has one rule; the program door states that a freeze without a report is invisible; the handoff door names the one shared handoff; the read ledger taken as an API ask. Kept: the rail keeps its facts, because the mark, not the fact, was the anxiety.
- **GPT-6 Sol** could not be reached: Multithread refused the call before submission twice (once for the main checkout, once for this worktree) because Codex's hooks have not been reviewed for this repository. An independent Opus 5.5 review through `multithread peer claude` stood in, as it did for the audit, and is labelled as a substitute here.
- **The Opus stand-in** read the source and four screens. Its strongest challenge is to the direction: five of the six doors still map one to one onto a reading, so the audit's first cause is *renamed rather than removed*, and a big number on every door reads like a KPI grid. It would make the **episode** the unit (a recent-episodes list with the questions as entry filters) and keep this build's stop composition as the inspector, which lands close to direction B. That is recorded here as the best case against A rather than adopted, because it is a different direction; the slow door's big number was removed on its argument. Acted on from its code review: a failed retake no longer shows as observed (the most serious finding); the hardware step reads coverage and caps and raised its cap to 250 so the report filed at the next start is not cut off by a storm; a door no longer writes a zero for a log it did not read; the home no longer reads as all-clear when doors failed; the screen-reader text states the real rule; the agent recipe carries the chosen stop count; anchors are named for what they are (next start, announcement, report); choosing a stop moves focus to the composition and scrolls to it where the list sits above it; a door's accessible name is its question; stored acknowledgements are validated and shared across tabs; levels and marks no longer share shapes; the stop list says *New* in words instead of five coral triangles.
- **Buddy's second review** of the built branch found no failed or unread reading rendered as a zero or a fact, confirmed the three windows of the stop recipe against the evidence, and caught a raw ISO boundary in the change step and a window that would have centred on an error report's filing time; both fixed. Its one change before the comparison, putting the acknowledgement where the agent can read it, is API ask 4.

## Reproduce

Build the dashboard in `dashboard/` (`npm run build` writes to the ignored `sentinel/static`), start `docs/screens/fixtures/fixture-server.py` with a scratch `SYSTEM_SENTINEL_HOME`, then run `docs/screens/fixtures/capture-views.cjs` with the shots `home stopped stopped-changes stopped-older home-after programs programs-open record record-open nav-open` (order matters: `home-after` follows a visit to the stop door in the same browser context). For `home-gaps`, start the server with `SENTINEL_FIXTURE_HOME_GAPS=1`. [docs/screens/README.md](../../../screens/README.md) has the full commands.
