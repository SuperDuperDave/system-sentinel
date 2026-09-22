import { useEffect, useState } from 'react';
import { AddToStack } from '../AddToStack';
import { PerformanceCollection, Unauthorized, clearPerformanceHistory, observed, performanceCollection, setPerformanceCollection } from '../api';
import { OutcomeLine } from '../Outcome';
import { Facts, Head, MomentLink, Section, Segmented, Tree, part, size } from '../Sections';
import { useApp } from '../store';
import { useReading } from '../useReading';
import { ProcessPressure } from './ProcessPressure';
import styles from './Performance.module.css';

interface Sample {
  at: string;
  cadence_seconds: number | null;
  cpu_percent: number | null;
  memory_available_mb: number | null;
  committed_bytes: number | null;
  commit_limit_bytes: number | null;
  pages_per_sec: number | null;
  disk_queue: number | null;
  disk_read_bytes_per_sec: number | null;
  disk_write_bytes_per_sec: number | null;
}

interface Shape {
  window_start: string;
  window_end: string;
  first: string | null;
  last: string | null;
  largest_gap_seconds: number | null;
  metrics: Record<string, { count: number; min: number | null; median: number | null; max: number | null }>;
}

const HOURS = [1, 6, 24, 48];
const INTERVALS = [60, 120, 300, 600];
const STAMP = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const EXACT_STAMP = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });

/** A local numeric witness, continuous while enabled and preserved across a stop. */
export function Performance() {
  const moment = useApp((s) => s.moment);
  const setSession = useApp((s) => s.setSession);
  const { hours, endChoice, selectedAt } = useApp((s) => s.performanceView);
  const setPerformanceView = useApp((s) => s.setPerformanceView);
  const [collection, setCollection] = useState<PerformanceCollection | null>(null);
  const [controlProblem, setControlProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearNote, setClearNote] = useState('');
  const [rawOpen, setRawOpen] = useState(false);
  const [takeNow, setTakeNow] = useState(false);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const history = useReading('performance_history', { hours, end: endChoice === 'held' && moment ? moment : '' });
  const live = useReading<Sample>('load', {}, takeNow);
  const samples = observed(history.reading) ? part<Sample[]>(history.reading, 'samples') ?? [] : [];
  const shape = observed(history.reading) ? part<Shape>(history.reading, 'shape') : null;
  const matchingPosition = selectedAt === null ? samples.length - 1 : samples.findIndex((sample) => sample.at === selectedAt);
  const selectionMissing = selectedAt !== null && matchingPosition < 0 && samples.length > 0;
  const selectedPosition = samples.length ? selectionMissing ? samples.length - 1 : matchingPosition : 0;
  const selected = samples.length ? samples[selectedPosition] : null;
  const heldAt = moment && Number.isFinite(Date.parse(moment)) ? EXACT_STAMP.format(new Date(moment)) : null;

  useEffect(() => {
    let active = true;
    const refresh = () => performanceCollection().then((value) => { if (active) { setCollection(value); setCheckedAt(Date.now()); setControlProblem(null); } }).catch((err: unknown) => {
      if (!active) return;
      if (err instanceof Unauthorized) setSession('closed');
      else setControlProblem(err instanceof Error ? err.message : String(err));
    });
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [setSession]);

  useEffect(() => {
    if (endChoice !== 'now') return;
    const timer = window.setInterval(history.retake, 60_000);
    return () => window.clearInterval(timer);
  }, [endChoice, history.retake]);

  async function configure(enabled: boolean, interval: number) {
    setBusy(true);
    setControlProblem(null);
    try {
      setCollection(await setPerformanceCollection(enabled, interval));
      history.retake();
    } catch (err) {
      if (err instanceof Unauthorized) setSession('closed');
      else setControlProblem(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  }

  async function clear() {
    setBusy(true);
    setControlProblem(null);
    try {
      const result = await clearPerformanceHistory();
      setConfirmClear(false);
      setClearNote(`${result.cleared_files} ${result.cleared_files === 1 ? 'day file' : 'day files'} cleared. Collection ${collection?.settings.enabled ? 'continues' : 'stays paused'}.`);
      setPerformanceView({ selectedAt: null });
      history.retake();
      setCollection(await performanceCollection());
    } catch (err) {
      if (err instanceof Unauthorized) setSession('closed');
      else setControlProblem(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  }

  const settings = collection?.settings;
  const attempt = collection?.last_attempt;
  const gapCount = samples.reduce((count, row, index) => count + (index > 0 && isGap(samples[index - 1], row) ? 1 : 0), 0);

  return (
    <section>
      <Head title="Performance">{history.reading ? <AddToStack item={{ kind: 'reading', envelope: history.reading }} label="Stack this history" /> : null}</Head>
      <p className={styles.lede}>Fresh process use and numeric history collected locally every minute by default. A gap in the history means no sample was kept.</p>

      <ProcessPressure />

      <section className={styles.collection} aria-label="Local collection">
        <div className={styles.collectionTop}>
          <div>
            <p className="label">Local collection</p>
            <p className={styles.collectionState}>{settings ? settings.config_error ? 'Settings could not be read · collection stopped' : settings.enabled ? `Collection on · every ${settings.interval_seconds / 60} ${settings.interval_seconds === 60 ? 'minute' : 'minutes'}` : 'Collection paused' : 'Checking collection…'}</p>
            <p className={`${styles.collectionDetail} readout`}>Numeric samples only · retained up to {collection?.retention_days ?? 30} days on this computer · no background upload</p>
          </div>
          <div className={styles.collectionActions}>
            {settings ? <button onClick={() => configure(!settings.enabled, settings.interval_seconds)} disabled={busy}>{settings.enabled ? 'Pause collection' : 'Resume collection'}</button> : null}
            {settings ? <label className="readout">Interval <select value={settings.interval_seconds} disabled={busy} onChange={(event) => configure(settings.enabled, Number(event.target.value))}>{!INTERVALS.includes(settings.interval_seconds) ? <option value={settings.interval_seconds}>{settings.interval_seconds / 60} min</option> : null}{INTERVALS.map((seconds) => <option key={seconds} value={seconds}>{seconds / 60} min</option>)}</select></label> : null}
          </div>
        </div>
        <p className={`${styles.lastAttempt} readout`}>Last attempt: {attempt?.at ? `${STAMP.format(new Date(attempt.at))} · ${attempt.outcome}${attempt.took_ms != null ? ` · ${attempt.took_ms} ms` : ''}${settings?.enabled && checkedAt !== null && checkedAt - Date.parse(attempt.at) > 2.5 * settings.interval_seconds * 1000 ? ' · overdue; no recent sample confirmed' : ''}` : 'none yet'}{settings?.config_error ? ' · save a valid setting to resume' : ''}</p>
        {controlProblem ? <p className={styles.controlProblem} role="alert">Could not change collection: {controlProblem}</p> : null}
      </section>

      <h2 className={styles.historyTitle}>Stored history</h2>
      <div className={styles.windowControls}>
        <Segmented value={hours} onChange={(value) => setPerformanceView({ hours: value, selectedAt: null })} options={HOURS.map((value) => ({ value, label: `${value} h` }))} label="History window" />
        {heldAt ? <div className={styles.heldControl}>
          <Segmented value={endChoice} onChange={(value) => setPerformanceView({ endChoice: value, selectedAt: null })} options={[{ value: 'now', label: 'Until now' }, { value: 'held', label: 'Before held moment' }]} label="Window end" />
          <p className="readout">Held moment · {heldAt} local</p>
        </div> : null}
      </div>
      <OutcomeLine taken={history} noun="stored samples" emptyText="No stored samples in this window" />
      {samples.length && shape ? (
        <>
          <div className={styles.rangeLine}>
            <span className="readout">{STAMP.format(new Date(shape.window_start))} → {STAMP.format(new Date(shape.window_end))}</span>
            <span className="readout">{gapCount} {gapCount === 1 ? 'gap' : 'gaps'} between returned samples{gapCount ? ` · longest interval ${timeSpan(shape.largest_gap_seconds)}` : ''}</span>
          </div>
          <div className={styles.charts}>
            <Trace label="Processor time" note="Average across processors · 0–100%" samples={samples} shape={shape} selected={selected} value={(row) => row.cpu_percent} max={100} display={(value) => value == null ? 'Not reported' : `${value}%`} />
            <Trace label="Committed memory" note="Committed bytes / reported limit · 0–100%" samples={samples} shape={shape} selected={selected} value={commitPercent} max={100} display={(value) => value == null ? 'Not reported' : `${value.toFixed(1)}%`} />
            <Trace label="Disk read + write" note="Aggregate bytes each second · scaled to this window" samples={samples} shape={shape} selected={selected} value={diskThroughput} max={Math.max(1, ...samples.map((row) => diskThroughput(row) ?? 0))} display={(value) => value == null ? 'Not reported' : `${size(value)}/s`} />
          </div>
          <div className={styles.scrub}>
            <label className="readout" htmlFor="performance-sample">Inspect sample · {selected ? STAMP.format(new Date(selected.at)) : ''}</label>
            <input id="performance-sample" type="range" min={0} max={samples.length - 1} value={selectedPosition} onChange={(event) => setPerformanceView({ selectedAt: samples[Number(event.target.value)]?.at ?? null })} aria-valuetext={selected ? `Sample ${selectedPosition + 1} of ${samples.length}, ${EXACT_STAMP.format(new Date(selected.at))}` : undefined} />
            <button onClick={() => setPerformanceView({ selectedAt: null })} disabled={selectedAt === null}>Latest</button>
          </div>
          {selectionMissing ? <p className={`${styles.selectionMissing} readout`} role="status">The selected sample is no longer in this returned window. Showing the latest returned sample.</p> : null}
          {selected ? <Section title="Selected sample" cls="raw" note="readable units · full precision in raw series"><Facts rows={[
            ['Taken', EXACT_STAMP.format(new Date(selected.at))],
            ['Processor time', metric(selected.cpu_percent, '%')],
            ['Memory available', metric(selected.memory_available_mb, ' MB')],
            ['Committed / limit', selected.committed_bytes != null && selected.commit_limit_bytes != null ? `${size(selected.committed_bytes)} / ${size(selected.commit_limit_bytes)}` : 'Not reported'],
            ['Pages per second', metric(selected.pages_per_sec, '')],
            ['Disk queue', metric(selected.disk_queue, '')],
            ['Disk read', selected.disk_read_bytes_per_sec == null ? 'Not reported' : `${size(selected.disk_read_bytes_per_sec)}/s`],
            ['Disk write', selected.disk_write_bytes_per_sec == null ? 'Not reported' : `${size(selected.disk_write_bytes_per_sec)}/s`],
            ['Planned interval', selected.cadence_seconds == null ? 'Not reported' : `${selected.cadence_seconds} s`],
          ]} />
            <div className={styles.sampleRecord}>
              <p>See what the System log returned before this sample. Those records do not establish what drove the counters.</p>
              <MomentLink at={selected.at} label="System record before this sample" />
            </div>
            <details className={styles.raw}><summary>Raw selected sample</summary><pre className="readout">{JSON.stringify(selected, null, 2)}</pre></details>
          </Section> : null}
          <details className={styles.raw} onToggle={(event) => setRawOpen(event.currentTarget.open)}><summary>Raw sample series · {samples.length} rows</summary>{rawOpen ? <pre className="readout">{JSON.stringify(samples, null, 2)}</pre> : null}</details>
          <details className={styles.raw}><summary>How the window was summarized</summary><Tree value={shape} /></details>
        </>
      ) : history.reading?.outcome === 'empty' ? <p className={styles.noSamples}>No sampled numbers fall in this window. The machine may have been off, asleep, unable to answer, or collection may have been paused; the empty interval alone cannot tell which.</p> : null}

      <div className={styles.clearArea}>
        {confirmClear ? <p>Delete all stored numeric samples on this computer? <button onClick={clear} disabled={busy}>Delete stored history</button><button onClick={() => setConfirmClear(false)} disabled={busy}>Keep it</button></p> : <button onClick={() => setConfirmClear(true)} disabled={busy}>Clear stored history…</button>}
        {clearNote ? <p className="readout" role="status">{clearNote}</p> : null}
      </div>

      <section className={styles.now}>
        <div><h2>Fresh snapshot</h2><p>Ask Windows for aggregate counters right now, without waiting for the next stored sample.</p></div>
        {!takeNow ? <button onClick={() => setTakeNow(true)}>Take a fresh snapshot</button> : null}
        {takeNow ? <><OutcomeLine taken={live} noun="snapshots" emptyText="No numeric counter answered" />{observed(live.reading) && part<Sample>(live.reading, 'snapshot') ? <Tree value={part<Sample>(live.reading, 'snapshot')} /> : null}{live.reading ? <AddToStack item={{ kind: 'reading', envelope: live.reading }} label="Stack this snapshot" /> : null}</> : null}
      </section>
    </section>
  );
}

function metric(value: number | null, unit: string): string { return value == null ? 'Not reported' : `${value}${unit}`; }
function timeSpan(value: number | null): string { return value == null ? 'unknown' : value >= 3600 ? `${(value / 3600).toFixed(1)} h` : `${Math.round(value / 60)} min`; }
function commitPercent(row: Sample): number | null { return row.committed_bytes != null && row.commit_limit_bytes != null && row.commit_limit_bytes > 0 ? (row.committed_bytes / row.commit_limit_bytes) * 100 : null; }
function diskThroughput(row: Sample): number | null { return row.disk_read_bytes_per_sec != null && row.disk_write_bytes_per_sec != null ? row.disk_read_bytes_per_sec + row.disk_write_bytes_per_sec : null; }
function isGap(before: Sample, after: Sample): boolean { return (Date.parse(after.at) - Date.parse(before.at)) / 1000 > 2 * Math.max(before.cadence_seconds ?? 60, after.cadence_seconds ?? 60); }

function Trace({ label, note, samples, shape, selected, value, max, display }: { label: string; note: string; samples: Sample[]; shape: Shape; selected: Sample | null; value: (row: Sample) => number | null; max: number; display: (value: number | null) => string }) {
  const start = Date.parse(shape.window_start);
  const span = Date.parse(shape.window_end) - start;
  const x = (at: string) => Math.max(0, Math.min(600, ((Date.parse(at) - start) / span) * 600));
  const y = (amount: number) => 78 - Math.max(0, Math.min(1, amount / max)) * 70;
  let path = '';
  let previous: Sample | null = null;
  for (const row of samples) {
    const amount = value(row);
    if (amount == null || !Number.isFinite(amount)) { previous = null; continue; }
    path += `${previous && !isGap(previous, row) ? ' L' : ' M'}${x(row.at).toFixed(2)},${y(amount).toFixed(2)}`;
    previous = row;
  }
  const selectedValue = selected ? value(selected) : null;
  return (
    <div className={styles.trace}>
      <div className={styles.traceHead}><div><h2>{label}</h2><p className="readout">{note}</p></div><strong className="readout">{display(selectedValue)}</strong></div>
      <svg viewBox="0 0 600 86" preserveAspectRatio="none" role="img" aria-label={`${label} across ${samples.length} returned samples. Missing readings and gaps are not connected. Selected value: ${display(selectedValue)}.`}>
        <path className={styles.gridLine} d="M0 8H600M0 43H600M0 78H600" />
        <path className={styles.dataLine} d={path} />
        {selected ? <line className={styles.cursor} x1={x(selected.at)} x2={x(selected.at)} y1="0" y2="86" /> : null}
        {selected && selectedValue != null ? <circle className={styles.point} cx={x(selected.at)} cy={y(selectedValue)} r="3.5" /> : null}
      </svg>
    </div>
  );
}
