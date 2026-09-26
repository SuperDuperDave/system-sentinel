import { useEffect, useState } from 'react';
import { AddEvidence } from '../AddEvidence';
import { observed } from '../api';
import { OutcomeLine } from '../Outcome';
import { Facts, Section, Tree, part, size } from '../Sections';
import { useReading } from '../useReading';
import styles from './ProcessPressure.module.css';

interface ProcessRow {
  pid: number;
  name: string | null;
  cpu_core_percent: number | null;
  private_working_set_bytes: number | null;
  private_bytes: number | null;
  io_bytes_per_sec: number | null;
  io_read_bytes_per_sec: number | null;
  io_write_bytes_per_sec: number | null;
  handles: number | null;
  threads: number | null;
}

interface Snapshot {
  at: string | null;
  logical_processors: number | null;
  total_processes: number;
  returned_processes: number;
  omitted_processes: number;
}

interface Leader { pid: number; name: string | null; value: number; logical_capacity_percent?: number | null }
interface Leaders { cpu: Leader[]; memory: Leader[]; io: Leader[] }
type Metric = 'cpu' | 'memory' | 'io';
type Sort = Metric | 'pid';

const LABELS: Record<Metric, { title: string; note: string }> = {
  cpu: { title: 'Processor time', note: '100% means one logical processor; a process can exceed it' },
  memory: { title: 'Private memory', note: 'Resident memory held privately by each process' },
  io: { title: 'Process I/O', note: 'All reported I/O, including activity beyond disks' },
};
const COUNTERS: Record<Metric, keyof ProcessRow> = { cpu: 'cpu_core_percent', memory: 'private_working_set_bytes', io: 'io_bytes_per_sec' };
const CLOCK = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });

/** A fresh and bounded view of process pressure; no process name enters the retained history. */
export function ProcessPressure() {
  const taken = useReading('processes');
  const snapshot = observed(taken.reading) ? part<Snapshot>(taken.reading, 'snapshot') : null;
  const rows = observed(taken.reading) ? part<ProcessRow[]>(taken.reading, 'processes') ?? [] : [];
  const leaders = observed(taken.reading) ? part<Leaders>(taken.reading, 'leaders') : null;
  const basis = taken.reading?.sections.find((section) => section.name === 'leaders')?.basis;
  const [open, setOpen] = useState(false);
  const [rawOpen, setRawOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<Sort>('cpu');
  const [selectedPid, setSelectedPid] = useState<number | null>(null);

  useEffect(() => {
    const timer = window.setInterval(taken.retake, 60_000);
    return () => window.clearInterval(timer);
  }, [taken.retake]);

  useEffect(() => {
    if (!open || selectedPid === null) return;
    const target = document.getElementById(`process-${selectedPid}`);
    target?.scrollIntoView({ block: 'center' });
    target?.focus({ preventScroll: true });
  }, [open, selectedPid]);

  function inspect(pid: number, metric: Sort) {
    setQuery('');
    setSort(metric);
    setOpen(true);
    setSelectedPid(pid);
  }

  const needle = query.trim().toLowerCase();
  const visible = rows.filter((row) => !needle || (row.name ?? '').toLowerCase().includes(needle) || String(row.pid).includes(needle));
  visible.sort((a, b) => sort === 'pid' ? a.pid - b.pid : ((b[COUNTERS[sort]] as number | null) ?? -1) - ((a[COUNTERS[sort]] as number | null) ?? -1) || a.pid - b.pid);

  return (
    <section className={styles.section} aria-label="Current process use">
      <div className={styles.head}>
        <div><p className="label">Current use</p><h2>Who is using the machine?</h2><p>Windows' current process counters. Names and IDs are shown here and never kept in history.</p></div>
        {taken.reading ? <AddEvidence item={{ kind: 'reading', envelope: taken.reading }} label="Stack this process snapshot" /> : null}
      </div>
      <OutcomeLine taken={taken} noun="process instances" singular="process instance" emptyText="Windows returned no process instances" />
      {snapshot?.at ? <p className={`${styles.asOf} readout`}>Counter snapshot {CLOCK.format(new Date(snapshot.at))} · {snapshot.returned_processes} of {snapshot.total_processes} process instances returned{snapshot.omitted_processes ? ` · ${snapshot.omitted_processes} omitted by the response limit` : ''}</p> : null}
      {leaders && rows.length ? (
        <Section title="Largest in this snapshot" cls="derived" basis={basis} note="Each bar is relative to the largest returned value in its own column; it is not a health grade.">
          <div className={styles.leaders}>
            {(['cpu', 'memory', 'io'] as Metric[]).map((metric) => <div className={styles.leaderGroup} key={metric}>
              <h3>{LABELS[metric].title}</h3>
              <p className="readout">{LABELS[metric].note}</p>
              <ol>{leaders[metric].map((leader) => {
                const max = leaders[metric][0]?.value ?? 0;
                return <li key={leader.pid}><button onClick={() => inspect(leader.pid, metric)} aria-label={`Inspect ${leader.name ?? 'unknown process'}, PID ${leader.pid}, ${LABELS[metric].title.toLowerCase()} ${display(metric, leader.value)}`}>
                  <span className={styles.leaderLine}><span className={styles.leaderName}>{leader.name ?? 'Unknown instance'} <span className="readout">#{leader.pid}</span></span><strong className="readout">{display(metric, leader.value)}</strong></span>
                  <span className={styles.track}><span style={{ width: `${max > 0 ? (leader.value / max) * 100 : 0}%` }} /></span>
                </button></li>;
              })}</ol>
            </div>)}
          </div>
        </Section>
      ) : null}
      {rows.length ? <Section title="Every returned process" cls="raw" note="Search or sort this snapshot; select a row for exact counters. PID identifies an instance only while that process lives.">
        <details className={styles.all} open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
          <summary>Inspect all {rows.length} returned process instances</summary>
          {open ? <>
            <div className={styles.controls}>
              <label>Find a name or PID <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
              <label>Sort by <select value={sort} onChange={(event) => setSort(event.target.value as Sort)}><option value="cpu">Processor time</option><option value="memory">Private memory</option><option value="io">Process I/O</option><option value="pid">PID</option></select></label>
            </div>
            <p className={`${styles.count} readout`}>{visible.length} matching {visible.length === 1 ? 'instance' : 'instances'}</p>
            <ol className={styles.rows}>{visible.map((row) => <li key={row.pid}>
              <button id={`process-${row.pid}`} className={styles.row} onClick={() => setSelectedPid(selectedPid === row.pid ? null : row.pid)} aria-expanded={selectedPid === row.pid}>
                <span className={styles.name}>{row.name ?? 'Unknown instance'} <small className="readout">#{row.pid}</small></span>
                <span className="readout">CPU {value(row.cpu_core_percent, '%')}</span>
                <span className="readout">Private {row.private_working_set_bytes == null ? 'unknown' : size(row.private_working_set_bytes)}</span>
                <span className="readout">I/O {row.io_bytes_per_sec == null ? 'unknown' : `${size(row.io_bytes_per_sec)}/s`}</span>
              </button>
              {selectedPid === row.pid ? <div className={styles.inspect}><Facts rows={[
                ['Snapshot', snapshot?.at ? CLOCK.format(new Date(snapshot.at)) : 'Unknown'],
                ['PID', row.pid],
                ['Instance', row.name ?? 'Unknown'],
                ['Processor time', value(row.cpu_core_percent, '% of one processor')],
                ['Logical capacity', row.cpu_core_percent != null && snapshot?.logical_processors ? `${(row.cpu_core_percent / snapshot.logical_processors).toFixed(2)}% estimated` : 'Unknown'],
                ['Private working set', bytes(row.private_working_set_bytes)],
                ['Private bytes', bytes(row.private_bytes)],
                ['Process I/O', rate(row.io_bytes_per_sec)],
                ['Read I/O', rate(row.io_read_bytes_per_sec)],
                ['Write I/O', rate(row.io_write_bytes_per_sec)],
                ['Handles', value(row.handles, '')],
                ['Threads', value(row.threads, '')],
              ]} /></div> : null}
            </li>)}</ol>
          </> : null}
        </details>
      </Section> : null}
      {rows.length ? <details className={styles.raw} onToggle={(event) => setRawOpen(event.currentTarget.open)}><summary>Raw process readout · exact returned fields</summary>{rawOpen ? <Tree value={rows} /> : null}</details> : null}
    </section>
  );
}

function display(metric: Metric, amount: number): string { return metric === 'cpu' ? `${amount}%` : metric === 'memory' ? size(amount) : `${size(amount)}/s`; }
function value(amount: number | null, unit: string): string { return amount == null ? 'Unknown' : `${amount}${unit}`; }
function bytes(amount: number | null): string { return amount == null ? 'Unknown' : `${size(amount)} (${amount.toLocaleString()} bytes)`; }
function rate(amount: number | null): string { return amount == null ? 'Unknown' : `${size(amount)}/s (${amount.toLocaleString()} bytes/s)`; }
