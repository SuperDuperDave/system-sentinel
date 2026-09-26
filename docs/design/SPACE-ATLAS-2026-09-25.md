# Space Atlas — working design

Status: [interactive concept](space-atlas-prototype/index.html), using invented data. No filesystem scan or cleanup action is implemented by the accompanying prototype.

## The job

A person wants to answer three questions: **where is the space**, **what changed**, and **what could be investigated safely**. An agent needs the same observations with source, coverage and stable references. A large rectangle alone answers only the first question. This feature belongs in System Sentinel because unexplained disk pressure is a machine condition, and a selected folder can travel into an investigation handoff without the tool deciding what to delete.

## The surface

The top strip reconciles filesystem-reported used and free capacity with space attributed by a scan. The difference is labeled **unattributed**, never presented as a folder or as reclaimable. The main region pairs a spatial map with a ranked trail. The map gives quick proportion and a memorable location; the trail keeps labels, exact numbers and keyboard/phone navigation legible. Both represent the same current directory and the same selection. A click inspects in place. An explicit Open action enters a folder. Breadcrumbs, Back and a focus-preserving return recover position.

The map has three honest layers: area encodes measured allocation, hue encodes change between comparable scans, and hatch marks coverage gaps. The growth view ranks *delta*, not a file's age. Changing modes preserves the selection and root. A small area never implies a safe deletion. Every node details its logical length, allocated footprint, file count, last write, comparison basis and scan coverage when known. The scan timestamp remains visible on a phone. The prototype uses invented values to test the interaction grammar; its values are not measurements.

## Measurement contract before real data

- Run disk observations through the existing Windows bridge. Start with fixed volumes and a chosen subtree. Snapshot time, root identity and collection outcome must be explicit. Scan on demand; never start a whole-disk walk in the background by opening the dashboard.
- Volume used and free come from the filesystem. File logical length and allocated bytes are distinct. Compressed/sparse files and cloud placeholders may have a large length and small local allocation. Avoid file-content reads and placeholder hydration.
- On Windows, the [extended directory information record](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_extd_dir_info) can report allocation, length, reparse tag and file ID in one enumeration. Test its availability and field behavior on the host before choosing it over per-file metadata calls; do not label logical length as allocation when the native query fails.
- Never traverse a junction, symlink, mount point or other reparse directory. Record its presence and reason. Identify hardlinks by volume plus file identity and count their allocation once *within the observed scope*. This does not establish that removing one path would free those bytes; other links may exist outside it.
- Each subtree carries coverage: complete, access denied, skipped link, timeout, or other error, with counts and bounded examples. A partial aggregate is a **lower bound**. A failed root is not an empty root. A scan that cannot reconcile with volume used has an explicit unattributed remainder; no guessed allocation fills it.
- Compare scans only for the same volume/root and measurement method. A moved, omitted or unreadable subtree is incomparable, not growth or shrinkage. Save a small numeric/opaque snapshot locally only after an explicit scan; paths and names require an explicit local display or unredacted request. Standard capture, Stack and MCP redaction remain in force.
- A review basket contains references and current observations, not a claim of savings. Recheck paths, hardlinks, source control and application ownership at action time. No delete or trash action belongs in the first release.

## First real slice and acceptance

1. A bounded, cancellable scan of one explicitly chosen local root with allocated/logical totals, coverage, and top-level directory attribution. Expose one reading envelope to dashboard and MCP. This requires timing the host walk and choosing a viable scan cadence before locking the transport.
2. A volume ledger that makes attributed, unattributed, used and free add up without pretending the scan sees protected, shared or reserved storage. An opaque node reference supports an on-demand drill-down, so default agent responses and URLs do not carry local paths.
3. Verify a normal scan, an unreadable child, a link, a hardlink, a placeholder, and a scan that stops early on Windows. Check the dashboard on desktop and phone, by keyboard and with reduced motion. Inspect the actual API envelope and MCP response, including default redaction.

Potential later work: saved-scan growth, hotspots by file type/age, source-aware cleanup guides for Windows-managed storage, and a reversible plan handoff. Review state and possible savings are separate from measured occupancy. Estimates get visibly different texture and precision. An unreadable area without a reliable bound cannot be assigned a fictional tile area. Each addition must earn its place from real user and host observations.

## Reference and consultation

[disktree](https://github.com/tobi/disktree) already provides a treemap, smooth zoom, size/files/age modes, name filtering, reclaimable hints, mark/review, disk free space, and careful removal guards. The differentiation here is reconciliation, change, coverage, and a shared human/agent evidence model. A high-effort Claude Opus 5.5 consultation on 2026-09-25 argued for a volume ledger, readable drill-down and scan-to-scan growth. Buddy's scoped critique supported a survey metaphor, persistent scan time, visual certainty, a review queue and a bounded agent handoff. Its suggestion that reclaimable hints and marked review differentiate this design from disktree was incorrect against the reference's README. Both consultations are design input, not implementation evidence.
