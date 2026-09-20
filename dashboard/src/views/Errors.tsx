import { useState } from 'react';
import { EventRecord, Reading, observed } from '../api';
import { AddToStack } from '../AddToStack';
import { Glyph, OutcomeLine, clock, firstLine } from '../Outcome';
import { Facts, Head, RowList, Section, Segmented, Tree, Value, ago, basisOf, part } from '../Sections';
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

const WINDOWS = [
  { value: 24, label: '24 hours' },
  { value: 168, label: '7 days' },
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
  const [hours, setHours] = useState(24);
  const [count, setCount] = useState(30);
  const window = WINDOWS.find((w) => w.value === hours)?.label ?? `${hours} hours`;

  const storms = useReading('storms', { hours });
  const whea = useReading('whea', { count });

  const buckets = part<Buckets>(storms.reading, 'buckets');
  const status = part<Status>(storms.reading, 'status');
  const signatures = part<Signature[]>(storms.reading, 'signatures') ?? [];

  const records = part<EventRecord[]>(whea.reading, 'records') ?? [];
  const decoded = part<Decoded[]>(whea.reading, 'decoded') ?? [];

  return (
    <section>
      <Head title="Hardware errors">
        <Segmented value={hours} onChange={setHours} options={WINDOWS} label="How far back" />
      </Head>
      <OutcomeLine taken={storms} noun="WHEA-Logger records" emptyText={`No WHEA-Logger records in the last ${window}`} />

      {observed(storms.reading) && status ? (
        <Section
          title="Status"
          cls="inferred"
          basis={basisOf(storms.reading, 'status')}
          controls={storms.reading ? <AddToStack item={{ kind: 'reading', envelope: storms.reading, title: `Hardware error storms, last ${window}` }} /> : null}
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

      {observed(storms.reading) && buckets ? (
        <Section title="The window" cls="derived" basis={basisOf(storms.reading, 'buckets')}>
          <Trace buckets={buckets} />
        </Section>
      ) : null}

      {signatures.length ? (
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
        <OutcomeLine taken={whea} noun="WHEA-Logger records" emptyText="No WHEA-Logger records in the System log" />
        {observed(whea.reading) && records.length > 0 ? (
          <RowList
            items={records}
            layout={styles.recordRow}
            cells={(r) => (
              <>
                <span className={`${styles.time} readout`}>{clock.format(new Date(r.TimeCreated))}</span>
                <span className={styles.level} title={r.LevelDisplayName}>
                  <Glyph kind={levelKind(r.Level)} />
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
    return top;
  });
  const peak = peaks.reduce((a, b) => Math.max(a, b), 0);
  const floor = height - 3;
  const scale = peak > 0 ? (height - 6) / peak : 0;
  const points = peaks.map((v, i) => `${((i / Math.max(1, columns - 1)) * width).toFixed(1)},${(floor - v * scale).toFixed(1)}`).join(' ');
  const label = `${buckets.total} WHEA-Logger records over ${buckets.bucket_count} buckets of ${buckets.bucket_seconds} seconds; highest ${peak} in one bucket`;

  return (
    <figure className={styles.traceFigure}>
      <svg className={styles.trace} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={label}>
        {Array.from({ length: quarters - 1 }, (_, i) => {
          const x = ((i + 1) / quarters) * width;
          return <line key={i} className={styles.traceTick} x1={x} y1="0" x2={x} y2={height} vectorEffect="non-scaling-stroke" />;
        })}
        <polyline className={styles.tracePath} points={points} fill="none" vectorEffect="non-scaling-stroke" />
      </svg>
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
      {envelope ? (
        <div className={styles.rowActions}>
          <AddToStack item={{ kind: 'selection', envelope, ids: [record.RecordId], title: `WHEA-Logger record ${record.RecordId}` }} label="Add this record to the stack" />
        </div>
      ) : null}
    </>
  );
}

function levelKind(level: number): 'critical' | 'error' | 'warning' | 'info' {
  return level === 1 ? 'critical' : level === 2 ? 'error' : level === 3 ? 'warning' : 'info';
}
