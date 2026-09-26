# Space Atlas: data contract for the full experience

Status: implementation design. The live `space` and `space_page` readings cover one bounded walk and its immediate children. This document defines what the remaining views need before they can make measured claims. The [experience catalogue](SPACE-ATLAS-EXPERIENCE-2026-09-25.md) names the interactions.

## One measured world

Every view reads the same immutable **observation**: one scan ID, root, begin/end time, measurement method, coverage and node table. A node is a directory, file or explicit aggregate. Each directory has immediate children and an aggregate of what the scan actually listed beneath it. Area is observed allocated bytes, using one hardlink attribution order within that observation. A node with no measure has `allocated_bytes: null` and appears in the trail with its reason, never as a zero-area tile that pretends to be known. A partial node has a measured lower bound and a visible gap marker; the gap itself has no invented byte size.

The volume ledger is separate from the scanned hierarchy. `volume_used_bytes - attributed_allocated_bytes` is an unattributed remainder, not an ancestor folder. It includes storage outside the root, unlisted material and filesystem metadata. It cannot be entered or called reclaimable. If the subtraction is negative, keep it unknown and show the conflict.

The current collector already provides per-group allocated/logical bytes, file and folder counts, hardlink repeats, placeholder and compressed/sparse counts, byte-weighted last-write age bands, coverage status, largest-file leads and volume context. Its `scan_id` is ephemeral and `space_page` retains **immediate rows of one walk** for 30 minutes. A child `scope_id` takes a new walk with a new scan ID. The two observations can disagree through hardlink attribution and filesystem change; no cross-level reconciliation or growth claim is licensed yet.

**Never fold two walk IDs into one measured total.** A child collected later or with narrower coverage must remain a separate observation beside its parent, even if their names or directory identities agree. A retained hierarchy endpoint should reject a mixed-walk aggregation and return the distinct IDs, times and coverages. A comparison can return a byte delta only after the scope, volume, attribution method, identity and coverage checks below pass. This prevents an apparently exact parent total from silently absorbing a partial child collected at another time.

## Snapshot V2

The retained snapshot should have these independent records:

| Record | Required fields | Meaning |
| --- | --- | --- |
| Observation | `scan_id`, schema/method version, root scope, volume identity, `started_utc`, `finished_utc`, limits, coverage, source outcome | One bounded walk; the interval is not an atomic filesystem instant |
| Node | observation-local ID, parent ID, kind, opaque navigation reference, optional display name, observed measures, own coverage, flags | A node and its measurements only in that observation |
| Child page | parent ID, ordered node IDs, returned/total or unknown counts, next handle, aggregation rule | Pages from the same observation, never a silent rescan |
| Volume ledger | total/free/available/used, attributed allocation, unreconciled remainder or null | Filesystem facts adjacent to the selected scope |
| Evidence reference | scan ID, node ID, measure, method, coverage and optional row/file identity | What an agent can cite without a path |

IDs must not imply more stability than Windows provides. A 64-bit directory file ID and volume serial are useful evidence within a take, but a durable node match needs validation of the filesystem's extended ID, volume continuity, reuse and replacement. A renamed node can be matched across observations only with adequate identity evidence; otherwise it remains `possibly_moved` or incomparable. A path is display information, not a stable identity. Names and paths remain opt-in in responses and should not be persisted by default. Retention, bounds and clearing need a local settings surface before writing history.

An observation may stop inside a directory. Its already observed entries survive, and its child count or allocation is a lower bound. A failed root yields no snapshot. A saved snapshot must keep its original coverage and method even if a later scan completes. API and MCP continuations should name their scan ID and expiry; an expired handle asks for a new scan, never guesses a path.

## Comparison rules

Two observations support a net byte delta only when root, volume identity, measure definition, attribution policy and relevant coverage are comparable. The response should return `comparable: false` with specific reasons for a moved root, replaced volume, changed method, skipped subtree, unfinished walk or uncertain node identity. If only some children are comparable, show a measured subtotal and the incomparable rows separately; never calculate a whole-scope growth rate from the subtotal. Two timestamps show net change between observations, not the intervening churn. Missing intervals remain blank in Tideline and Pressure Map.

Last-write bands answer *when these observed bytes were last written*. They do not answer last access, usefulness, or likely savings. An Orbits radius may use the byte-weighted band distribution; the newest file's timestamp must not label a whole folder. A Core Sample can use hierarchy depth and these bands now, with the independent-walk warning across depths. Finer strata require a retained coherent hierarchy.

An action receipt should bind an explicit user action marker to observations before and after it. The receipt reports predicted ranges, actually observed free-space and subtree changes, elapsed time, coverage and confounders. It does not infer that the marker caused every difference. A move to the Recycle Bin, another hardlink, cloud behavior, cache regrowth and work by other processes can all make a deletion or move differ from a byte estimate.

## Gates for the more speculative lenses

| Lens | Real input beyond the current reading | When absent |
| --- | --- | --- |
| Terra Cognita | Scan progress events with measured nodes and honest stopped regions | Show the completed observation and coverage; do not animate fictitious surveying |
| Core Sample, Matter, Skyline, coarse Orbits, Watershed | Current hierarchy, logical/allocation, file counts and last-write bands | Enabled on the one walk; independent descents remain labeled |
| Tideline, net-growth Pressure Map, Regrowth Reef | Retained comparable observations and explicit action markers for regrowth | Show no history, not a fabricated curve |
| Metabolism, churn weather | Trusted change journal or explicit source/destination events with evidence of move identity | No flow arrows or churn rate |
| Packing Crate | Target drive capacity, filesystem, cluster size and expected compression/sparse handling | Scenario range, never a fit guarantee |
| Twin Stars | Explicitly chosen bounded content-hash job, with byte-read cost and no cloud hydration by default | No duplicate verdict from names, sizes or timestamps |
| Echo lines | Retained per-file exact size and last-write timestamp pairs from at least two distinct folders | Do not draw a connection from age bands. A same-size, same-time match is a weak metadata echo, never content identity or a savings estimate |
| Resonance | The same measured shares as the ranked list, initiated by the user | Silent; no autoplay or hidden exact values |

An agent citation should hold an observation ID, scope/node identity, observed value, method and coverage. **Pin and re-walk** opens that held reading in the navigator and takes a fresh, separately named observation of the same scope on request. It keeps the cited reading visible if the handle has expired or the new walk fails. A lower-coverage walk is explicitly incomparable; a comparable net change remains an observation, not proof of cause. The [synthetic lab](space-atlas-lab/index.html) exercises changed, lower-coverage, expired-handle and same-value outcomes without reading a disk.

All lenses share scope, selection, ancestor path, exact ranked values, coverage and observation time. A view switch never starts a scan. An Enter action on a folder must either navigate within one retained observation or clearly announce that it is taking a fresh walk. A file is terminal evidence, not a doorway to an invented world. The live dashboard follows the second rule today.
