import { useState } from 'react';
import { EventRecord, Reading, observed } from '../api';
import { AddToStack } from '../AddToStack';
import { Glyph, OutcomeLine, clock, firstLine } from '../Outcome';
import { Facts, Head, MomentLink, RowList, Section, Segmented, Tree, Value, ago, basisOf, part, shortDay } from '../Sections';
import { useApp } from '../store';
import { useReading } from '../useReading';
import styles from './Errors.module.css';

/** The window counted into wall-clock buckets, idle ones included. */
interface Buckets {
  from: string;
  to: string;
  bucket_seconds: number;
  bucket_count: number;
  total: number;
  unplaced: number;
  totals: number[];
  active: { index: number; start: string; total: number; signatures: Record<string, number> }[];
}

/** The burst and acceleration rules applied to those buckets: a lead, never a diagnosis. */
interface Status {
  state: string;
  severity: string | null;
  reason: string;
  peak_rate: number;
  recent_rate: number;
  baseline_rate: number;
  acceleration: number;
  dominant: string[];
  recent_buckets: number;
  baseline_buckets: number;
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
  sample: { RecordId?: number; TimeCreated?: string; Id?: number; LevelDisplayName?: string; Message?: string };
}

/** One entry of the decoded section: the CPER structure inside a record, or why there is none. */
interface Decoded {
  RecordId: number | null;
  decoded?: unknown;
  error?: string;
}

/** How far back the window reaches: a fixed span, or this session, which only the machine can say. */
type Span = number | 'boot';

const WINDOWS: { value: Span; label: string }[] = [
  { value: 24, label: '24 hours' },
  { value: 168, label: '7 days' },
  { value: 'boot', label: 'since boot' },
];
const COUNTS = [30, 100];

/**
 * Hardware errors: what the firmware told Windows, and the shape of it over time.
 *
 * Two readings, each with its own outcome line, because they answer different questions and can
 * fail apart: `storms` counts the window and applies the burst rules, `whea` fetches the records
 * themselves with the CPER payload decoded beside each one. The status is a lead — it is typed
 * inferred, it carries its rule in the open, and it is never lit. The one lit thing on this page
 * is the trace, which is the window itself: one line over every wall-clock bucket, flat when the
 * machine reported nothing, which on a healthy machine is what it should be.
 */
export function Errors() {
  const [span, setSpan] = useState<Span>(24);
  const [count, setCount] = useState(30);

  // Since boot is the machine's number, not the dashboard's: the window is only as long as this
  // session has been up, so the snapshot is taken when that option is chosen and not before.
  const boot = span === 'boot';
  const system = useReading('system', {}, boot);
  const uptime = part<{ uptime_seconds: number | null }>(system.reading, 'snapshot')?.uptime_seconds ?? null;
  const bootHours = uptime == null ? null : Math.max(1, Math.ceil(uptime / 3600));
  const hours = boot ? bootHours : span;
  const windowLabel = boot ? (hours == null ? 'since boot' : `since boot, ${hours} hours`) : WINDOWS.find((w) => w.value === span)?.label ?? `${span} hours`;
  const inWindow = boot ? windowLabel : `in the last ${windowLabel}`;
  // Once the machine has said how long it has been up, the option says how long the window is:
  // "since boot" is a question until then and an answer afterwards. Only the machine's number
  // goes on it, never the fixed span that happens to be chosen.
  const windows = WINDOWS.map((w) => (w.value === 'boot' && bootHours !== null ? { value: w.value, label: `since boot · ${bootHours} h` } : w));

  // The reading counts the window into buckets and refuses one that would take more than its cap,
  // so a long window asks for wider buckets: sixty seconds up to about two weeks, then five
  // minutes, a quarter hour, an hour. The trace reduces whatever it is given to the columns it
  // can draw, and the status names the bucket it was computed over.
  const bucketSeconds = hours === null ? 60 : bucketFor(hours);
  const storms = useReading('storms', hours === null ? { hours: 24 } : { hours, bucket_seconds: bucketSeconds }, hours !== null);
  const whea = useReading('whea', { count });

  const buckets = part<Buckets>(storms.reading, 'buckets');
  const status = part<Status>(storms.reading, 'status');
  const signatures = part<Signature[]>(storms.reading, 'signatures') ?? [];

  const records = part<EventRecord[]>(whea.reading, 'records') ?? [];
  const decoded = part<Decoded[]>(whea.reading, 'decoded') ?? [];
  const shownLevels = [...new Map(records.map((r) => [r.Level, r.LevelDisplayName] as const)).entries()].sort(([a], [b]) => a - b);

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
        <OutcomeLine taken={storms} noun="WHEA-Logger records" singular="WHEA-Logger record" emptyText={`No WHEA-Logger records ${inWindow}`} />
      )}

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
            recent {status.recent_rate.toFixed(2)} · baseline {status.baseline_rate.toFixed(2)} · acceleration {status.acceleration.toFixed(2)}× · peak{' '}
            {status.peak_rate} in one bucket — records per bucket of {buckets?.bucket_seconds ?? 60} s, over the last {status.recent_buckets} buckets
            against the {status.baseline_buckets} before them
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

      <Section
        title="Records"
        cls="raw"
        note="WHEA-Logger, most recent first"
        controls={
          <>
            <Segmented value={count} onChange={setCount} options={COUNTS.map((c) => ({ value: c, label: `last ${c}` }))} label="How many records" />
            {whea.reading ? <AddToStack item={{ kind: 'reading', envelope: whea.reading, title: `WHEA-Logger records, last ${count}` }} /> : null}
          </>
        }
      >
        <OutcomeLine taken={whea} noun="WHEA-Logger records" singular="WHEA-Logger record" emptyText="No WHEA-Logger records in the System log" />
        {observed(whea.reading) && records.length > 0 ? (
          <div className={`${styles.levelLegend} readout`}>
            <span>Windows event levels</span>
            <ul>
              {shownLevels.map(([level, label]) => <li key={level}><Glyph kind={levelKind(level)} />{label || `Level ${level}`}</li>)}
            </ul>
          </div>
        ) : null}
        {observed(whea.reading) && records.length > 0 ? (
          <RowList
            items={records}
            layout={styles.recordRow}
            cells={(r) => (
              <>
                <span className={`${styles.time} readout`}>{clock.format(new Date(r.TimeCreated))}</span>
                <span className={styles.level} title={r.LevelDisplayName}>
                  <Glyph kind={levelKind(r.Level)} />
                  <span className={styles.srOnly}>{r.LevelDisplayName || `Level ${r.Level}`}</span>
                </span>
                <span className={`${styles.eventId} readout`}>{r.Id}</span>
                <span className={styles.message}>{r.Message ? firstLine(r.Message) : <span className={styles.quiet}>no message text</span>}</span>
              </>
            )}
            inspect={(r) => <RecordDetail record={r} decoded={decoded.find((d) => d.RecordId === r.RecordId)} envelope={whea.reading} />}
          />
        ) : null}
      </Section>
    </section>
  );
}

/**
 * The window as one line. The buckets are reduced to the columns the line can actually draw, by
 * the highest count in each group rather than the mean: a single minute that held a burst is the
 * whole point of looking, and an average would flatten it away. Quarter marks put a burst
 * somewhere in the window at a glance, and keep an empty window reading as a window rather than
 * as a blank.
 */
function Trace({ buckets }: { buckets: Buckets }) {
  const setMoment = useApp((s) => s.setMoment);
  const totals = buckets.totals ?? [];
  const width = 720;
  const height = 72;
  const quarters = 4;
  const columns = Math.max(1, Math.min(totals.length, 360));
  const per = totals.length / columns;
  const peaks = Array.from({ length: columns }, (_, i) => {
    const from = Math.floor(i * per);
    const to = Math.max(from + 1, Math.floor((i + 1) * per));
    let top = 0;
    for (let j = from; j < to && j < totals.length; j += 1) top = Math.max(top, totals[j]);
    return { top, ends: Math.min(to, totals.length) };
  });
  const peak = peaks.reduce((a, b) => Math.max(a, b.top), 0);
  const floor = height - 3;
  const scale = peak > 0 ? (height - 6) / peak : 0;
  const points = peaks.map((p, i) => `${((i / Math.max(1, columns - 1)) * width).toFixed(1)},${(floor - p.top * scale).toFixed(1)}`).join(' ');
  const label = `${buckets.total} WHEA-Logger records over ${buckets.bucket_count} buckets of ${buckets.bucket_seconds} seconds; highest ${peak} in one bucket`;

  // The lit stretches are the only ones worth going to, so they are the only ones that are a
  // control: a hit target over each run of columns that holds a record, and nothing at all over a
  // quiet one. The line itself is untouched; the targets are a layer above it.
  const unit = 100 / Math.max(1, columns - 1);
  const runs = lit(peaks)
    .map((run) => ({ from: run.from, to: run.to, at: endOf(buckets, peaks[run.to].ends) }))
    .filter((run): run is { from: number; to: number; at: string } => run.at !== null);

  return (
    <figure className={styles.traceFigure}>
      <div className={styles.traceStage}>
        <svg className={styles.trace} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={label}>
          {Array.from({ length: quarters - 1 }, (_, i) => {
            const x = ((i + 1) / quarters) * width;
            return <line key={i} className={styles.traceTick} x1={x} y1="0" x2={x} y2={height} vectorEffect="non-scaling-stroke" />;
          })}
          <polyline className={styles.tracePath} points={points} fill="none" vectorEffect="non-scaling-stroke" />
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
          {peak === 0 ? `nothing in any of the ${buckets.bucket_count} buckets` : `highest ${peak} in one bucket of ${buckets.bucket_seconds} s`}
          {buckets.unplaced ? ` · ${buckets.unplaced} undated` : ''}
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
function endOf(buckets: Buckets, bucketIndex: number): string | null {
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
          ['First seen', <Value value={`${signature.first_seen} · ${ago(signature.first_seen)}`} />],
          ['Last seen', <Value value={`${signature.last_seen} · ${ago(signature.last_seen)}`} />],
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

/** One WHEA-Logger record in full, with the decoded structure of its payload beside it. */
function RecordDetail({ record, decoded, envelope }: { record: EventRecord; decoded?: Decoded; envelope: Reading | null }) {
  return (
    <>
      <Facts
        rows={[
          ['Provider', <Value value={record.ProviderName} />],
          ['Event', <Value value={record.TaskDisplayName ? `${record.Id} · ${record.TaskDisplayName}` : record.Id} />],
          ['Level', <Value value={record.LevelDisplayName} />],
          ['Record', <Value value={record.RecordId} />],
          ['Time', <Value value={`${record.TimeCreated} · ${ago(record.TimeCreated)}`} />],
        ]}
      />
      {record.Message ? <p className={styles.fullMessage}>{record.Message}</p> : null}
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
      <div className={styles.rowActions}>
        <MomentLink at={record.TimeCreated} />
        {envelope ? (
          <AddToStack item={{ kind: 'selection', envelope, ids: [record.RecordId], title: `WHEA-Logger record ${record.RecordId}` }} label="Add this record to the stack" />
        ) : null}
      </div>
    </>
  );
}

function levelKind(level: number): 'critical' | 'error' | 'warning' | 'info' {
  return level === 1 ? 'critical' : level === 2 ? 'error' : level === 3 ? 'warning' : 'info';
}
