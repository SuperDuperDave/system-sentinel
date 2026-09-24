# Changelog

## [1.9.14] - 2026-09-24

### Fixed
- A reading's `took_ms` now measures elapsed time from entering Sentinel's reading boundary through completing its evidence. It includes worker and bridge waits, composition, and all Signals scheduling waves. Health failures no longer appear to take zero milliseconds, and CLI `check` uses the same timing path. Dump inspection by reference includes both fresh reads and reference matching.
- The API and MCP schema now describe `asked_at` as the envelope's host-clock stamp, rather than the start of a machine query. Signals' method keeps each input's own duration; the parent duration includes time inputs spent waiting for a Signals lane.

### Limits
- Reading duration excludes request routing before the reading boundary and redaction or response encoding after it. The empty-script benchmark floor remains bridge-level time, so subtracting it from a reading duration does not isolate Windows execution. Earlier benchmark rows and saved Stack readings carry mixed timing meanings; old Stack envelopes have no version field, while capture manifests do. No new public stage-timing field is added until wait, launch, query, decode and composition stages can be measured consistently.

## [1.9.13] - 2026-09-24

### Fixed
- A cold PowerShell session waiting for a held WSL launch slot now reports `unavailable` with `error.kind=busy` after one wait. Slot contention no longer counts as a failed session start, triggers a 30-second startup cooldown, or sends the same question to another launch that needs the same slot. Direct `Session.start` callers remain protected by the slot.
- Queueing for a session, waiting for its launch slot, and waiting for a one-shot fallback slot now share the question's wait limit, including WSL interop retries. Once Windows execution begins, the script still receives its full timeout. A genuine session-start failure wakes every queued question to reconsider its path, and an unexpected startup error gives the pool its reserved capacity back.
- Health now says that fallback questions went to one-shot launches without claiming each one received an answer. Each reading's outcome remains the source of that truth.

### Limits
- A synthetic held-slot gate and portable pool lifecycle tests verified these paths; they do not measure how often slot contention occurs on a Windows host. The shared deadline limits waiting for bridge resources, not total execution time. Session-start slot contention is visible on the affected reading as `busy` rather than in `last_start_failure`.

## [1.9.12] - 2026-09-24

### Fixed
- A question that cannot enter a busy PowerShell session pool or WSL launch slot still reports `unavailable`, but now carries `error.kind=busy` where the reading is built from that bridge result, records the wait in `took_ms`, and explains that the timed-out attempt could not reach Windows. Signals now keeps a busy input's precise cause in its method and warnings. The screen, API guide and saved handoff no longer describe every unavailable answer as a missing bridge. Existing saved readings and the six outcome values remain valid.
- One Signals reading now starts at most `max(1, pool size − 1)` concurrent inputs. At the default four-session size it can leave one session for a concurrent light question when nothing else is using the bridge. A pool of one leaves no spare session. Disabling the session pool keeps Signals' previous one-shot concurrency.

### Limits
- A synthetic four-session gate reproduced a cheap reading waiting 250 ms and then receiving `unavailable` before this change; another gate showed one Signals reading filling all four sessions. These establish the pathway, not frequency or 60-second behavior on a Windows host. Other clients can still fill the pool; the per-Signals cap is not a global reserved lane. With three lanes instead of four, Signals may take longer, especially when several inputs each reach their own timeout; A small alternating host comparison observed a latency cost; `bench --readings signals --transport session` measures Signals on another machine.

## [1.9.11] - 2026-09-24

### Fixed
- Capture keeps taking and writing readings one at a time, then moves its uninterrupted saved-Stack, handoff, manifest, ZIP close and publication work to one worker. A large saved investigation no longer holds the event loop while that tail runs; its Stack and handoff still come from one snapshot.
- Cancellation while readings are being taken removes the pending capture. Once the tail worker owns it, that worker finishes a complete visible capture or closes and attempts to remove a failed one, even if the caller leaves. A late worker cannot touch a ZIP already cleaned up by the request. An OS refusal to remove a pending file is logged; it never appears as a completed capture.

### Limits
- A corrected, yielding synthetic Linux fixture with a default-size automatic Record measured median largest loop gaps of 6.8/445.5/2397.2 ms for zero/one/five saved 2,000-row items before this change, and 6.3/12.2/29.6 ms after it; three samples per case. Those are fixture measurements, not Windows timings or typical Stack sizes. Reading-member formatting and compression still run on the event loop between reading takes; their default and large-answer costs need separate measurement.

## [1.9.10] - 2026-09-24

### Fixed
- Async HTTP Stack add and MCP Stack, prompt and capture-list operations now put saved-file work in workers. A slow read, write or lock wait no longer holds the event loop and delays unrelated clients.
- Once a Stack edit begins in a worker, its saved change and handoff notification finish together even if the caller cancels. A refused edit still sends no notification; a notification failure cannot make a saved edit look unsuccessful.

### Limits
- A gated synthetic Stack read and a held transaction reproduced the previous loop stall and verified the correction; native Windows Stack size and latency remain unmeasured. These calls can still wait on their own saved-file lock or worker capacity. Capture ZIP assembly and large MCP answer encoding remain separate measurements before changing them.

## [1.9.9] - 2026-09-24

### Fixed
- When the machine's names must be relearned after a failed identity probe, redacted async HTTP and MCP requests now wait for one shared lookup without freezing other clients or occupying a worker thread per waiter. The triggering answer uses the new policy; cancelling one request does not cancel the lookup for others.
- Stack and performance edits acquire their redaction policy before changing saved data. An unexpected identity-lookup exception therefore refuses an edit before it lands, instead of reporting failure after a successful change.

### Limits
- On native Windows the inherited machine name normally avoids this relearn path; the measured event-loop stall came from a synthetic delayed probe in a development-style unknown-identity state. A routine unavailable lookup keeps the prior policy and retries no more than once per minute. An unexpected lookup exception with no known host refuses redacted answers until the next attempt or a successful direct learn and can close a connected live stream. Saved Stack I/O and capture assembly on async paths remain to be measured separately.

## [1.9.8] - 2026-09-24

### Changed
- A composed handoff now reports whether its selected prompt was included, turned off, absent, missing, or unavailable. Missing and unreadable selected prompts leave a clear notice in the Markdown while the saved Stack evidence remains readable. HTTP and MCP receive a compact Stack index from the same saved snapshot as that text, so clients can display one consistent investigation.
- The Stack screen refreshes its evidence and handoff from that one response. A failed prompt-library read is shown at the library without hiding readable evidence or suggesting that no prompt was chosen. Captures retain `composed.md` when only the selected prompt fails; their manifest and bounded listing state its condition.

### Limits
- Prompt resolution follows the saved Stack snapshot and may observe a later library edit; the returned status states what was actually included. A damaged Stack remains unavailable until its saved file is repaired.

## [1.9.7] - 2026-09-24

### Fixed
- Saved Stack and prompt failures now distinguish a lock not obtained in time, inaccessible files, damaged content, edits that did not reach replacement, and edits whose result must be checked. HTTP gives agents a stable `reason` beside the existing error and detail; MCP refusals lead with it. Prompt deletion answers with the list its own transaction wrote.
- A failed unlock after a completed saved-file edit no longer tells a client the edit failed and invites a duplicate retry. Damaged saved files are still refused, never replaced with an empty one.
- Capture and benchmark descriptions now say their readings run in turn, so their costs add up. Concurrent capture collection remains unmeasured and is unchanged.

### Limits
- A failed replacement is reported as `uncertain` even when it may not have changed the file; inspect saved state before retrying. A hard stop can leave a dot-named scratch copy of private or recoverable data. The app preserves it for manual review rather than deleting a possible recovery source.

## [1.9.6] - 2026-09-24

### Fixed
- A capture now renders `composed.md` from the same saved Stack snapshot written as `stack.json`. An edit arriving between those steps can no longer make one ZIP contain two different versions of the investigation.
- Stack remove and clear now return the exact index state their own transaction wrote on both HTTP and MCP. A second client's immediate edit cannot appear in that mutation's answer, and the routes avoid an unnecessary full-file reread.
- Separate Stack and prompt instances in one process now share a local queue per saved file before taking the bounded OS lock. The running server already used one instance per file; this prevents spurious timeouts for other same-process callers while the OS lock still protects separate processes. The change followed an intermittent Windows CI lock-timeout failure that passed on an exact-commit rerun.

### Measurement and limits
- [A reproducible synthetic Stack probe](docs/measurements/stack.md) measures direct saved-file reads and edits at several large evidence sizes without opening installed data. No storage migration is made: normal-use size and concurrent lock cost remain unmeasured, and full-file replacement still scales with saved evidence.

## [1.9.5] - 2026-09-24

### Changed
- `events` now accepts `order=oldest` with an explicit ISO `since` moment. It returns the earliest matching System or Application records with exact seven-digit time filtering before its row cap. The extra matching row defines an exclusive later reach; requested bounds, the host clock, returned times and probe must check out before that reach can be claimed. Default newest-first calls remain available.
- Record now shows the first System records timestamped at or after a held moment below its progressive before frame. The sides have separate outcomes and reach, a visible boundary, a phone-friendly jump control, complete raw rows and independent Stack actions. Widening keeps open rows and keyboard place; a failed retake labels and keeps the last observed answer. The Log view's “Every level” control now requests every level, including any records outside levels 1–4.
- [Agent-answer measurements](docs/measurements/agent-answers.md) now distinguish serialized answer size from a synthetic Claude Code 2.1.280 client probe. This client chose structured content and saved a complete oversized result behind a path; other clients and versions remain unmeasured.

### Limits
- Oldest-first reach assumes no clock inversion among unreturned records. Returned inversions remove the contiguous time claim without erasing raw rows. A time-based split can miss a later-written record whose timestamp moved backward. A large System log or another machine may have different query cost; the after frame starts with 25 rows and can be widened to 2,000.

## [1.9.4] - 2026-09-24

### Changed
- Reading envelopes now put outcome, count, error, warnings and redaction notes before bulk sections and the collection script. HTTP and MCP return the same fields and full evidence; a text reader sees the verdict and caveats at the beginning of a long answer.
- A reproducible synthetic [agent-answer size measurement](docs/measurements/agent-answers.md) records text, serialized MCP result, section and method bytes, including a 2,000-record case. It reads no machine data.

### Limits
- Field order does not reduce answer size or establish whether an agent client passes, truncates or rejects a large result. Any future summary projection needs client evidence and must preserve source reach, omission counts and links between raw and derived sections.

## [1.9.3] - 2026-09-24

### Changed
- `faults` now accepts `order=oldest` for an explicit time window. It keeps the earliest matching Application records and uses the extra matching probe for an exclusive later reach when capped. Exact seven-digit bounds, the independently requested window, the host clock, readable row times and returned time order must check out before that reach is claimed. Raw and decoded records remain available when reach is unknown; an unverified empty request cannot masquerade as complete absence. The existing newest-first answer remains the default.
- Record's optional nearby Application reports now use two independent 50-record reads meeting at the selected moment: newest before and oldest at or after. Each has its own outcome, reach, cap and Stack action. A grouped live-kernel report on the after side is placed by its first returned filing while detail remains tied to the latest returned record. Opening, paging or retaking one side keeps the held frame and any still-returned open fault in place; a failed retake labels and preserves the last observed reading with its warnings. Agents have the same two-call recipe in the API guide.

### Limits
- These are report-filing-time windows, not proof of when a fault occurred. A live-kernel report can have records on both sides; a decoded entry groups only what that side returned. Directional time reach assumes no clock inversion among unreturned records. The oldest-first query may cost more on a large retained Application log, particularly for a recent moment; the sparse development host does not establish high-volume performance.

## [1.9.2] - 2026-09-24

### Changed
- Record's optional nearby Kernel-WHEA panel now asks for two exact, independent filing-time windows meeting at the held moment. The newest 250 reports before it and the oldest 250 at or after it keep the nearest evidence visible even when later traffic would have filled the former single 500-report cap. Each side shows its own outcome, retention reach, cap and Stack action; the same exact seven-digit moment is the exclusive end of one and inclusive start of the other. Local paging follows the direction, so "older" and "later" mean the correct thing.
- Opening a report or retaking a side keeps the selected moment and place. A failed retake keeps the previously observed report and marks its reading as held; closing and reopening the panel does not re-query. Browser-valid moment addresses that do not meet the exact Windows timestamp grammar are canonicalized before a query. Agent guidance includes the two-call recipe and its directional coverage limits.

### Limits
- The two sides remain separate answers; do not combine their coverage into one claim. Each is capped at 250 previews, 500 total, and a truncated side still omits reports farther from the moment. A filing time is not an error-occurrence time. The nearby Application fault panel has its own newest-first 100-record cap and is not yet directional. Dense Kernel-WHEA query cost and prevalence are unmeasured.

## [1.9.1] - 2026-09-24

### Fixed
- The `changes` reading now derives a historical millisecond window from the request and checks both bounds returned by its collector. A mismatched or unreadable echo leaves coverage unknown; safe returned rows remain visible and are placed against the requested interval. An unverified, unclocked or entirely future empty window reports a failure rather than a complete absence. The reading count now counts only returned rows inside that requested interval; outside rows remain in raw and derived sections with an issue count and an `outside_window` marker on each interpreted entry.
- Generated `changes`, System storm and Kernel-WHEA timeline scripts now parse explicit UTC bounds with invariant culture. A fixed-timestamp probe under `en-US`, `th-TH` and `ar-SA` parsed correctly before this change; the parser update is precautionary hardening, not evidence of a locale failure on the development machine.

### Compatibility and limits
- `changes` continues to round its end down to the millisecond used by its Event Log XPath query. A source that stops partway through still reports failure and does not expose partial rows from that source; independent source rows remain available. Full seven-digit filtering is deferred until event-time comparison on Windows is measured.

## [1.9.0] - 2026-09-24

### Changed
- Routine Stack list, add, update, remove, clear and prompt-choice answers now return a compact item index with separate `provenance` fields instead of copying every saved reading. The dashboard reads that index, and agent calls receive the same compact shape. The Stack file, composed handoff, captures and duplicate-refusal behavior still use the complete saved evidence.
- `GET /api/stack/items/{id}` and the read-only `stack_item(id)` MCP tool return one complete stored item on request. The default remains redacted; unredacted MCP access requires a reason. An old saved item with malformed reading provenance keeps its item and shows null for each unknown index field.

### Compatibility and limits
- Clients that read `items[].reading` from `GET /api/stack` or Stack mutation responses must read `items[].provenance` for the index and request the selected full item by ID. `stack_add(take=…)` now returns provenance, so an agent needing the taken evidence should call the reading directly or `stack_item`. An uncertain add can be checked in the index; an already saved envelope can be retrieved through the exact item path.
- This removes repeated response transfers but does not change the full Stack file read or atomic rewrite cost of a mutation. A large number of items, long titles or parameters, or long selection ID lists can still enlarge the index; actual user Stack sizes and latency remain unmeasured.

## [1.8.0] - 2026-09-24

### Changed
- Broad System WHEA and Kernel-WHEA report timelines now keep exact per-bucket totals and coverage but send sparse returned-bucket columns. System signature counts use compact bucket/signature pairs. Fixed-header severity totals include reports with unplaceable times and distinguish unreadable headers from readable unknown severity codes. System signature sample messages are limited to 1,024 UTF-16 characters with their original length; the exact record keeps the full text.
- `whea_reports` omits its per-report reference list by default. Set `references=true` to retrieve every returned reference, including reports without a readable filing time. `storms` retains its existing opt-in reference list. The Hardware errors screen reads the compact timeline and asks `whea_window` for at most 500 previews in the selected stretch. The Record moment's nearby Kernel report list also uses an exact, bounded window. Previous previews remain visible while a selected stretch loads; exact record reads remain available from each preview.
- Stack summaries read both the new sparse buckets and older saved object buckets. Capture manifests explain the Kernel timeline's scope. MCP text JSON is compact while the typed structured answer remains the same, reducing duplicate text bytes for large readings.

### Compatibility and limits
- Clients that read `buckets.active` must use `buckets.returned` and, for System signatures, `buckets.signature_pairs`; the bucket arithmetic is in [the API contract](docs/API.md). Saved older Stack items remain readable. `whea_reports.reports` now requires `references=true`; agents can instead ask `whea_window` for a selected exact interval and `whea_record` for complete retained fields. The Errors screen's separate preview query can answer at a different time from its timeline. Its 500-preview cap can leave older reports in a busy selected interval, with source reach and truncation stated. An unplaceable report remains available in opt-in references; if returned by an exact time-window query it can make that preview fail rather than imply the interval was empty.
- The sparse response reduces transferred answer bytes, not the Windows query or bridge projection cost: System WHEA signature generation still receives full messages. High-cardinality signature lists can remain large. No report-time cluster proves when the underlying hardware error occurred.

## [1.7.0] - 2026-09-24

### Changed
- `record`, `events` and `faults` now honor Windows' seventh fractional timestamp digit at their selected boundaries. The indexed XPath takes a broad millisecond range; an exact tick filter removes outside rows before the count cap. A record just before a selected event in the same millisecond can now appear in “The record before this,” while a `since` bound excludes earlier rows in that millisecond. Returned collection bounds show the exact normalized UTC time. Ordinary unwindowed newest-record reads retain their existing `-MaxEvents` path.
- `crash` uses the same exact pre-cap filter when finding the last System event before the next start. Earlier versions could skip a predecessor in the start's millisecond. A row that is not strictly earlier now fails that supporting lookup instead of being presented as the predecessor.

### Compatibility and limits
- `record.before` and `events`/`faults.since` still accept offset-free timestamps interpreted in the server's local zone; `events`/`faults.before` still requires `Z` or an offset. For these three readings, Windows Event Log times before 1601 and fractions longer than seven digits are now refused instead of being silently rounded. `since=boot` keeps Windows' existing millisecond boot-time bound. An exact boundary does not prove Windows emitted every event or that an event's timestamp reflects when an error occurred. The separate `crash` moment and `changes` window still use their published millisecond boundaries.

## [1.6.0] - 2026-09-23

### Added
- `whea_window` lets an agent inspect one WHEA log over an exact filing-time interval with up to 500 bounded previews. Required `source`, `since` and `before` preserve Windows' seven fractional timestamp digits; `order=newest` or `oldest` decides which side the cap keeps. Returned rows are in ascending report time, with source-specific retained reach, the host's pre-query clock, and an exclusive boundary from the extra matching probe row. Use `whea_record` for one exact selected report and compare its filing time.
- The exact time filter runs before the count cap, so millisecond XPath boundary rows do not consume preview slots. A future interval remains incomplete, and failed or denied collection supplies no evidence sections. The Stack and MCP guidance expose the new reading; bulk capture omits it because it requires a selected window.

### Limits
- This is a report-time view of retained log content, not an error-occurrence timeline. Windows may file an earlier-session error later, and an event written after the pre-query clock is outside this answer. Directional time reach assumes filing times did not move backward across retained record order; a clock correction can invalidate it. The reading has no cross-log grouping, signature, full binary payload or decoder output; use the existing `whea`, `storms` and `whea_reports` readings for their different questions. The Record screen does not yet request this exact window.

## [1.5.0] - 2026-09-23

### Changed
- `whea` is now a bounded newest-record preview across the two WHEA logs. Each row keeps its source, RecordId, report time, at most 1,024 UTF-16 message characters with the original length, and at most the first 128 bytes of its first binary property with the full payload length. The collector no longer serializes every binary property twice or launches a detail decoder for every list row. Source outcomes, cutoff, fixed-header facts and likely cross-log groups remain visible.
- Opening a row in Hardware errors reads that exact retained event through `whea_record`, compares its source, RecordId and filing time with the preview, and shows full Windows fields, a structural CPER check and System WHEA-Logger decoded detail. Exact CPER bytes still require the explicit unredacted action.
- Agent guidance points from the preview to `whea_record`. Capture manifests now state that `whea` in a ZIP is a preview and selected exact records are absent.

### Compatibility and limits
- New `whea.records` rows omit `Properties` and `RawData`, and `whea` no longer has a `decoded` section. Saved older Stack envelopes retain their original full shape. Agents and clients needing complete rows should use `whea_record(source, record_id)` and compare `TimeCreated` before relying on a re-read.
- `whea_record` still refuses a row whose binary properties total over 1 MiB. The preview can show its reported length and fixed-header facts but cannot provide exact fields for a row above that bound. The preview's fixed-header facts do not certify the full CPER structure.

## [1.4.1] - 2026-09-23

### Added
- `storms` can return one bounded, citable reference per returned in-window System WHEA-Logger report when `references=true`. References include fixed-header facts and explicitly unplaceable rows, omit messages and CPER bytes, and can lead to an exact `whea_record(source=system)` re-read. Compare the filing time because log-local RecordIds can be reused.

### Changed
- The default storm answer and Hardware errors view omit the new per-report list. A requested full list remains available to agents and future focused Record views; compact Stack handoffs retain signature sample references and count omitted per-report references.
- System and Kernel-WHEA references use one fixed-header projection and normalize unreadable filing times to null.

### Limits
- A deliberately requested full list at the 20,000-row cap can add several megabytes to API, MCP or full Stack output. Even the default answer grows with the number of active buckets and signatures. Narrow the filing-time window before requesting detail. This is still report traffic, not a count or clock for underlying hardware errors.

## [1.4.0] - 2026-09-23

### Added
- System WHEA-Logger storm readings now derive fixed CPER-header PreviousError and unreadable-header counts from a bounded 128-byte projection. Historical windows keep these aggregate facts; storm readings omit the projected bytes, and `whea_record` can retrieve exact evidence on demand. The Hardware errors screen and compact Stack handoffs expose the counts and their limits.
- Live storm status remains about report traffic. A separate `not_marked_burst` is true, false or unknown according to readable flags, returned reports and recent-window coverage. A report not marked earlier-session is not assigned an error occurrence time.

### Changed
- Both WHEA timeline collectors find the first binary event property with a direct PowerShell loop, avoiding a nested pipeline and full payload enumeration for every returned report.

### Limits
- System WHEA-Logger events do not all promise a readable CPER header. Missing or invalid headers remain unknown. Native Windows projection was exercised with synthetic CPER, short, empty and absent binary properties; the synthetic 20,000-row cost check does not include real Event Log retrieval.

## [1.3.9] - 2026-09-23

### Added
- `storms` now accepts an exclusive, time-zoned `before` anchor to inspect an older System WHEA-Logger filing-time window without spending its bounded query on newer traffic. The host query time, actual bucket-aligned bounds and observed upper reach travel with the answer. An end beyond the host clock cannot be complete, and an entirely future interval fails.
- Historical storm answers keep bounded bucket, signature and coverage evidence for API, MCP and Stack handoffs, but omit the live burst, acceleration and quiet status. Windows filing time is not necessarily the hardware occurrence time; the System storm projection does not yet distinguish CPER PreviousError backlog after a restart.
- Compact Stack handoffs explain the missing historical status and include up to three exact System sample references so an agent can re-read retained events. The sanitized screen fixture uses one clock for storm samples and exact records.

## [1.3.8] - 2026-09-23

### Added
- `whea_reports` accepts an exclusive `before` anchor, so an old report-time window can be read without newer traffic consuming the cap. The host query time, actual bounded window and observed upper reach travel with the reading; future requested time cannot be called complete. Both `whea_reports` and `storms` use the preceding observed bucket when an exclusive end lands exactly on a bucket boundary. The Record moment frame can open this channel separately, inspect exact reports in place and add the anchored reading to Stack.

### Changed
- CPER header time no longer assumes one byte encoding. `identity.cper.timestamp` is removed from new readings and replaced by `header_time` with the stored bytes, integer and BCD calendar readings, and the separate precise flag. Older saved Stack readings retain their original shape. The exact-record view explains ambiguous or invalid dates and keeps Windows report time separate.

### Limits
- Kernel-WHEA event time records when Windows filed a CPER report, not necessarily when the hardware error occurred. The channel retains history independently of the System log. A nearby report is a lead, not proof of a cause; an empty report-time window does not rule out an earlier error.

## [1.3.7] - 2026-09-23

### Changed
- `events` and `faults` accept an exclusive `before` bound alongside `since` for a bounded moment window. Window coverage now shows `covered_until` and never marks future requested time complete; `changes` follows the same rule. Out-of-window collector rows remain visible as raw evidence and lower the completeness claim.
- The Record moment frame can open an on-demand Application-log fault window, with its own coverage, interpreted details, raw records and Stack handoff. It stays in place while the person inspects nearby reports.
- Compact `events` and `record` Stack handoffs keep source outcome and retention reach beside citable `Log:RecordId` rows. Selected rows keep that context and exact raw records. Older saved readings name missing context, and a requested summary that uses full sections says so.
- The Agents catalog describes its `private` hints as places that may contain masked details. A listed path field is not removed wholesale by default redaction; recognized user-profile segments are masked and the rest remains available as evidence.

## [1.3.6] - 2026-09-23

### Changed
- Stack now distinguishes later observations of the same reading from retries of one held observation. Duplicate responses identify the existing item and observation time; selected log rows use the same identity whether supplied by numeric RecordId or its qualified `Log:RecordId` form.
- Saved Stack and prompt edits use a cross-process lock and atomic, owner-only replacement. Malformed or unreadable saved files produce an explicit unavailable response and stay intact; the dashboard no longer presents that state as an empty Stack. Captures keep machine readings when saved context is unavailable and name any omitted Stack or handoff member in the manifest.
- Direct agent calls validate Stack and prompt arguments before changing saved files. The API describes the observation and retry boundaries and how to recover an unreadable saved file.
- Whole crash and fault Stack items now default to bounded summaries that keep their interpreted stop or fault facts beside source outcomes and retention reach. Selecting a stop or fault carries its derived meaning with the exact selected raw records at full verbosity; full whole-reading sections remain available on demand.

### Limits
- A `take` creates a new observation on every call. After an uncertain add, inspect the Stack before retrying. A redacted response to a `take` is not a guaranteed duplicate key for the raw stored parameters.

## [1.3.5] - 2026-09-23

### Changed
- `events`, `record` and `faults` now return the source log's enabled state, mode and oldest retained record after their matching query, with derived retention reach. An empty match before the log's retained history no longer reads as a quiet period. Windowed queries distinguish a complete returned window, a row limit, an interrupted query and unknown metadata while keeping any valid returned records. A future start keeps an observed empty query outcome but cannot claim a complete window; newest-record requests show retention without claiming a time window.
- Already connected default-redacted streams use the current identity policy after each poll. A failed relearn preserves previously known names, and a native Windows server can use its inherited computer and user names if the PowerShell identity query fails while still reporting that failure. Bridge-error redaction runs off the server event loop.
- The `boot` event-log boundary is rounded to the same UTC millisecond used by the log index and returned as the resolved window start.

### Limits
- Retention reach depends on the live log's recorded order and does not prove Windows emitted every event. `faults` covers Application log reports by filing time, not every live kernel event that occurred. An offset-free timestamp is interpreted in the server's local zone; use an explicit offset or `Z` when WSL and Windows may differ. Windows' reported kernel session can span multiple power-ons with Fast Startup.


## [1.3.4] - 2026-09-23

### Changed
- `crash?moment=` now carries independent System and Application retention evidence and says whether the first returned start is established as the first after the requested moment. If System retention begins later, returned report-only stops and a startless Kernel-Power 41 stay in the derived stop list instead of disappearing behind a false first-start claim. A report-only stop is ordered by when Windows filed the report; the stop itself may have happened before the requested moment.
- A clean-start claim and `no_bugcheck_recorded` now require Application report retention to reach the relevant start as well as completed source queries. The dashboard explains this uncertainty. A 41 without a returned start no longer asks for or presents the record before its announcement as the record before restart; that preceding record may already belong to the new boot.

### Limits
- Retention reach does not prove Windows emitted every event. The oldest-first moment query has explicit record bounds, and a report's filing time does not establish its stop time. If log metadata is unavailable, returned evidence remains visible and the first-start conclusion stays unknown.

## [1.3.3] - 2026-09-23

### Changed
- Captures are now published only after their ZIP and manifest close. A capture in progress does not appear in the list or download route, and concurrent captures started in the same second keep separate files. Canceling or failing a capture removes its unfinished file; a pending file left by an abrupt process exit is removed when it is older than 24 hours and a new capture or listing runs. Completed captures are never removed by this cleanup. On POSIX, new capture files are owner-only.

### Limits
- An abrupt process exit can leave a hidden pending file until the next cleanup pass; it is never presented as a completed capture. The capture still takes readings sequentially, so its members carry their own observation times rather than a single simultaneous snapshot.

## [1.3.2] - 2026-09-23

### Added
- Hardware errors now lists the returned Kernel-WHEA report references under their report-time trace. Selecting a lit stretch filters the list in place; opening a report reads its exact retained record with default redaction, checks its source, RecordId and timestamp against the timeline, and shows the CPER bytes only after a separate explicit action. The list reveals 50 rows at a time up to 500 and names query, retention and display limits.

### Changed
- Opening another record or stop detail keeps the chosen row at its viewport position. Crash fault-kind cards filter their rows without jumping the page. The report trace exposes its selected stretch and keyboard controls.
- A compact or selected Stack handoff for an exact `whea_record` now carries its CPER header severity and PreviousError fact alongside the selected raw row, with default redaction accurately described.

### Limits
- The timeline dates when Windows wrote a report, not necessarily when the hardware condition occurred. An exact record may disappear or be replaced as the Windows log rotates; the UI declines to attach a mismatched re-read. The visible list is bounded, while the complete returned reading remains available through the API and Stack.

## [1.3.1] - 2026-09-23

### Added
- `whea_record` retrieves one retained WHEA event by its required log source and EventRecordID, including a report outside `whea`'s newest-record window. The query asks for one extra match to detect ambiguity and rejects binary properties over 1 MiB before serialization. The source outcome, log retention metadata, CPER header facts and exact raw fields remain available; default responses withhold CPER bytes. The Hardware errors screen's explicit exact-read action now uses this lookup, so newer events cannot silently push the selected row out of its re-read.

### Changed
- Captures and default benchmarks take readings that need no exact event or file selection. A capture's manifest names selection-dependent readings it omitted, while its listing exposes only the omission count. An explicitly named selection-dependent benchmark is marked not taken instead of creating a false machine failure. The catalog declares this requirement for agents and other clients.

### Limits
- The event must still be retained in its own Windows log, and a log-local RecordId can later refer to a different event; compare its timestamp with the original reference. Kernel-WHEA channel records still receive fixed-header interpretation only. This release does not infer a hardware cause.

## [1.3.0] - 2026-09-23

### Added
- Kernel-WHEA reports now have their own report-time timeline, separate from the System WHEA-Logger storm lead. The channel's event-query outcome, retained reach, 20,000-report limit, unreadable times and fixed CPER-header facts travel with it. PreviousError reports are marked as earlier-session conditions; a cluster of reports after restart is never called a burst of new hardware errors. The dashboard shows the separate trace and its coverage. Stack shares a bounded summary by default and keeps the full stored reading available on demand.

### Changed
- Power and Signals now describe HiberbootEnabled as a Fast Startup preference, not proof that hibernation is available or that a particular boot used it. A nonbinary value is unknown. The former “no cold start” lead, which could not be supported by the registry value and uptime, is removed; the observed uptime remains available in Power and Hardware.

### Limits
- The report timeline carries only fixed-header facts and event references. Exact CPER bytes are available on explicit unredacted request through the separate `whea` reading within its own newest-500-record limit; older timeline reports might not be in that reading. The channel's external detail decoder remains deferred. This release does not infer a hardware cause from either WHEA source.

## [1.2.0] - 2026-09-23

### Added
- Hardware errors now reads both System WHEA-Logger and the separate Kernel-WHEA/Errors CPER channel. The newest records are merged with exact log references, per-source failures and retained reach; safe CPER header facts show severity and previous-session reporting. The screen shows dates, source scope and an explicit on-demand exact binary readout; default redacted responses withhold CPER bytes, including copies in event properties. Stack selections carry header meaning beside the chosen row. Detailed external decoding of the newly included channel is deferred until real payloads pass an isolated safety check; the current storm trace names its System-only scope.
- Signals now counts unplanned stops from the Crash reading once, separates its returned stop limit from primary-source completeness, and keeps log anchors for the five newest returned stops, including report-only stops. The lead's former power-ledger `records` and `window` evidence fields are replaced by `returned`, `limit`, `limit_reached`, `sources_complete` and stop times. Each input's warning count and bounded warning excerpts remain visible in the Signals method and dashboard; answered inputs with warnings also appear in the outcome line.
- PCIe now keeps the full raw PCI device inventory when parent relations fail or are incomplete, reports source and placement coverage, and groups only devices with fully reported chains. Signals marks missing relations as a gap and limits shared-link leads to root ports; Diagnostics distinguishes root ports, direct non-PCI parents and unknown topology.
- Event, Record, WHEA and fault readings now report an exact record cutoff alongside their raw and decoded evidence. Time-window readings warn when the requested period extends past that cutoff. The Record frame uses this result to end paging precisely, keeps the reader's place as rows arrive, and widens progressively to avoid repeated small transfers. Large log Stack items default to a bounded summary, with all stored records available in full on demand. Numeric count limits are published in the catalog and MCP schema and rejected before event-log collection.
- An on-demand `changes` reading brings Windows Update results, device configuration records and MSI installation/removal results into one time-ordered answer before a chosen moment. Each log reports its own read outcome, retention reach and truncation. Raw rows keep only safe projected event fields with exact log and RecordId references; decoded entries label unsupported layouts and never treat proximity as a cause.
- Application crash and hang details now expose supported process IDs and interpreted creation times, with exact FILETIME values, source layout, independent validity and explicit limits. Unknown layouts and malformed values keep their raw evidence; inconsistent timestamps are flagged without being discarded.
- Recorded process identity in minidump inspection: process ID and creation time when their validity flags permit, with one-second time precision and the exact 24-byte metadata prefix retained for review. CPU times and unused fields remain in the raw evidence; PID reuse and timestamp precision still limit cross-record matching.
- Application dumps from the current Windows user's default CrashDumps directory, with explicit location coverage, bounded MDMP interpretation and raw structural evidence. Custom destinations and other accounts remain outside the discovery scope; application files cannot become reboot dump matches.
- Opaque dump inspection references shared by the dashboard and agents, so private paths stay redacted while files remain selectable. References resolve against fresh inventories and explain when a restart requires refreshing the list.
- An individual Signal lead can be added to the shared Stack by its string ID. Its composed handoff keeps the input basis and, in full detail, the rule evidence; a later observation of the same lead is a separate snapshot. Invalid or missing selection IDs are refused instead of producing an empty or misleading handoff.
- Continuous local performance history: one elected collector per data home keeps numeric-only aggregate processor, memory and disk samples across stops. The default interval is one minute, configurable to ten minutes; daily JSONL is capped and kept up to 30 days. A pause switch, clear action and last-attempt status are authenticated controls. `load` takes a fresh snapshot; `performance_history` gives agents exact rows and a derived window summary. The Performance view charts the returned samples with unconnected gaps, a keyboard-operable sample inspector, raw JSON and a window held before an investigation moment. A selected sample opens System records before its timestamp and remains selected on return; the held moment is shown with its time.
- A fresh `processes` reading names the returned process instances using Windows' CPU time, private memory and process I/O counters, with bounded raw rows and separately derived leaders. Performance opens with three visual rankings that lead to exact, searchable process rows; no process name enters the retained history.
- Crashes shows the last System record before restart, Windows' stop estimate and the next start as separate evidence points for each returned stop. A report-only stop leads with its filed time, bug check and matched dump while naming the missing System evidence. Selecting either kind opens its exact detail, record jump and stack action.
- Crashes now maps the faults returned in a reading by decoded kind, with application and live-kernel groups, kind filters that focus exact rows, the full derived summary and matching raw Application-log records inside each fault. The overview distinguishes raw log-record counts from decoded fault instances, since one live-kernel report can span several records.
- A selected minidump in Crashes now has a readable stream directory with each declared range and bounded metadata read status before the exact raw file readout. Later streams of a type already sampled are named as intentionally skipped, not mistaken for failed reads.
- A grouped, left-aligned navigation rail with distinct line icons. On phones, the current view stays visible above a keyboard-operable disclosure of every view and the device sign-in action.
- A day-by-day visual history in Crashes: Windows' stability index and returned reliability events share a UTC timeline. Selecting a day shows exact event types, the reported index, and the raw records; the same reading can be stacked for an agent.
- Dashboard addresses now remember the selected view and an investigation moment. Refresh, copied links and browser back/forward return to the right screen; links to sections that load after a reading answer land at the section, and view changes focus the new title.
- Record opens with a time-density view and leading sources for the returned System log rows. A bar or source opens and focuses one exact row below; the chart names its sample boundary instead of implying it covers the whole log.
- Diagnostics shows a memory map after the person takes the reading: installed capacity, reported slot population and each returned module's configured speed against its rating, with links to the exact derived and Windows fields below.
- On phones, Record keeps the selected level and window visible in one disclosure. Its full filter controls remain one tap away, letting the returned evidence appear sooner in the first viewport.
- Hardware errors now shows a visible legend for the Windows event levels present in returned records; each record's symbol also has a spoken level name.

### Changed
- Memory and Power now carry per-query outcomes. A failed battery query no longer claims mains, and failed module, wake-device, transition or memory-test queries no longer turn into empty facts. Power marks when its 120 returned transitions hit the limit; Memory reads all system-memory arrays and leaves error correction unknown when they disagree. Memory removes its redundant System-only WHEA ledger in favor of the dedicated Hardware errors reading. Agents reading `memory.derived.ledger` should use `whea`, and `memory.raw.array` is now `memory.raw.arrays`; several formerly definite derived fields and Memory's `count` can now be null when their source fails.
- Final app shutdown closes the PowerShell session pool and refuses later bridge questions without launching another process. Intentional benchmark and test resets explicitly reopen the pool. Tray quit gives in-flight readings the server's grace period before lifespan closes sessions; a reading still running then returns unavailable instead of starting another process.
- Final shutdown also interrupts PowerShell children that are still starting or answering through the one-shot transport, including a launch waiting for WSL's shared slot. A cancelled performance sample does not replace the last completed sample status with a shutdown artifact.
- Stack selections identify a record by its log as well as its RecordId when a reading combines Windows logs. Ambiguous numeric selections are refused, and older saved selections with that ambiguity explain it instead of composing another log's record. Crashes sends exact log-qualified selections.
- The signed driver inventory is labeled as a current inventory sorted by each driver's authored date; that date is not presented as an installation or change time.
- Live PowerShell startup failures keep a bounded WSL interop reason when its signature reaches stderr before the first probe can close. The health report does not expose the raw startup text.
- A PowerShell session that finishes starting after its pool shuts down is retired before it can serve a reading. A session closed between checkout and the next write can fall back to one-shot during an intentional pool reset; final app shutdown refuses the fallback. Shutdown accounting remains consistent in both races.
- Exact large integers survive the dashboard, agent responses, event stream, stack handoffs and new captures. Integers outside JavaScript's safe range are sent as decimal strings after redaction, keeping adjacent 64-bit timestamps and record identifiers distinct while internal collection and calculations retain integer values. Existing saved files remain intact.
- The fallback PowerShell transport sends collector scripts over standard input through a fixed-size bootstrap, so large queries remain available beyond Windows' command-line limit.
- Structured collectors must return exactly one object. Missing or ambiguous output now fails before derivation, preventing extra or absent collector objects from becoming misleading zero-findings readings. Payload warning extraction also preserves the original bridge answer.
- Dump inventory reports coverage for each Windows dump location, retains files found before an enumeration error, and distinguishes unreadable locations from observed empty ones. Crash matching and bounded dump inspection share that inventory, so a lookup failure cannot imply a reported dump was deleted. Crashes exposes the location results in place.
- Crash readings expose each event-log query's outcome and response bound. Failed logs no longer produce a false empty result, surviving reports remain available even for a requested moment, and incomplete queries cannot certify that a stop had no bug check. Each stop identifies whether its preceding-record lookup answered, failed, was denied or was not requested. Repeated-stop signals describe the evidence without inventing why no code was recorded.
- Reliability history reports each Windows source's outcome, matching and returned row counts, response limit and queried window. Failed or partially failed empty collections no longer claim that Windows kept no history. A surviving stability index remains available while unavailable event counts stay null through the daily rollup, Signals and dashboard. A failed device query also now returns its failure instead of claiming that no device has a problem.
- Crash stops now open their exact evidence directly beneath the selected stop instead of scrolling to a second list. Crashes remembers its counts, fault filter and selected source identities while visiting other views; returning takes fresh readings and restores the same evidence and keyboard focus when available. A missing selection is named explicitly. Dump and hardware-error rows follow source identity across refreshes, and driver rows without a unique identifier close when their source snapshot changes.
- Whole Signals readings and individual leads now keep later observations as separate Stack snapshots. Stack shows the reading's observation time beside the add time, and composed handoffs state the envelope's count without calling every kind of result a log record.
- Changing a reading's parameters now hides the previous answer immediately until the new question returns. Taking the same question again keeps its timestamped answer visible while refreshing; a transport failure clears it rather than leaving stale rows under an error.
- An empty Signals reading now says its conclusion is limited to inputs that answered, and calls out missing inputs in the outcome line. The input list and warnings still name each gap.
- The dashboard now distinguishes a failed connection from a rejected access token. A fresh page waits for the cheap catalog check, offers a focused retry when the service cannot answer, and reserves the wrong-token message for an actual 401 response.
- Reading outcome lines now use singular words for one returned item, including one dump file, record, sample, driver or signal.
- Every reading's Method panel now offers the complete JSON response that the dashboard received, with default redaction and the method, outcome, warnings, and all evidence sections intact. It renders only when opened and can be copied in one action, with manual selection when browser clipboard access is unavailable.
- Stack can download its composed handoff as a Markdown file beside Copy; both actions use the same already returned, redacted text.
- A keyboard skip link now moves straight to the active reading past the navigation controls.
- Method offers one-action copying for each returned query, with the same manual-selection fallback as the complete reading JSON.
- Capture lists now show the manifest's original capture time, redaction state and reading-outcome counts for each file. Missing, damaged or oversized manifests remain listed with their privacy and outcome status explicitly unknown; listing reads only a bounded manifest, never the archived readings.
- Stack can refresh its server-backed evidence, prompts, handoff and captures on demand after an agent or another device changes them.
- Reading section headers now explain what `raw`, `derived`, `invariant` and `inferred` mean in place, with a brief description and an expandable explanation for people learning how to judge the evidence.
- Record now leads with a plain-language question and a clearer sampled time-and-source panel, then opens a more legible exact row with its complete returned JSON. The tool's Field Instrument refinement increases the base reading sizes and keyboard focus visibility while keeping the same source and raw evidence paths.
- The source basis shown with decoded stops and faults now describes the event-manifest mapping without a fixed observation date in product copy.
- Crash descriptions now say precisely that the last System record is the last one before the next start. Its timestamp may be later than Windows' estimated stop and does not by itself establish cause.
- Network adapter inventory stays available when Windows cannot assemble IP configuration; affected `ip` fields are null with a reading warning instead of empty lists that suggest no addresses.
- The memory reading leaves installed capacity unknown if any module lacks a reported capacity, and leaves free slot count unknown when the array's total is missing, contradictory or unsupported by returned modules. Missing data no longer becomes zero capacity or zero free slots.
- Signals opens with a visual five-class lead map and input coverage, with each count linked to its evidence and no health score implied.
- Machine opens with a visual inventory of four selected components and gauges for processor load and free physical memory; each part leads to its detailed reading, with exact fields still available below. The hardware fingerprint is classified as derived, with its selection rule on the wire. The disk selected by Windows index 0 is labeled disk 0 and exposed as `disk0_model`, rather than being called the boot disk without evidence.
- The reliability reading now counts returned records by source **and event ID** in its derived daily rollup. The UI and API name informational records, including successful Windows updates, so event volume cannot masquerade as failure volume.
- The index-fall signal requires adjacent UTC days and carries the event types Windows returned. A missing day no longer assigns an unseen index change to the next returned day.
- WHEA decoding checks the CPER header, declared length and section bounds before starting the external decoder. Malformed records retain their raw payload and carry a per-record error. Identical payloads are decoded once per reading and still appear under each record's own ID. The synthetic screenshot server now serves structurally valid CPER data wherever it offers binary data, so reproducing screenshots does not feed short fixture tokens to the real decoder.

## [1.1.0] - 2026-09-21

The moment, answered in one call and one click. "It froze at 02:14" now has a reading that names the stop, the bug check, the dump and the machine's last word before it, and a dashboard where every time that frames an investigation is a place you can go to.

### Added
- A person-triggered tray update flow: check GitHub's latest stable release, confirm before download, verify the executable with GitHub's asset digest and the release's SHA-256 list, then start it through the existing handoff. `system-sentinel update` checks from a terminal; `--install` installs on Windows. No background checks or machine readings leave the computer.
- `crash`: the stops the machine did not plan, newest first, each one composed out of the records of its session — when it stopped as Windows estimated it (read from the EventLog 6008 record's binary value, not from its locale text), when it started again, the bug check if one was written and the parameters it carried, the dump file on disk that belongs to it and how it was matched to it, and the last record the machine managed before it went, with how long it had been quiet. Give it a moment instead of a count and it reports what the first start at or after that moment announced, which is the only thing that can answer for a freeze; a start that announced nothing is an answer too, and the reading says which start it was. One launch, however many stops it names.
- `faults`: what went wrong while the machine kept running — the programs that crashed or hung and the kernel's own live reports, a GPU timeout or a watchdog, each with its application, its module and its exception named, and one entry per report however many records Windows wrote it across.
- `reliability`: Windows' own events related to reliability, including informational entries, and the stability index it computes every hour, rolled up by day — where the index stood at each day's end, how low it went, and what Windows returned that day, by source. It is somebody else's arithmetic, which is exactly why it is worth having beside the tool's own readings: it can disagree, and a disagreement is a lead.
- **Crashes**, the view, in place of Crash dumps: the stops, the programs and the kernel's live reports, and the dump files, each reading with its own outcome line and each row opening in place on the facts behind it. Opening a dump now takes `dump_header` on demand: its format and recorded bug check where readable, or an explicit Windows denial.
- `dump_header` opens only a selected file from the Windows dump inventory and exposes bounded structural bytes and their offsets. A recognized x64 kernel header yields its stop code and four parameters; an MDMP stream minidump yields its directory, exception and system metadata, and up to 128 module records. An exception address is located within a recorded module range where possible, without treating that module as a cause. Crashes puts the interpreted facts first, with an in-place raw readout and one action to stack the same reading for an agent. The rest of the dump is not validated.
- The moment as somewhere to go. A stop, a fault, a dump file, a hardware-error record, a time inside a signal's evidence, a lit stretch of the hardware-error trace: each carries one control that frames the log around that instant — the records before it, oldest first, ending there, widened twenty-five at a time, with the way back beside them. The frame is held while you look at something else and is still there when you come back.
- `since` on `events` and `faults`: an ISO moment, or the word `boot` for this session only, answered by the log's own index rather than by a scan. Record and Hardware errors both offer it as a window.
- Two more signals, each a lead with its rule: stops that share a bug check code, and stops that wrote none, as one signal per group; and the day Windows' own stability index fell furthest below where the day before left it.

- The bridge answers from a pool of live PowerShell sessions instead of starting a process for every question. A question used to pay for a shell to start before any work happened, and for a light reading that was most of the cost; now the shell is already running. `Bridge.run` is unchanged — the same six outcomes, the same envelope — because the rules that decide an outcome were lifted into one function that both transports hand the same three facts. A session that overruns its question or dies is discarded and replaced, a question no session can take is launched the old way rather than lost, and `health` says which transport carried your questions and how the pool is doing. `docs/measurements/latency.md` has both transports measured side by side on one machine.
- `system-sentinel bench`: the tool measures itself. Every reading, N times, min, median and p95 of the envelope's own `took_ms`, with a reading that was not observed carrying its outcome rather than a time, because a failure is not a measurement.
- The MCP surface is the whole protocol rather than a tool list. Every tool is annotated with what it does to the machine, so a client can stop confirming harmless readings and keep confirming the two that remove things. Every reading answers with typed structured content beside its text against one shared schema, so an agent branches on `outcome` as a field instead of parsing a string. The six presets are MCP prompts; the catalog and the composed handoff are resources; captures are tools. Asking for an unredacted view now requires a reason, and the reason is carried into the answer's warnings.
- The evidence a reviewer checks in a minute: dependencies pinned in a hashed lock and installed with `--require-hashes`, `pip-audit` and `npm audit`, `ruff` and strict `mypy` over the boundary files, `eslint` with accessibility rules for the dashboard, CodeQL, an SBOM beside each released executable, coverage printed, and a test that fails the day the counts written in our own prose go stale.

### Changed
- A live PowerShell session now waits for both output streams to close within the question's timeout. An incomplete error stream makes the reading unavailable and retires the session; it can no longer make a failed query appear empty. Health reports a bounded category for the last session-start failure, so a one-shot fallback has a reason without exposing raw stderr. The bridge also recognizes WSL's socket-bind launch failure as unavailable and retries it. Redaction covers numeric and structured values under sensitive field names, not only strings.
- `memory` gives up the bug check half of its ledger — the stops are `crash`'s to report now — and gains the result of Windows' own memory test beside the time the System log's oldest record carries, so no result says how far back that reaches instead of implying the test was never run.
- `power` counts the log's own start and stop (EventLog 6005 and 6006) among its transitions.
- The MCP instructions lead with the moment: what is up, then the last stops or the stop a moment announced, then the record before it.
- Launches of `powershell.exe` are bounded: a process caps how many it has in flight, and under WSL a slot shared across every process that uses the bridge serializes them machine-wide, a test suite and a live dashboard included (the numbers live where they are defined in `sentinel/bridge.py`). A reading made of several others queues behind the same bridge rather than racing them, which under WSL's interop layer was how a reading came back `unavailable` for reasons that had nothing to do with the machine; a slot held by another process for longer than the launch's own timeout is reported the same way rather than waited for without end.

## [1.0.1] - 2026-09-21

The file looks after itself: it installs, updates and removes itself, says which version it is, and says so out loud when it cannot start.

### Added
- The executable installs and updates itself. Started from anywhere but its installed path it is a download: it copies itself into `%LOCALAPPDATA%\SystemSentinel\` and starts that copy. When a server is already answering with this machine's token it reads the `version` that server reports: equal to its own it just opens the dashboard, older it asks to quit and takes its place, newer it leaves alone and says so in a message box, because a download should not quietly undo an update.
- `POST /api/quit`: the bearer token only — a request carrying just the session cookie is refused, so a browser on any device cannot stop the machine's tool — and only from this machine. It answers `202 {"quitting": true}` and the process exits gracefully within a few seconds, under the tray and under `serve`.
- The version in the file's own properties, where Windows keeps it: `(Get-Item SystemSentinel.exe).VersionInfo.ProductVersion`.
- The tray's version submenu, named for the version it is running: *Check for updates…*, which opens the releases page in the browser and asks the network for nothing itself, and *Remove from this computer…*, which names what goes and what stays, asks once, turns *Start with Windows* off, quits and deletes its home behind itself.
- Failure is visible. A native message box whenever the windowed executable cannot start — the port held, a previous instance still stopping — or a second instance's browser did not answer, naming what happened, the address and where `launcher.log` is. The log rotates now, two backups behind it.
- Continuous integration on the repository, and a release workflow that builds the executable and attaches a build provenance attestation: `gh attestation verify SystemSentinel.exe -R SuperDuperDave/system-sentinel` checks a release from this version on.

### Changed
- The install note's short prompt is one prompt for install and update: it downloads to a temporary path, checks the published SHA-256, compares it with the installed copy and stops there when they are the same file and it is already running, and proves the result by the version the server reports against the version in the file. The long prompt looks for a server on the port before starting a second one. New sections cover updating, removing, which version you have, and a table of what to do about what you see.
- `version` on `GET /api/readings` is documented, and the dashboard's Agents view shows it beside a link to the releases page.
- The dashboard's sign-in screen names both ways in: the tray, which signs a browser in, and `system-sentinel token` from source. It said only the second, which is not the path most people are on.

## [1.0.0] - 2026-09-20

The overhaul. One boundary for three clients; the identity in the tool; nothing that reads the machine outside the API.

### Added
- `sentinel/`, the Python package: a typed bridge to Windows whose outcome is one of `ok`, `empty`, `failed`, `unavailable`, `denied`, `timeout`; the reading envelope with `raw`, `derived`, `invariant` and `inferred` sections and the query that produced it; a catalog of nineteen readings projected to REST, OpenAPI and MCP from one table.
- The access token and the session cookie; every route under `/api/` and the MCP endpoint require one.
- Redaction at the boundary: serial numbers, platform identifiers, the computer name, user names, MAC addresses and profile paths, with what was removed listed on every response; `unredacted` by name.
- The stack on the server: items with rank and verbosity, the prompt library seeded with the six presets, the composed handoff as a route an agent reads; captures of every reading with a manifest that says what is in the ZIP.
- The stream: one query per poll across the presets, `record`, `heartbeat` and `bridge` events, a longer poll while the machine is not answering.
- `storms` computed from the log on demand over wall-clock buckets, idle minutes included; the PCIe fabric read from `pnputil` in a fraction of a second.
- `dashboard/`, rebuilt in Vite and React with CSS modules and the identity (the tokens, Azeret Mono, JetBrains Mono and DM Sans self-hosted, the mark, the graticule in one zone, the phosphor as light only), served from the API's origin; eight views, each starting with the outcome line; phone width first.
- `tests/`: a fake `powershell.exe`, a fake bridge, fixtures for what this machine cannot show, and host tests against the real log.
- `docs/API.md` for agents; `docs/DEPLOY.md`, the prompts a person pastes to their agent: one for the published release, one from source, both run on a clean machine by the harness in `build/windows/sandbox/`.
- The launcher: `SystemSentinel.exe`, one file built by `build/windows/build.ps1`, which starts the server, opens the browser signed in by a one-time code and sits in the tray with Open dashboard, Sign in another device, Copy address for agents, Start with Windows and Quit; the person never reads the token.
- A sign-in link for another device: `POST /api/session/link` asks Tailscale which address it publishes for the port, mints a five-minute one-time code and draws the link as a QR code; the dashboard and the tray both show it.

### Removed
- The FastAPI backend and Next.js dashboard of 0.2.0 (`backend/`, `frontend/`, `start.sh`, `system_prompts.md`), whose queries were kept as the material of the readings. With them: open CORS, the two-port start, the in-memory WHEA store and its watermark, the hard-coded PCIe fixture and driver sentence in the composer, and every path that returned an empty list on failure.

## [0.2.0] - 2025-01-15

### Added
- WHEA storm detection and visualization
- Deep diagnostics with hardware domain analysis (memory, PCIe, power, forensic signals)
- Hardware topology tree with component-level views (CPU, GPU, storage, network, motherboard)
- System constraint analysis
- Context Composer for AI-augmented diagnostics
- Prompt Gallery with 6 specialist diagnostic modes
- Live event streaming via SSE

### Changed
- Expanded backend service architecture with domain-specific analyzers
- Improved frontend dashboard with tabbed deep diagnostics views

## [0.1.0] - 2024-12-01

### Added
- Initial release
- Windows event log collection via PowerShell/WMI
- WHEA error record decoding
- Crash dump inventory
- Basic dashboard with system overview
- Capture pack export
