import { useState, type ReactNode } from 'react';
import { EventRecord, Reading, type RecordId, observed } from '../api';
import { AddToStack } from '../AddToStack';
import { Glyph, OutcomeLine, clock, firstLine } from '../Outcome';
import { Facts, Head, MomentLink, RowList, Section, Segmented, Tree, Value, ago, basisOf, part, shortDay } from '../Sections';
import { useApp } from '../store';
import { useReading } from '../useReading';
import styles from './Errors.module.css';

/** The window counted into wall-clock buckets, idle ones included. */
interface TimelineBuckets {
  from: string;
  to: string;
  bucket_seconds: number;
  bucket_count: number;
  total: number;
  unplaced: number;
  totals: (number | null)[];
  unknown_buckets: number;
  active: { index: number; start: string; total: number; complete: boolean }[];
}

interface Buckets extends TimelineBuckets {
  active: (TimelineBuckets['active'][number] & { signatures: Record<string, number> })[];
}

interface ReportBuckets extends TimelineBuckets {
  previous_session: number;
  header_unreadable: number;
}

interface Coverage { system: { covered_from: string | null; covered_from_inclusive: boolean | null; complete: boolean | null } }
interface ReportCoverage { kernel_whea: { covered_from: string | null; covered_from_inclusive: boolean | null; complete: boolean | null } }

/** The burst and acceleration rules applied to those buckets: a lead, never a diagnosis. */
interface Status {
  state: string;
  severity: string | null;
  reason: string;
  peak_rate: number | null;
  observed_peak: number;
  recent_rate: number | null;
  baseline_rate: number | null;
  acceleration: number | null;
  dominant: string[];
  recent_buckets: number;
  baseline_buckets: number;
  recent_observed: number;
  baseline_observed: number;
}

interface Signature {
  id: string;
  key: string;
  description: string;
  error_type: string;
  bank: string | null;
  apic_id: string | null;
  mci_status: string | null;
  vendor_id: string | null;
  device_id: string | null;
  count: number;
  first_seen: string;
  last_seen: string;
  event_ids: number[];
  sample: { RecordId?: RecordId; TimeCreated?: string; Id?: number; LevelDisplayName?: string; Message?: string };
}

/** One entry of the decoded section: the CPER structure inside a record, or why there is none. */
interface Decoded {
  Log?: string;
  RecordId: RecordId | null;
  decoded?: unknown;
  error?: string;
}

interface WheaIdentity {
  Log: string;
  RecordId: RecordId;
  cper: {
    record_id: string;
    severity: string;
    previous_session: boolean;
    timestamp: string | null;
  } | null;
  error: string | null;
}

interface WheaSource {
  outcome: string;
  returned: number;
  truncated: boolean | null;
  stopped: { kind: string; detail: string } | null;
}

interface WheaCollection { sources: { system: WheaSource; kernel_whea: WheaSource } }
interface WheaReach { complete: boolean | null; shown: number; retained_from: string | null; enabled: boolean | null }
interface WheaCoverage { complete: boolean; sources: { system: WheaReach; kernel_whea: WheaReach } }

const KERNEL_WHEA = 'Microsoft-Windows-Kernel-WHEA/Errors';
const recordDay = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
const recordRef = (record: { Log?: string; RecordId: RecordId | null }) => `${record.Log ?? 'System'}:${record.RecordId}`;

function markerKind(record: EventRecord, identity?: WheaIdentity): 'critical' | 'error' | 'warning' | 'info' {
  const severity = identity?.cper?.severity;
  return severity === 'fatal' ? 'critical' : severity === 'recoverable' ? 'error' : severity === 'corrected' ? 'warning' : severity === 'informational' ? 'info' : levelKind(record.Level);
}

/** How far back the window reaches: a fixed span, or this session, which only the machine can say. */
type Span = number | 'boot';

const WINDOWS: { value: Span; label: string }[] = [
  { value: 24, label: '24 hours' },
  { value: 168, label: '7 days' },
  { value: 'boot', label: 'since boot' },
];
const COUNTS = [30, 100, 500];

/**
 * Hardware errors: what the firmware told Windows, and the shape of it over time.
 *
 * Three readings, each with its own outcome line, because they answer different questions and can
 * fail apart: `storms` counts System-log errors, `whea_reports` counts separate Kernel-WHEA report
 * times, and `whea` fetches the records themselves with their CPER headers. The status is a lead — it is typed
 * inferred, it carries its rule in the open, and it is never lit. The one lit thing on this page
 * is the trace, which is the window itself: one line over every wall-clock bucket, flat only
 * where covered buckets returned nothing and hatched where a quiet claim is unsupported.
 */
export function Errors() {
  const [span, setSpan] = useState<Span>(24);
  const [count, setCount] = useState(30);

  // The machine supplies uptime; both readings accept whole hours, so rounding up can include
  // some time before this session. Say that rather than calling the query an exact boot cutoff.
  const boot = span === 'boot';
  const system = useReading('system', {}, boot);
  const uptime = part<{ uptime_seconds: number | null }>(system.reading, 'snapshot')?.uptime_seconds ?? null;
  const bootHours = uptime == null ? null : Math.max(1, Math.ceil(uptime / 3600));
  const hours = boot ? bootHours : span;
  const windowLabel = boot ? (hours == null ? 'this session' : `${hours}-hour window around this session`) : WINDOWS.find((w) => w.value === span)?.label ?? `${span} hours`;
  const inWindow = boot ? `in the ${windowLabel}` : `in the last ${windowLabel}`;
  const windows = WINDOWS.map((w) => (w.value === 'boot' && bootHours !== null ? { value: w.value, label: `since boot · ${bootHours} h window` } : w));

  // The reading counts the window into buckets and refuses one that would take more than its cap,
  // so a long window asks for wider buckets: sixty seconds up to about two weeks, then five
  // minutes, a quarter hour, an hour. The trace reduces whatever it is given to the columns it
  // can draw, and the status names the bucket it was computed over.
  const bucketSeconds = hours === null ? 60 : bucketFor(hours);
  const storms = useReading('storms', hours === null ? { hours: 24 } : { hours, bucket_seconds: bucketSeconds }, hours !== null);
  const reports = useReading('whea_reports', hours === null ? { hours: 24 } : { hours, bucket_seconds: bucketSeconds }, hours !== null);
  const whea = useReading('whea', { count });

  const buckets = part<Buckets>(storms.reading, 'buckets');
  const status = part<Status>(storms.reading, 'status');
  const coverage = part<Coverage>(storms.reading, 'coverage')?.system;
  const emptyStormText = coverage?.complete
    ? `System log timeline: no WHEA-Logger records ${inWindow}`
    : coverage?.covered_from == null
      ? `System log timeline: no WHEA-Logger records returned ${inWindow}; log coverage could not be established`
      : `System log timeline: no WHEA-Logger records returned ${inWindow}; the window is not fully covered`;
  const signatures = part<Signature[]>(storms.reading, 'signatures') ?? [];
  const reportBuckets = part<ReportBuckets>(reports.reading, 'buckets');
  const reportCoverage = part<ReportCoverage>(reports.reading, 'coverage')?.kernel_whea;
  const emptyReportText = reportCoverage?.complete
    ? `No Kernel-WHEA reports recorded ${inWindow}`
    : reportCoverage?.covered_from == null
      ? `No Kernel-WHEA reports returned ${inWindow}; channel coverage could not be established`
      : `No Kernel-WHEA reports returned ${inWindow}; the channel does not cover the full window`;

  const records = part<EventRecord[]>(whea.reading, 'records') ?? [];
  const decoded = part<Decoded[]>(whea.reading, 'decoded') ?? [];
  const identities = part<WheaIdentity[]>(whea.reading, 'identity') ?? [];
  const identityByRef = new Map(identities.map((entry) => [recordRef(entry), entry]));
  const collection = part<WheaCollection>(whea.reading, 'collection');
  const wheaCoverage = part<WheaCoverage>(whea.reading, 'coverage');

  return (
    <section>
      <Head title="Hardware errors">
        <Segmented value={span} onChange={setSpan} options={windows} label="How far back" />
      </Head>
      {boot && hours === null ? (
        <p className={`${styles.windowNote} readout`}>
          {system.state === 'taking' || system.state === 'idle' ? (
            'Reading how long this session has been up, to fix the window…'
          ) : observed(system.reading) ? (
            <>
              <Glyph kind="warn" /> Since boot needs this machine's uptime, and the system reading carries no boot time. Choose a fixed window.
            </>
          ) : (
            <>
              <Glyph kind="warn" /> Since boot needs this machine's uptime, and the system reading was not observed. Choose a fixed window, or take it again from Machine.
            </>
          )}
        </p>
      ) : (
        <OutcomeLine taken={storms} noun={coverage?.complete ? 'WHEA-Logger records' : 'returned WHEA-Logger records'} singular={coverage?.complete ? 'WHEA-Logger record' : 'returned WHEA-Logger record'} emptyText={emptyStormText} />
      )}
      {hours !== null ? <p className={`${styles.windowNote} readout`}>This status uses the System log. Kernel-WHEA has its own report timeline below; a quiet System timeline cannot clear that channel.{boot ? ' The whole-hour window can include time before this session began.' : ''}</p> : null}

      {hours !== null && observed(storms.reading) && status ? (
        <Section
          title="Status"
          cls="inferred"
          basis={basisOf(storms.reading, 'status')}
          controls={storms.reading ? <AddToStack item={{ kind: 'reading', envelope: storms.reading, title: `Hardware error storms, ${windowLabel}` }} /> : null}
        >
          <p className={styles.status}>
            <span className={`${styles.state} readout`}>{status.state}</span>
            <span className={styles.reason}>{status.reason}</span>
          </p>
          <p className={`${styles.rates} readout`}>
            recent {status.recent_rate?.toFixed(2) ?? 'unknown'} · baseline {status.baseline_rate?.toFixed(2) ?? 'unknown'} · acceleration {status.acceleration == null ? 'unknown' : `${status.acceleration.toFixed(2)}×`} ·{' '}
            {status.peak_rate == null ? (status.observed_peak ? `at least ${status.observed_peak} returned in one bucket` : 'peak unknown; no records returned in recent buckets') : `peak ${status.peak_rate} in one bucket`} — records per bucket of {buckets?.bucket_seconds ?? 60} s;{' '}
            {status.recent_observed} of {status.recent_buckets} recent and {status.baseline_observed} of {status.baseline_buckets} baseline buckets observed
            {status.severity ? ` · severity ${status.severity}` : ''}
          </p>
          {status.dominant.length ? <p className={`${styles.rates} readout`}>most of it: {status.dominant.join(' · ')}</p> : null}
        </Section>
      ) : null}

      {hours !== null && observed(storms.reading) && buckets ? (
        <Section title="The window" cls="derived" basis={basisOf(storms.reading, 'buckets')}>
          <Trace buckets={buckets} />
        </Section>
      ) : null}

      {hours !== null && signatures.length ? (
        <Section title="Signatures" cls="derived" basis={basisOf(storms.reading, 'signatures')} note={`${signatures.length} distinct, most seen first`}>
          <RowList
            items={signatures}
            idOf={(signature) => signature.id}
            layout={styles.sigRow}
            cells={(s) => (
              <>
                <span className={`${styles.sigCount} readout`}>{s.count}×</span>
                <span className={styles.sigDescription}>{s.description}</span>
                <span className={`${styles.sigId} readout`}>{s.id}</span>
              </>
            )}
            inspect={(s) => <SignatureDetail signature={s} />}
          />
        </Section>
      ) : null}

      {hours !== null ? (
        <Section
          title="Kernel-WHEA reports"
          cls="derived"
          basis={basisOf(reports.reading, 'buckets')}
          controls={reports.reading ? <AddToStack item={{ kind: 'reading', envelope: reports.reading, title: `Kernel-WHEA reports, ${windowLabel}` }} /> : null}
        >
          <OutcomeLine taken={reports} noun={reportCoverage?.complete ? 'Kernel-WHEA reports' : 'returned Kernel-WHEA reports'} singular={reportCoverage?.complete ? 'Kernel-WHEA report' : 'returned Kernel-WHEA report'} emptyText={emptyReportText} />
          <p className={`${styles.windowNote} readout`}>This trace places reports when Windows wrote them. A CPER PreviousError flag means the hardware condition occurred in an earlier Windows session; a cluster here does not establish when those errors occurred.</p>
          {observed(reports.reading) && reportBuckets ? (
            <>
              <Trace buckets={reportBuckets} source="Kernel-WHEA reports" interactive={false} />
              <p className={`${styles.rates} readout`}>{reportBuckets.previous_session} returned report{reportBuckets.previous_session === 1 ? '' : 's'} marked previous session · {reportBuckets.header_unreadable} with unreadable headers · {reportBuckets.unknown_buckets} buckets with incomplete coverage</p>
            </>
          ) : null}
        </Section>
      ) : null}

      <Section
        title="Records"
        cls="raw"
        note="System and Kernel-WHEA/Errors, most recent first"
        controls={
          <>
            <Segmented value={count} onChange={setCount} options={COUNTS.map((c) => ({ value: c, label: `last ${c}` }))} label="How many records" />
            {whea.reading ? <AddToStack item={{ kind: 'reading', envelope: whea.reading, title: `Hardware error records, last ${count}` }} /> : null}
          </>
        }
      >
        <OutcomeLine taken={whea} noun="hardware error records" singular="hardware error record" emptyText="No hardware error records returned by either log" />
        {collection ? <WheaSources collection={collection} coverage={wheaCoverage} /> : null}
        {observed(whea.reading) && records.length > 0 ? (
          <div className={`${styles.levelLegend} readout`}>
            <span>Markers use CPER header severity when readable; otherwise Windows event level.</span>
            <ul aria-label="CPER severity markers">
              <li><Glyph kind="critical" /> fatal</li>
              <li><Glyph kind="error" /> recoverable</li>
              <li><Glyph kind="warning" /> corrected</li>
              <li><Glyph kind="info" /> informational</li>
            </ul>
          </div>
        ) : null}
        {observed(whea.reading) && records.length > 0 ? (
          <RowList
            items={records}
            idOf={recordRef}
            layout={styles.recordRow}
            cells={(r) => {
              const identity = identityByRef.get(recordRef(r));
              const severity = identity?.cper?.severity;
              return (
                <>
                  <span className={`${styles.time} readout`} title={r.TimeCreated}><span>{recordDay.format(new Date(r.TimeCreated))}</span><span>{clock.format(new Date(r.TimeCreated))}</span></span>
                  <span className={styles.level} title={severity ? `CPER severity: ${severity}` : `Windows event level: ${r.LevelDisplayName}`}>
                    <Glyph kind={markerKind(r, identity)} />
                    <span className={styles.srOnly}>{severity ? `CPER severity ${severity}` : r.LevelDisplayName || `Level ${r.Level}`}</span>
                  </span>
                  <span className={`${styles.eventId} readout`}>{r.Id}</span>
                  <span className={styles.message}>
                    <span className={`${styles.sourceLabel} readout`}>{r.Log === KERNEL_WHEA ? 'Kernel-WHEA' : 'System'}</span>{' '}
                    {r.Log === KERNEL_WHEA
                      ? identity?.cper ? `CPER header: ${severity} hardware error${identity.cper.previous_session ? ' · from a previous Windows session' : ''}` : 'Hardware error record · header unavailable'
                      : r.Message ? firstLine(r.Message) : <span className={styles.quiet}>no message text</span>}
                  </span>
                </>
              );
            }}
            inspect={(r) => <RecordDetail record={r} identity={identityByRef.get(recordRef(r))} decoded={decoded.find((d) => recordRef(d) === recordRef(r))} envelope={whea.reading} count={count} />}
          />
        ) : null}
      </Section>
    </section>
  );
}

function WheaSources({ collection, coverage }: { collection: WheaCollection; coverage: WheaCoverage | null }) {
  const sources = [
    { key: 'system' as const, label: 'System WHEA-Logger' },
    { key: 'kernel_whea' as const, label: 'Kernel-WHEA/Errors' },
  ];
  return (
    <div className={`${styles.sourceSummary} readout`}>
      <p>{sources.map(({ key, label }) => {
        const source = collection.sources[key];
        const shown = coverage?.sources[key]?.shown ?? source.returned;
        const reach = source.returned > shown ? ', more returned than shown' : source.truncated ? ', more retained' : source.stopped ? ', query stopped' : '';
        return `${label}: ${source.outcome === 'ok' || source.outcome === 'empty' ? `${shown} shown${reach}` : 'not observed'}`;
      }).join(' · ')}</p>
      <details>
        <summary>Source reach and limits</summary>
        <dl>
          {sources.map(({ key, label }) => {
            const source = collection.sources[key];
            const reach = coverage?.sources[key];
            const oldest = reach?.retained_from ? new Date(reach.retained_from) : null;
            const oldestText = oldest && !Number.isNaN(oldest.getTime()) ? recordDay.format(oldest) : 'not established';
            return (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{source.outcome} · oldest retained event: {oldestText} · {reach?.enabled === false ? 'log disabled' : reach?.complete === true ? 'all retained matches shown' : 'some matches or source reach may be missing'}</dd>
              </div>
            );
          })}
        </dl>
        <p>Windows logs have their own retention. An empty returned list says nothing about events older than each log keeps.</p>
      </details>
    </div>
  );
}

/**
 * The window as one line. The buckets are reduced to the columns the line can actually draw, by
 * the highest count in each group rather than the mean: a single minute that held a burst is the
 * whole point of looking, and an average would flatten it away. Quarter marks put a burst
 * somewhere in the window at a glance, and keep an empty window reading as a window rather than
 * as a blank.
 */
function Trace({ buckets, source = 'WHEA-Logger records', interactive = true }: { buckets: TimelineBuckets; source?: string; interactive?: boolean }) {
  const setMoment = useApp((s) => s.setMoment);
  const unknownPattern = interactive ? 'stormUnknown' : 'reportUnknown';
  const totals = buckets.totals ?? [];
  const active = new Map(buckets.active.map((bucket) => [bucket.index, bucket.total]));
  const width = 720;
  const height = 72;
  const quarters = 4;
  const columns = Math.max(1, Math.min(totals.length, 360));
  const per = totals.length / columns;
  const peaks = Array.from({ length: columns }, (_, i) => {
    const from = Math.floor(i * per);
    const to = Math.max(from + 1, Math.floor((i + 1) * per));
    let top = 0;
    let known = true;
    for (let j = from; j < to && j < totals.length; j += 1) {
      if (totals[j] === null) known = false;
      top = Math.max(top, totals[j] ?? active.get(j) ?? 0);
    }
    return { top, known, ends: Math.min(to, totals.length) };
  });
  const peak = peaks.reduce((a, b) => Math.max(a, b.top), 0);
  const floor = height - 3;
  const scale = peak > 0 ? (height - 6) / peak : 0;
  const x = (index: number) => (index / Math.max(1, columns - 1)) * width;
  const observedRuns: { from: number; to: number }[] = [];
  const unknownRuns: { from: number; to: number }[] = [];
  peaks.forEach((column, index) => {
    const group = column.known ? observedRuns : unknownRuns;
    const last = group[group.length - 1];
    if (last && last.to === index - 1) last.to = index;
    else group.push({ from: index, to: index });
  });
  const label = `${buckets.total} returned ${source} over ${buckets.bucket_count} buckets of ${buckets.bucket_seconds} seconds; coverage unknown for ${buckets.unknown_buckets} buckets; highest returned count ${peak} in one bucket`;

  // The lit stretches are the only ones worth going to, so they are the only ones that are a
  // control: a hit target over each run of columns that holds a record, and nothing at all over a
  // quiet one. The line itself is untouched; the targets are a layer above it.
  const unit = 100 / Math.max(1, columns - 1);
  const runs = (interactive ? lit(peaks) : [])
    .map((run) => ({ from: run.from, to: run.to, at: endOf(buckets, peaks[run.to].ends) }))
    .filter((run): run is { from: number; to: number; at: string } => run.at !== null);

  return (
    <figure className={styles.traceFigure}>
      <div className={styles.traceStage}>
        <svg className={styles.trace} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={label}>
          <defs>
            <pattern id={unknownPattern} width="7" height="7" patternUnits="userSpaceOnUse"><path className={styles.traceUnknownHatch} d="M0 7L7 0" /></pattern>
          </defs>
          {unknownRuns.map((run) => <rect key={`unknown-${run.from}`} className={styles.traceUnknown} style={{ fill: `url(#${unknownPattern})` }} x={(run.from / columns) * width} y="0" width={((run.to - run.from + 1) / columns) * width} height={height} />)}
          {Array.from({ length: quarters - 1 }, (_, i) => {
            const x = ((i + 1) / quarters) * width;
            return <line key={i} className={styles.traceTick} x1={x} y1="0" x2={x} y2={height} vectorEffect="non-scaling-stroke" />;
          })}
          {observedRuns.map((run) => run.from === run.to ? (
            <circle key={`observed-${run.from}`} className={styles.tracePoint} cx={x(run.from)} cy={floor - peaks[run.from].top * scale} r="2" />
          ) : (
            <polyline key={`observed-${run.from}`} className={styles.tracePath} points={peaks.slice(run.from, run.to + 1).map((p, offset) => `${x(run.from + offset).toFixed(1)},${(floor - p.top * scale).toFixed(1)}`).join(' ')} fill="none" vectorEffect="non-scaling-stroke" />
          ))}
          {peaks.map((p, i) => !p.known && p.top > 0 ? <circle key={`partial-${i}`} className={styles.tracePartialPoint} cx={x(i)} cy={floor - p.top * scale} r="2.5" /> : null)}
        </svg>
        {runs.length ? (
          <div className={styles.traceHits}>
            {runs.map((run) => {
              const when = new Date(run.at);
              const words = `the record before ${shortDay.format(when)} ${clock.format(when)}`;
              return (
                <button
                  key={run.from}
                  type="button"
                  className={styles.traceHit}
                  style={{ left: `${Math.max(0, run.from * unit - unit / 2)}%`, width: `${(run.to - run.from + 1) * unit}%` }}
                  onClick={() => setMoment(run.at)}
                  aria-label={words}
                  title={words}
                />
              );
            })}
          </div>
        ) : null}
      </div>
      <figcaption className={styles.traceScale}>
        <span className={`${styles.traceEnds} readout`}>
          <span>{clock.format(new Date(buckets.from))}</span>
          <span>now</span>
        </span>
        <span className={`${styles.tracePeak} readout`}>
          {peak === 0 ? (buckets.unknown_buckets ? `no returned records; coverage unknown for ${buckets.unknown_buckets} buckets` : `nothing in any of the ${buckets.bucket_count} buckets`) : `highest returned ${peak} in one bucket of ${buckets.bucket_seconds} s${buckets.unknown_buckets ? ` · coverage unknown for ${buckets.unknown_buckets} buckets` : ''}`}
          {buckets.unplaced ? ` · ${buckets.unplaced} returned records not bucketed` : ''}
        </span>
      </figcaption>
    </figure>
  );
}

/** The widest window each bucket size can count within the reading's cap of 20,000 buckets. */
const BUCKET_SIZES = [60, 300, 900, 3600];
const BUCKET_CAP = 20000;

function bucketFor(hours: number): number {
  return BUCKET_SIZES.find((seconds) => (hours * 3600) / seconds <= BUCKET_CAP) ?? BUCKET_SIZES[BUCKET_SIZES.length - 1];
}

/** The runs of columns that hold at least one record. Adjacent ones are one stretch, so a burst is
 *  a target you can hit rather than forty targets a pixel wide. */
function lit(peaks: { top: number }[]): { from: number; to: number }[] {
  const runs: { from: number; to: number }[] = [];
  for (let i = 0; i < peaks.length; i += 1) {
    if (peaks[i].top <= 0) continue;
    const last = runs[runs.length - 1];
    if (last && last.to === i - 1) last.to = i;
    else runs.push({ from: i, to: i });
  }
  return runs;
}

/** Where a column ends in wall-clock time, from the window's own start and bucket size. Never past
 *  the window's end, so the last column of a partly filled bucket does not point into the future. */
function endOf(buckets: TimelineBuckets, bucketIndex: number): string | null {
  const from = Date.parse(buckets.from);
  const to = Date.parse(buckets.to);
  if (Number.isNaN(from)) return null;
  const at = from + bucketIndex * buckets.bucket_seconds * 1000;
  return new Date(Number.isNaN(to) ? at : Math.min(at, to)).toISOString();
}

/** What the signature was made of, and the last record that matched it. */
function SignatureDetail({ signature }: { signature: Signature }) {
  return (
    <>
      <Facts
        rows={[
          ['Error type', <Value value={signature.error_type} />],
          ['Bank', <Value value={signature.bank} />],
          ['APIC id', <Value value={signature.apic_id} />],
          ['MCi status', <Value value={signature.mci_status} />],
          ['PCI vendor:device', signature.vendor_id ? <Value value={`${signature.vendor_id}:${signature.device_id}`} /> : <Value value={null} />],
          ['Event ids', <Value value={signature.event_ids} />],
          ['First returned', <Value value={`${signature.first_seen} · ${ago(signature.first_seen)}`} />],
          ['Last returned', <Value value={`${signature.last_seen} · ${ago(signature.last_seen)}`} />],
          ['Key', <span className={styles.key}>{signature.key}</span>],
        ]}
      />
      {signature.sample?.Message ? (
        <>
          <p className={`${styles.sampleLabel} label`}>The last record that matched · raw</p>
          <p className={styles.sample}>{signature.sample.Message}</p>
        </>
      ) : null}
    </>
  );
}

/** One source record in full, with the decoded structure of its payload beside it. */
function RecordDetail({ record, identity, decoded, envelope, count }: { record: EventRecord; identity?: WheaIdentity; decoded?: Decoded; envelope: Reading | null; count: number }) {
  const cper = identity?.cper;
  return (
    <>
      <Facts
        rows={[
          ['Provider', <Value value={record.ProviderName} />],
          ['Log', <Value value={record.Log ?? 'System'} />],
          ['Event', <Value value={record.TaskDisplayName ? `${record.Id} · ${record.TaskDisplayName}` : record.Id} />],
          ['Level', <Value value={record.LevelDisplayName} />],
          ['Record', <Value value={record.RecordId} />],
          ['Time', <Value value={`${record.TimeCreated} · ${ago(record.TimeCreated)}`} />],
          ...(cper ? [
            ['CPER severity', <Value value={cper.severity} />],
            ['CPER record', <Value value={cper.record_id} />],
            ['Previous session', <Value value={cper.previous_session ? 'The error occurred in an earlier Windows session; this event reports it after a restart' : 'Not marked as a previous-session error'} />],
          ] as [string, ReactNode][] : []),
        ]}
      />
      {identity?.error ? <p className={`${styles.notDecoded} readout`}><Glyph kind="warn" /> CPER header unavailable: {identity.error}</p> : null}
      {record.Message ? <p className={styles.fullMessage}>{record.Message}</p> : null}
      {cper ? (
        <details className={styles.decoded}>
          <summary className="label">CPER header facts · derived</summary>
          <div className={styles.decodedBody}><Tree value={cper} /></div>
        </details>
      ) : null}
      {decoded?.error ? (
        <p className={`${styles.notDecoded} readout`}>
          <Glyph kind="warn" /> Not decoded: {decoded.error}
        </p>
      ) : null}
      {decoded && 'decoded' in decoded && decoded.decoded ? (
        <details className={styles.decoded}>
          <summary className="label">Decoded structure · derived</summary>
          <div className={styles.decodedBody}>
            <Tree value={decoded.decoded} />
          </div>
        </details>
      ) : null}
      <RawReadout record={record} count={count} />
      <div className={styles.rowActions}>
        <MomentLink at={record.TimeCreated} />
        {envelope ? (
          <AddToStack item={{ kind: 'selection', envelope, ids: [recordRef(record)], title: `${record.Log === KERNEL_WHEA ? 'Kernel-WHEA' : 'System WHEA-Logger'} record ${record.RecordId}` }} label="Add this record to the stack" />
        ) : null}
      </div>
    </>
  );
}

function RawReadout({ record, count }: { record: EventRecord; count: number }) {
  const [requested, setRequested] = useState(false);
  const exact = useReading('whea', { count, unredacted: true }, requested);
  const matching = part<EventRecord[]>(exact.reading, 'records')?.find((candidate) => recordRef(candidate) === recordRef(record) && candidate.TimeCreated === record.TimeCreated);
  return (
    <details className={styles.decoded}>
      <summary className="label">Returned Windows fields · redacted</summary>
      <div className={styles.decodedBody}><Tree value={record} /></div>
      <button className={styles.exactRaw} type="button" onClick={() => requested ? exact.retake() : setRequested(true)} disabled={exact.state === 'taking'}>{requested ? 'Refresh exact readout' : 'Show exact returned fields and CPER bytes'}</button>
      {exact.state === 'taking' ? <p className={`${styles.notDecoded} readout`}>Reading exact fields from Windows…</p> : null}
      {exact.state === 'lost' || exact.reading && !observed(exact.reading) ? <p className={`${styles.notDecoded} readout`}>Exact readout unavailable: {exact.problem ?? exact.reading?.error?.detail ?? 'the source did not answer'}</p> : null}
      {requested && exact.state === 'done' && observed(exact.reading) && !matching ? <p className={`${styles.notDecoded} readout`}>This record is no longer in the newest {count} returned rows. Take a wider reading and select it again.</p> : null}
      {matching ? <div className={styles.exactRawBody}><p className="label">Exact Windows fields and CPER bytes · unredacted</p><div className={styles.decodedBody}><Tree value={matching} /></div></div> : null}
    </details>
  );
}

function levelKind(level: number): 'critical' | 'error' | 'warning' | 'info' {
  return level === 1 ? 'critical' : level === 2 ? 'error' : level === 3 ? 'warning' : 'info';
}
