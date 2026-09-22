import { ReactNode, useEffect, useRef, useState } from 'react';
import { AddToStack } from '../AddToStack';
import { EventRecord, Reading, observed } from '../api';
import { OutcomeLine, clock } from '../Outcome';
import { Basis, Facts, Head, MomentLink, RowList, Section, Segmented, Value, ago, basisOf, byDay, duration, part, size } from '../Sections';
import { useReading } from '../useReading';
import { useApp } from '../store';
import { ReliabilityHistory } from './ReliabilityHistory';
import styles from './Crashes.module.css';

/** What a bug check is, wherever the code was found: the record that named it says so. */
interface Bugcheck {
  code: string | null;
  name: string | null;
  parameters: string[];
  source?: string;
  bucket: string | null;
}

/** The dump that belongs to a stop, and how it was matched to it. A file the inventory no longer holds keeps its path and loses its size. */
interface Dump {
  name: string | null;
  path: string | null;
  bytes: number | null;
  modified: string | null;
  matched_by: string;
}

/** The last System record before the next start; it may be after Windows' stop estimate. */
interface LastRecord {
  RecordId: number | null;
  TimeCreated: string | null;
  ProviderName: string | null;
  Id: number | null;
  LevelDisplayName: string | null;
  Message: string | null;
}

interface Stop {
  started_at: string | null;
  announced_at: string | null;
  stopped_at: string | null;
  /** When Windows Error Reporting filed the report: the one time a stop the System log no longer holds still carries. */
  reported_at: string | null;
  down_seconds: number | null;
  bugcheck: Bugcheck | null;
  no_bugcheck_recorded: boolean;
  power: { sleep_in_progress?: unknown; power_button_timestamp?: unknown; whea_boot_error_count?: unknown; boot_app_status?: unknown; checkpoint?: unknown } | null;
  dump: Dump | null;
  last_record_before: LastRecord | null;
  quiet_seconds: number | null;
  records: { start: number | null; power_41: number | null; eventlog_6008: number | null; wer_1001: number | null; report: number[] };
}

/** One fault decoded: a program that crashed or hung, or one live kernel report folded from its records. */
interface Fault {
  RecordId: number;
  Log?: string;
  kind: string;
  fields: Record<string, unknown>;
  exception?: { code: string | null; name: string | null };
  report?: { id: string | null; code: string | null; name: string | null; parameters: string[]; bucket: string | null; dump_path: string | null; records: number[] };
}

interface FaultSummary {
  by_kind: Record<string, number>;
  applications: { name: string; count: number; first: string | null; last: string | null; modules: string[] }[];
  live_kernel: { code: string | null; name: string | null; bucket: string | null; count: number; last: string | null }[];
}

/** One file under the Windows minidump, full dump and live kernel report locations. */
interface DumpFile {
  name: string;
  path: string;
  bytes: number;
  modified: string;
}

interface DumpInspection {
  format: string;
  header_status: string;
  architecture?: string | null;
  bugcheck?: { code: string; name: string | null; parameters: string[] };
  directory_status?: string;
  streams?: number;
  thread_count?: number | null;
  module_count?: number | null;
  exception?: { thread_id: number; code: string; name: string | null; address: string; access?: { operation: string | null; address: string }; module_at_address?: { name: string | null; version: string | null; base_address: string; basis: string } } | null;
  system?: { architecture: string | null; windows_version: string; processors: number } | null;
  modules_read?: number | null;
  limit: string;
}

interface DumpStreamEntry {
  index: number;
  type: number;
  name: string;
  offset: number;
  bytes: number;
  range_status: string;
  sample?: { offset: number; bytes_read: number };
  sample_status?: string;
  recorded_count?: number;
  modules?: unknown[];
}

interface DumpStreams {
  offset: number;
  entries: DumpStreamEntry[];
}

const STOP_COUNTS = [5, 20];
const FAULT_COUNTS = [30, 100];
const STOP_STAMP = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });

const KIND_WORD: Record<string, string> = {
  'application crash': 'crash',
  'application hang': 'hang',
  'live kernel event': 'kernel',
};

/**
 * Crashes: the stops the machine did not plan, what went wrong while it kept running, and the
 * dumps on disk.
 *
 * Three readings, three outcome lines, because they answer three questions and fail apart: a
 * machine that has not stopped still has faults, and an empty dump inventory says something about
 * the configuration rather than about the stops. The order is the order of the founding question
 * — it froze at 02:14 — so the stops come first and every one of them offers the System record
 * before its restart and the evidence handed to the stack.
 *
 * Nothing here is styled as a verdict. A stop with no bug check reads as a stop with no bug check,
 * which is itself the finding; the bucket WER named is shown as WER's words, not as a cause.
 */
export function Crashes() {
  const { stopCount, faultCount, faultKind, stopId, faultId, dumpPath, focus } = useApp((s) => s.crashesView);
  const setCrashesView = useApp((s) => s.setCrashesView);
  const returnTo = useRef(focus);
  const stopButtons = useRef(new Map<number, HTMLButtonElement>());
  const faultRowsRef = useRef<HTMLDivElement>(null);
  const dumpRowsRef = useRef<HTMLDivElement>(null);
  const missingStopRef = useRef<HTMLParagraphElement>(null);
  const missingFaultRef = useRef<HTMLParagraphElement>(null);
  const missingDumpRef = useRef<HTMLParagraphElement>(null);

  const crash = useReading('crash', { count: stopCount });
  const faults = useReading('faults', { count: faultCount });
  const dumps = useReading('dumps');

  const stops = part<Stop[]>(crash.reading, 'stops') ?? [];
  const selectedStopIndex = stops.findIndex((stop) => stopIdentity(stop) === stopId);
  const selectedStop = selectedStopIndex < 0 ? null : selectedStopIndex;
  const faultRecords = part<EventRecord[]>(faults.reading, 'records') ?? [];
  const decoded = part<Fault[]>(faults.reading, 'decoded') ?? [];
  const faultSummary = observed(faults.reading) ? part<FaultSummary>(faults.reading, 'summary') : null;
  const selectedFaultKind = faultKind && faultSummary?.by_kind[faultKind] ? faultKind : null;
  const shownFaults = selectedFaultKind ? decoded.filter((fault) => fault.kind === selectedFaultKind) : decoded;
  const files = part<DumpFile[]>(dumps.reading, 'files') ?? [];
  const times = new Map(faultRecords.map((r) => [r.RecordId, r.TimeCreated]));

  // Returning takes fresh readings. Reopen only the same source identity, and return keyboard
  // focus after it arrives. A missing item gets a visible explanation rather than another row.
  useEffect(() => {
    const destination = returnTo.current;
    if (!destination) return;
    const taken = destination === 'stop' ? crash : destination === 'fault' ? faults : dumps;
    if (taken.state === 'idle' || taken.state === 'taking') return;
    returnTo.current = null;
    if (!observed(taken.reading)) return;
    const root = destination === 'fault' ? faultRowsRef.current : dumpRowsRef.current;
    const id = destination === 'fault' ? faultId : dumpPath;
    const row = destination === 'stop'
      ? selectedStop === null ? null : stopButtons.current.get(selectedStop)
      : [...(root?.querySelectorAll<HTMLButtonElement>('button[data-row-id]') ?? [])].find((button) => button.dataset.rowId === id);
    const missing = destination === 'stop' ? missingStopRef.current : destination === 'fault' ? missingFaultRef.current : missingDumpRef.current;
    const target = row ?? missing;
    target?.scrollIntoView({ block: 'center' });
    target?.focus({ preventScroll: true });
  }, [crash, faults, dumps, selectedStop, faultId, dumpPath]);

  function chooseStop(index: number | null) {
    returnTo.current = null;
    setCrashesView({ stopId: index === null ? null : stopIdentity(stops[index]), focus: index === null ? null : 'stop' });
  }

  function chooseFaultKind(kind: string | null) {
    if (!faults.reading) return;
    returnTo.current = null;
    setCrashesView({ faultKind: kind, faultId: null, focus: null });
    requestAnimationFrame(() => {
      const target = faultRowsRef.current?.querySelector<HTMLButtonElement>('ol > li > button');
      (target ?? faultRowsRef.current)?.scrollIntoView({ block: 'center' });
      (target ?? faultRowsRef.current)?.focus({ preventScroll: true });
    });
  }

  return (
    <section onPointerDownCapture={() => { returnTo.current = null; }} onKeyDownCapture={() => { returnTo.current = null; }} onWheelCapture={() => { returnTo.current = null; }}>
      <Head title="Crashes">
        <Segmented value={stopCount} onChange={(count) => setCrashesView({ stopCount: count })} options={STOP_COUNTS.map((c) => ({ value: c, label: `last ${c}` }))} label="How many stops" />
        {crash.reading ? <AddToStack item={{ kind: 'reading', envelope: crash.reading, title: `Unplanned stops, last ${stopCount}` }} label="Stack this reading" /> : null}
      </Head>
      <OutcomeLine taken={crash} noun="stops" singular="stop" emptyText="No unplanned stop among the starts read" />
      {observed(crash.reading) && stopId && selectedStop === null ? <p ref={missingStopRef} className={`${styles.selectionMissing} readout`} role="status" tabIndex={-1}>The previously selected stop is not in this returned reading.</p> : null}

      {observed(crash.reading) && stops.length > 0 ? (
        <Section title="Stops" cls="derived" basis={basisOf(crash.reading, 'stops')} note="newest first">
          <StopSequence
            stops={stops}
            selected={selectedStop}
            envelope={crash.reading}
            onInspect={(index) => chooseStop(selectedStop === index ? null : index)}
            registerButton={(index, node) => { if (node) stopButtons.current.set(index, node); else stopButtons.current.delete(index); }}
          />
        </Section>
      ) : null}

      <ReliabilityHistory />

      <Section
        title="Programs and the kernel's live reports"
        cls="derived"
        basis={basisOf(faults.reading, 'decoded')}
        controls={
          <>
            <Segmented value={faultCount} onChange={(count) => setCrashesView({ faultCount: count })} options={FAULT_COUNTS.map((c) => ({ value: c, label: `last ${c}` }))} label="How many records" />
            {faults.reading ? <AddToStack item={{ kind: 'reading', envelope: faults.reading, title: `Faults, last ${faultCount}` }} /> : null}
          </>
        }
      >
        <OutcomeLine taken={faults} noun="records" singular="record" emptyText="No application crash, hang or live kernel report in the Application log" />
        {observed(faults.reading) && faultId && !shownFaults.some((fault) => faultIdentity(fault) === faultId) ? <p ref={missingFaultRef} className={`${styles.selectionMissing} readout`} role="status" tabIndex={-1}>The previously selected fault is not in this returned reading.</p> : null}
        {observed(faults.reading) && decoded.length > 0 ? faultSummary ? (
          <FaultOverview summary={faultSummary} rawCount={faultRecords.length} decodedCount={decoded.length} basis={basisOf(faults.reading, 'summary')} selected={selectedFaultKind} onChoose={chooseFaultKind} />
        ) : <p className={`${styles.faultSummaryMissing} readout`}>The derived fault summary was not returned; the decoded entries remain below.</p> : null}
        {observed(faults.reading) && decoded.length > 0 ? (
          <div className={styles.faultRows} ref={faultRowsRef} tabIndex={-1} aria-label="Decoded fault instances in this returned sample">
          <p className={`${styles.faultRowsCount} readout`}>{shownFaults.length} of {decoded.length} decoded fault {decoded.length === 1 ? 'instance' : 'instances'} shown{selectedFaultKind ? ` · ${faultKindLabel(selectedFaultKind)}` : ' · all kinds'}</p>
          <RowList
            items={shownFaults}
            idOf={faultIdentity}
            openId={faultId}
            onOpenChange={(id) => { returnTo.current = null; setCrashesView({ faultId: id === null ? null : String(id), focus: id === null ? null : 'fault' }); }}
            layout={styles.faultRow}
            cells={(f) => (
              <>
                <span className={`${styles.time} readout`}>{at(times.get(f.RecordId))}</span>
                <span className={`${styles.kind} readout`}>{KIND_WORD[f.kind] ?? f.kind}</span>
                <span className={styles.app}>{appOf(f)}</span>
                <span className={`${styles.module} readout`}>{moduleOf(f)}</span>
                <span className={`${styles.exception} readout`}>{exceptionOf(f)}</span>
              </>
            )}
            inspect={(f) => <FaultDetail fault={f} at={times.get(f.RecordId)} envelope={faults.reading} rawRecords={faultRecords} />}
          />
          </div>
        ) : null}
      </Section>

      <Section
        title="Dump files"
        cls="raw"
        note={files.length ? `newest first · ${size(files.reduce((n, f) => n + f.bytes, 0))} on disk` : undefined}
        controls={dumps.reading ? <AddToStack item={{ kind: 'reading', envelope: dumps.reading, title: 'Crash dump inventory' }} /> : null}
      >
        <OutcomeLine taken={dumps} noun="dump files" singular="dump file" emptyText="No dump files under the Windows dump locations" />
        {observed(dumps.reading) && dumpPath && !files.some((file) => file.path === dumpPath) ? <p ref={missingDumpRef} className={`${styles.selectionMissing} readout`} role="status" tabIndex={-1}>The previously selected dump file is not in this returned inventory.</p> : null}
        <div ref={dumpRowsRef}>
        {observed(dumps.reading) && files.length > 0
          ? byDay(files, (f) => f.modified).map(([label, rows]) => (
              <div key={label}>
                <p className={`${styles.day} label`}>{label}</p>
                <RowList
                  items={rows}
                  idOf={(file) => file.path}
                  openId={dumpPath}
                  onOpenChange={(id) => { returnTo.current = null; setCrashesView({ dumpPath: id === null ? null : String(id), focus: id === null ? null : 'dump' }); }}
                  layout={styles.fileRow}
                  cells={(f) => (
                    <>
                      <span className={`${styles.fileName} readout`}>{f.name}</span>
                      <span className={`${styles.fileSize} readout`}>{size(f.bytes)}</span>
                      <span className={`${styles.fileWhen} readout`}>
                        {at(f.modified)} · {ago(f.modified)}
                      </span>
                    </>
                  )}
                  inspect={(f) => (
                    <>
                      <Facts
                        rows={[
                          ['Path', <span className={styles.path}>{f.path}</span>],
                          ['Size', `${f.bytes.toLocaleString()} bytes`],
                          ['Written', f.modified],
                        ]}
                      />
                      <DumpHeaderDetail path={f.path} />
                      <div className={styles.actions}>
                        <MomentLink at={f.modified} />
                      </div>
                    </>
                  )}
                />
              </div>
            ))
          : null}
        </div>
      </Section>
    </section>
  );
}

/** Prefer the System start's record identity; report-only stops retain their report identity. */
function stopIdentity(stop: Stop): string {
  if (stop.records.start != null) return `start:${stop.records.start}:${stop.started_at}`;
  if (stop.records.power_41 != null) return `power:${stop.records.power_41}:${stop.announced_at}`;
  return JSON.stringify([stop.started_at, stop.announced_at, stop.reported_at, stop.records.report]);
}

function faultIdentity(fault: Fault): string {
  return `${fault.Log ?? 'Application'}:${fault.RecordId}`;
}

/** A map of the returned decoded instances, not a count of all faults on the machine. */
function FaultOverview({ summary, rawCount, decodedCount, basis, selected, onChoose }: {
  summary: FaultSummary;
  rawCount: number;
  decodedCount: number;
  basis: string | null;
  selected: string | null;
  onChoose: (kind: string | null) => void;
}) {
  const kinds = Object.entries(summary.by_kind);
  const peak = Math.max(1, ...kinds.map(([, count]) => count));
  return (
    <section className={styles.faultOverview} aria-labelledby="fault-overview-title">
      <div className={styles.faultOverviewHead}>
        <div><p className="label">Faults · derived summary</p><h3 id="fault-overview-title" className="display">What the returned records describe</h3></div>
        <p>{decodedCount} decoded fault {decodedCount === 1 ? 'instance' : 'instances'} from {rawCount} returned Application-log {rawCount === 1 ? 'record' : 'records'}. Windows can write one live-kernel report across several records; these totals answer different questions.</p>
      </div>
      {basis ? <div className={styles.faultBasis}><Basis text={basis} /></div> : null}
      <div className={styles.faultChoices} role="group" aria-label="Show decoded fault instances by kind">
        <button type="button" className={`${styles.faultChoice} ${selected === null ? styles.faultChoiceSelected : ''}`} aria-pressed={selected === null} onClick={() => onChoose(null)}>
          <span className="readout">All returned kinds</span><strong>{decodedCount}</strong><span className={`${styles.faultChoiceAction} readout`}>Show exact rows ↓</span>
        </button>
        {kinds.map(([kind, count]) => <button key={kind} type="button" className={`${styles.faultChoice} ${selected === kind ? styles.faultChoiceSelected : ''}`} aria-pressed={selected === kind} onClick={() => onChoose(kind)}>
          <span className="readout">{faultKindLabel(kind)}</span><strong>{count}</strong><span className={styles.faultChoiceBar} aria-hidden="true"><span style={{ width: `${(count / peak) * 100}%` }} /></span><span className={`${styles.faultChoiceAction} readout`}>Show exact rows ↓</span>
        </button>)}
      </div>
      <div className={styles.faultGroups}>
        <div>
          <h4>Programs in this sample</h4>
          <p className="readout">Crashes and hangs together</p>
          {summary.applications.length ? <ol>{summary.applications.slice(0, 3).map((app) => <li key={app.name}><span>{app.name}</span><strong className="readout">{app.count}</strong></li>)}</ol> : <p className={styles.faultNone}>None named in the returned instances.</p>}
          {summary.applications.length > 3 ? <p className={`${styles.faultMore} readout`}>+{summary.applications.length - 3} more in the full summary</p> : null}
        </div>
        <div>
          <h4>Live-kernel reports in this sample</h4>
          <p className="readout">Grouped by code and bucket</p>
          {summary.live_kernel.length ? <ol>{summary.live_kernel.slice(0, 3).map((item, index) => <li key={`${item.code}:${item.bucket}:${index}`}><span>{[item.code, item.name, item.bucket].filter(Boolean).join(' · ') || 'Unnamed report'}</span><strong className="readout">{item.count}</strong></li>)}</ol> : <p className={styles.faultNone}>None in the returned instances.</p>}
          {summary.live_kernel.length > 3 ? <p className={`${styles.faultMore} readout`}>+{summary.live_kernel.length - 3} more in the full summary</p> : null}
        </div>
      </div>
      <details className={styles.faultSummaryRaw}>
        <summary className="readout">Full derived summary · exact returned fields</summary>
        <pre className="readout">{JSON.stringify(summary, null, 2)}</pre>
      </details>
    </section>
  );
}

function faultKindLabel(kind: string): string {
  return kind === 'application crash' ? 'Program crashes' : kind === 'application hang' ? 'Program hangs' : kind === 'live kernel event' ? 'Live-kernel reports' : kind;
}

/** Three labeled points from each returned stop, with no claim that they form a timed line. */
function StopSequence({ stops, selected, envelope, onInspect, registerButton }: {
  stops: Stop[];
  selected: number | null;
  envelope: Reading | null;
  onInspect: (index: number) => void;
  registerButton: (index: number, node: HTMLButtonElement | null) => void;
}) {
  return (
    <section className={styles.sequence} aria-labelledby="stop-sequence-title">
      <div className={styles.sequenceHead}>
        <div><p className="label">Returned stops · crash reading</p><h3 id="stop-sequence-title" className="display">Evidence around each stop</h3></div>
        <p>Separate evidence points in your browser’s local time. The last System record before restart can be later than Windows’ stop estimate; these times do not establish a cause. A report without a returned session shows its report evidence.</p>
      </div>
      <ol className={styles.sequenceList}>
        {stops.map((stop, index) => {
          const last = stop.last_record_before;
          const relation = recordToEstimate(last?.TimeCreated, stop.stopped_at);
          const reportOnly = Boolean(stop.reported_at && !last && !stop.stopped_at && !stop.started_at && !stop.announced_at);
          return <li key={stopIdentity(stop)}>
            <button
              ref={(node) => registerButton(index, node)}
              type="button"
              className={`${styles.sequenceButton} ${selected === index ? styles.sequenceSelected : ''}`}
              onClick={() => onInspect(index)}
              aria-expanded={selected === index}
              aria-controls={`stop-detail-${index}`}
            >
              <span className={styles.sequenceLabel}><span className="readout">{String(index + 1).padStart(2, '0')} / returned stop</span><span className="readout">{selected === index ? 'Hide exact stop' : 'Inspect exact stop'}</span></span>
              {!reportOnly ? <span className={styles.sequenceFinding}>
                <strong>{[stop.bugcheck?.name, stop.bugcheck?.code].filter(Boolean).join(' · ') || (stop.no_bugcheck_recorded ? 'No bug check recorded' : 'No bug check named')}</strong>
                {stop.down_seconds == null ? null : <span className="readout">down {howLong(stop.down_seconds)}</span>}
                {stop.dump?.name ? <span className="readout">{stop.dump.name}</span> : null}
              </span> : null}
              {reportOnly ? <span className={styles.reportOnly}>
                <span className={styles.reportMain}><span className="label">Windows error report filed</span><strong className="readout">{stamp(stop.reported_at)}</strong></span>
                <span className={styles.reportFacts}>
                  <span><span className="label">Bug check</span><strong className="readout">{[stop.bugcheck?.name, stop.bugcheck?.code].filter(Boolean).join(' · ') || 'Not named'}</strong></span>
                  <span><span className="label">Matched dump</span><strong className="readout">{stop.dump?.name ?? 'None matched'}</strong></span>
                </span>
                <span className={`${styles.reportLimits} readout`}>No last System record before restart, Windows stop estimate, or next start was returned for this report.</span>
              </span> : <><span className={styles.sequencePhases}>
                <span className={styles.phase}>
                  <span className={`${styles.phaseLabel} label`}>Last System record before restart</span>
                  <strong className="readout">{last ? stamp(last.TimeCreated, 'Time not recorded') : 'Not recorded'}</strong>
                  {last ? <span className={styles.phaseNote}>{[last.ProviderName, last.Id == null ? null : `event ${last.Id}`].filter(Boolean).join(' · ') || 'Source not recorded'}</span> : null}
                  {relation ? <span className={styles.phaseRelation}>{relation}</span> : null}
                </span>
                <span className={styles.phase}>
                  <span className={`${styles.phaseLabel} label`}>Windows stop estimate</span>
                  <strong className="readout">{stamp(stop.stopped_at)}</strong>
                  <span className={styles.phaseNote}>{stop.stopped_at ? 'Read from EventLog 6008' : 'No estimate in the returned records'}</span>
                </span>
                <span className={styles.phase}>
                  <span className={`${styles.phaseLabel} label`}>Next start</span>
                  <strong className="readout">{stamp(stop.started_at)}</strong>
                  <span className={styles.phaseNote}>{stop.started_at ? 'Kernel-General start record' : 'No start in the returned records'}</span>
                </span>
              </span>
              {stop.reported_at ? <span className={`${styles.reported} readout`}>Report filed {stamp(stop.reported_at)}{!stop.started_at && !stop.stopped_at ? ' · only report timing is available' : ''}</span> : null}</>}
            </button>
            <div id={`stop-detail-${index}`} className={selected === index ? styles.sequenceDetail : undefined}>
              {selected === index ? <StopDetail stop={stop} envelope={envelope} /> : null}
            </div>
          </li>;
        })}
      </ol>
    </section>
  );
}

function stamp(value: string | null | undefined, missing = 'Not recorded'): string {
  if (!value) return missing;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? missing : STOP_STAMP.format(date);
}

function recordToEstimate(recordAt: string | null | undefined, estimateAt: string | null): string | null {
  if (!recordAt || !estimateAt) return null;
  const record = Date.parse(recordAt);
  const estimate = Date.parse(estimateAt);
  if (!Number.isFinite(record) || !Number.isFinite(estimate)) return null;
  if (record === estimate) return 'Same reported time as the estimate';
  return record > estimate ? 'Record time after the estimate' : 'Record time before the estimate';
}

/**
 * One stop in full: what Windows estimated, what it recorded, the dump it matched and the last
 * System record before restart. Then the two moves that follow — the record before the next
 * start and the stop itself onto the stack.
 */
function StopDetail({ stop, envelope }: { stop: Stop; envelope: Reading | null }) {
  const moment = stop.started_at ?? stop.announced_at ?? stop.reported_at;
  const ids = recordIds(stop);
  const rows: [string, ReactNode][] = [
    ['Stopped at', stop.stopped_at ? <Value value={`${stop.stopped_at} · ${ago(stop.stopped_at)}`} /> : <Value value={null} />],
    ['Started at', stop.started_at ? <Value value={`${stop.started_at} · ${ago(stop.started_at)}`} /> : <Value value={null} />],
    ['Announced at', <Value value={stop.announced_at} />],
    ['Reported at', <Value value={stop.reported_at} />],
    ['Down for', stop.down_seconds == null ? <Value value={null} /> : <Value value={howLong(stop.down_seconds)} />],
  ];
  if (stop.bugcheck) {
    rows.push(['Bug check', <Value value={[stop.bugcheck.code, stop.bugcheck.name].filter(Boolean).join(' · ')} />]);
    rows.push(['Parameters', <Value value={stop.bugcheck.parameters} />]);
    rows.push(['Named by', <Value value={stop.bugcheck.source ?? null} />]);
    rows.push(['Bucket', <Value value={stop.bugcheck.bucket} />]);
  } else {
    rows.push([
      'Bug check',
      stop.no_bugcheck_recorded ? <span className={styles.quiet}>none recorded: the Kernel-Power 41 carried bug check code 0</span> : <Value value={null} />,
    ]);
  }
  rows.push(['Dump', stop.dump ? <Value value={stop.dump.name} /> : <span className={styles.quiet}>none matched</span>]);
  if (stop.dump) {
    rows.push(['Dump file', <span className={styles.path}>{stop.dump.path}</span>]);
    rows.push(['Dump size', stop.dump.bytes == null ? <span className={styles.quiet}>the report named it; the inventory does not hold it</span> : <Value value={size(stop.dump.bytes)} />]);
    rows.push(['Matched by', <Value value={stop.dump.matched_by} />]);
  }
  if (stop.last_record_before) {
    const last = stop.last_record_before;
    rows.push(['Last System record before restart', <Value value={[last.TimeCreated, last.ProviderName, last.Id, last.LevelDisplayName].filter((v) => v != null).join(' · ')} />]);
    rows.push(['Until next start', stop.quiet_seconds == null ? <Value value={null} /> : <Value value={howLong(stop.quiet_seconds)} />]);
  }
  // The one field of the 41 that points somewhere else: hardware errors counted at that boot are
  // the Errors view's subject, and a stop that carries them is worth reading there too.
  if (stop.power?.whea_boot_error_count != null) rows.push(['WHEA errors at boot', <Value value={stop.power.whea_boot_error_count} />]);

  return (
    <>
      <Facts rows={rows} />
      {stop.dump?.path && stop.dump.bytes != null ? <DumpHeaderDetail path={stop.dump.path} /> : null}
      {stop.last_record_before?.Message ? <p className={styles.lastRecordMessage}>{stop.last_record_before.Message}</p> : null}
      <div className={styles.actions}>
        <MomentLink at={moment} />
        {envelope && ids.length ? (
          <AddToStack item={{ kind: 'selection', envelope, ids, title: `Stop at ${moment ?? stop.stopped_at ?? 'an unknown time'}` }} label="Stack this stop" />
        ) : null}
      </div>
    </>
  );
}

/** Read just the selected file's header when the person opens its detail. */
function DumpHeaderDetail({ path }: { path: string }) {
  const taken = useReading('dump_header', { path });
  const [rawOpen, setRawOpen] = useState(false);
  const info = part<DumpInspection>(taken.reading, 'inspection');
  const streams = part<DumpStreams>(taken.reading, 'streams');
  const raw = taken.reading?.sections.filter((section) => section.class === 'raw') ?? [];
  const rows: [string, ReactNode][] = info ? [
    ['Format', info.format],
    ['Header', info.header_status],
  ] : [];
  if (info?.architecture) rows.push(['Architecture', info.architecture]);
  if (info?.bugcheck) {
    rows.push(['In file: bug check', [info.bugcheck.code, info.bugcheck.name].filter(Boolean).join(' · ')]);
    rows.push(['In file: parameters', <Value value={info.bugcheck.parameters} />]);
  }
  if (info?.directory_status) rows.push(['Stream directory', `${info.directory_status} · ${info.streams ?? 0} listed`]);
  if (info?.exception) {
    rows.push(['In file: exception', [info.exception.code, info.exception.name].filter(Boolean).join(' · ')]);
    rows.push(['Thread · address', `${info.exception.thread_id} · ${info.exception.address}`]);
    if (info.exception.access) rows.push(['Access', `${info.exception.access.operation ?? 'unknown'} · ${info.exception.access.address}`]);
    if (info.exception.module_at_address) {
      const module = info.exception.module_at_address;
      rows.push(['Address in module', [module.name ?? module.base_address, module.version].filter(Boolean).join(' · ')]);
      rows.push(['Range match', module.basis]);
    }
  }
  if (info?.system) rows.push(['In file: system', `${info.system.architecture ?? 'unknown architecture'} · Windows ${info.system.windows_version} · ${info.system.processors} processors`]);
  if (info?.thread_count != null || info?.module_count != null) rows.push(['In file: counts', `${info.thread_count ?? '?'} threads · ${info.module_count ?? '?'} modules`]);
  if (info?.modules_read != null && info.module_count !== info.modules_read) rows.push(['Module records read', info.modules_read]);
  if (info) rows.push(['Limit', info.limit]);
  return (
    <>
      <p className="label">Inside the dump</p>
      <OutcomeLine taken={taken} noun="dump inspection" emptyText="This file is no longer in the dump inventory" />
      {observed(taken.reading) && info ? <Facts rows={rows} /> : null}
      {observed(taken.reading) && info?.directory_status ? <DumpStreamDirectory status={info.directory_status} declared={info.streams} streams={streams} /> : null}
      {observed(taken.reading) && raw.length > 0 ? (
        <details className={styles.rawDisclosure} onToggle={(event) => setRawOpen(event.currentTarget.open)}>
          <summary>Raw file readout</summary>
          {rawOpen ? <pre>{JSON.stringify(raw, null, 2)}</pre> : null}
        </details>
      ) : null}
      {taken.reading ? <div className={styles.actions}><AddToStack item={{ kind: 'reading', envelope: taken.reading, title: 'Dump inspection', verbosity: 'summary' }} label="Stack this dump inspection" /></div> : null}
    </>
  );
}

const DIRECTORY_STATUS: Record<string, string> = {
  ok: 'Directory read',
  invalid_entries: 'Directory read · some ranges outside file',
  outside_file: 'Directory outside file',
  invalid_offset: 'Invalid directory offset',
  incomplete: 'Directory read incomplete',
  limit: 'Declared count exceeds read limit',
};

const FIXED_SAMPLE_BYTES: Record<number, number> = { 3: 4, 4: 4, 6: 168, 7: 32 };

function streamRange(status: string): string {
  if (status === 'within_file') return 'Declared range within file';
  if (status === 'outside_file') return 'Declared range outside file';
  if (status === 'empty') return 'Empty · no bytes declared';
  return `Unknown · ${status || 'no status returned'}`;
}

function streamSample(entry: DumpStreamEntry): string {
  if (entry.range_status === 'outside_file') return 'Not read · range outside file';
  if (entry.range_status === 'empty') return 'Not read · empty stream';
  if (entry.range_status !== 'within_file') return 'Unknown · range status unavailable';
  const minimum = FIXED_SAMPLE_BYTES[entry.type];
  if (minimum === undefined) return 'Not sampled · this type has no bounded metadata read';
  if (entry.sample_status === 'skipped_duplicate') return 'Not sampled · earlier stream of this type was selected';
  if (entry.sample_status === 'incomplete') return 'Metadata sample changed or was truncated';
  const read = entry.sample?.bytes_read;
  if (read === undefined) return 'Metadata sample unavailable';
  if (read < minimum) return `Truncated fixed metadata · ${read} of ${minimum} bytes read`;
  if (entry.type === 4 && entry.recorded_count !== undefined) {
    if (entry.recorded_count > 128) return `${read.toLocaleString()} metadata bytes read · module count exceeds 128-record limit`;
    if (!entry.modules) return `${read.toLocaleString()} metadata bytes read · module list incomplete`;
  }
  return `${read.toLocaleString()} metadata ${read === 1 ? 'byte' : 'bytes'} read${read < entry.bytes ? ' · bounded prefix' : ''}`;
}

/** The directory is a map of declared ranges and a few sampled prefixes, not a file validation. */
function DumpStreamDirectory({ status, declared, streams }: { status: string; declared?: number; streams: DumpStreams | null }) {
  const entries = streams?.entries ?? [];
  const directoryStatus = DIRECTORY_STATUS[status] ?? `Unknown directory status · ${status}`;
  return (
    <details className={styles.streamDirectory}>
      <summary>Stream directory <span className={styles.streamDirectoryStatus}>{directoryStatus} · {entries.length} of {declared ?? '?'} entries returned</span></summary>
      <div className={styles.streamDirectoryBody}>
        <p>{streams ? `The file header points to this directory at offset ${streams.offset.toLocaleString()}. ` : ''}Each size and offset comes from the directory. Only selected fixed metadata prefixes were read; these ranges do not validate the dump contents.</p>
        {entries.length ? <ol className={styles.streamEntries}>
          {entries.map((entry) => {
            const range = streamRange(entry.range_status);
            const sample = streamSample(entry);
            return <li key={entry.index}>
              <div className={styles.streamEntryHead}>
                <strong>{entry.name === `stream ${entry.type}` ? 'Unknown stream name' : entry.name}</strong>
                <span className="readout">#{entry.index + 1} · type {entry.type}</span>
              </div>
              <dl className={styles.streamEntryFacts}>
                <div><dt>Declared</dt><dd className="readout">{entry.bytes.toLocaleString()} bytes at offset {entry.offset.toLocaleString()}</dd></div>
                <div><dt>Range</dt><dd className={entry.range_status === 'outside_file' ? styles.streamIssue : undefined}>{range}</dd></div>
                <div><dt>Metadata</dt><dd className={sample.startsWith('Truncated') || sample.includes('incomplete') || sample.includes('unavailable') ? styles.streamIssue : undefined}>{sample}</dd></div>
              </dl>
            </li>;
          })}
        </ol> : <p className={styles.streamNoEntries}>{declared === 0 && status === 'ok' ? 'No streams declared.' : 'No directory entries were returned for inspection.'}</p>}
      </div>
    </details>
  );
}

/** One fault named field by field, in the words of the record it came from, and the moment it happened. */
function FaultDetail({ fault, at: moment, envelope, rawRecords }: { fault: Fault; at?: string; envelope: Reading | null; rawRecords: EventRecord[] }) {
  const f = fault.fields;
  const rawIds = fault.report?.records?.length ? fault.report.records : [fault.RecordId];
  const matchingRaw = rawRecords.filter((record) => rawIds.includes(record.RecordId));
  const missingRaw = rawIds.filter((id) => !matchingRaw.some((record) => record.RecordId === id));
  const rows: [string, ReactNode][] = [];
  if (fault.report) {
    rows.push(['Code', <Value value={[fault.report.code, fault.report.name].filter(Boolean).join(' · ') || null} />]);
    rows.push(['Bucket', <Value value={fault.report.bucket} />]);
    rows.push(['Parameters', <Value value={fault.report.parameters} />]);
    rows.push(['Dump', fault.report.dump_path ? <span className={styles.path}>{fault.report.dump_path}</span> : <Value value={null} />]);
    rows.push(['Report id', <Value value={fault.report.id} />]);
    rows.push(['Written across', <Value value={fault.report.records} />]);
  } else {
    rows.push(['Application', <Value value={text(f.AppName) ?? text(f.ExeFileName)} />]);
    rows.push(['Version', <Value value={text(f.AppVersion)} />]);
    if (fault.kind === 'application crash') {
      rows.push(['Module', <Value value={text(f.ModuleName)} />]);
      rows.push(['Module version', <Value value={text(f.ModuleVersion)} />]);
      rows.push(['Exception', <Value value={[fault.exception?.code, fault.exception?.name].filter(Boolean).join(' · ') || text(f.ExceptionCode)} />]);
      rows.push(['Faulting offset', <Value value={text(f.FaultingOffset)} />]);
      rows.push(['Path', f.AppPath ? <span className={styles.path}>{String(f.AppPath)}</span> : <Value value={null} />]);
      rows.push(['Module path', f.ModulePath ? <span className={styles.path}>{String(f.ModulePath)}</span> : <Value value={null} />]);
    } else {
      rows.push(['Started', <Value value={text(f.StartTime)} />]);
      rows.push(['Terminated', <Value value={text(f.TerminationTime)} />]);
      rows.push(['Hang type', <Value value={text(f.HangType)} />]);
      rows.push(['Report id', <Value value={text(f.ReportId)} />]);
    }
    rows.push(['Process id', <Value value={text(f.ProcessId)} />]);
  }
  rows.push(['Time', <Value value={moment ?? null} />]);
  rows.push(['Record', <Value value={fault.RecordId} />]);

  return (
    <>
      <Facts rows={rows} />
      {matchingRaw.length ? <details className={styles.rawDisclosure}>
        <summary>Raw {matchingRaw.length === 1 ? 'record' : 'records'} · {matchingRaw.length} of {rawIds.length} returned</summary>
        <pre className="readout">{JSON.stringify(matchingRaw.length === 1 ? matchingRaw[0] : matchingRaw, null, 2)}</pre>
      </details> : <p className={`${styles.faultRawMissing} readout`}>No matching raw record was returned in this reading.</p>}
      {missingRaw.length ? <p className={`${styles.faultRawMissing} readout`}>Raw {missingRaw.length === 1 ? 'record' : 'records'} {missingRaw.join(', ')} {missingRaw.length === 1 ? 'was' : 'were'} named by this decoded entry but not returned.</p> : null}
      <div className={styles.actions}>
        <MomentLink at={moment} />
        {envelope ? (
          <AddToStack
            item={{ kind: 'selection', envelope, ids: fault.report?.records ?? [fault.RecordId], title: `${KIND_WORD[fault.kind] ?? fault.kind} at ${moment ?? 'an unknown time'}` }}
            label="Stack this record"
          />
        ) : null}
      </div>
    </>
  );
}

/** Every record the stop was composed from, so stacking it hands over the evidence and not the conclusion. */
function recordIds(stop: Stop): number[] {
  const { start, power_41, eventlog_6008, wer_1001, report } = stop.records;
  return [start, power_41, eventlog_6008, wer_1001, ...(report ?? [])].filter((id): id is number => typeof id === 'number');
}

function appOf(fault: Fault): string {
  if (fault.report) return fault.report.name ?? fault.report.code ?? 'live kernel report';
  return text(fault.fields.AppName) ?? text(fault.fields.ExeFileName) ?? 'unnamed';
}

function moduleOf(fault: Fault): string {
  if (fault.report) return fault.report.bucket ?? '';
  return text(fault.fields.ModuleName) ?? '';
}

function exceptionOf(fault: Fault): string {
  if (fault.report) return '';
  return fault.exception?.name ?? fault.exception?.code ?? text(fault.fields.ExceptionCode) ?? '';
}

function text(value: unknown): string | null {
  const s = typeof value === 'string' ? value.trim() : value == null ? '' : String(value);
  return s ? s : null;
}

/** A local time, or a dash where the record carries no moment. */
function at(moment: string | null | undefined): string {
  const t = moment ? new Date(moment) : null;
  return t && !Number.isNaN(t.getTime()) ? clock.format(t) : '—';
}

/** How long something lasted. Under a minute and a half the seconds are the point: a machine down
 *  40 seconds restarted, and "0 min" would lose that. */
function howLong(seconds: number): string {
  return seconds < 90 ? `${seconds} s` : duration(seconds);
}
