import { ReactNode, useSyncExternalStore } from 'react';
import { PRESETS, fixedRange, fmt, presetRange, rangeTitle } from '../time';
import { useApp } from '../store';
import styles from './Page.module.css';

/**
 * The frame every view on the time axis shares: its question, the range, the reading, and the
 * inspector beside it. The range is one control in one place, because it is one state.
 */
export function AxisPage({ title, question, summary, chart, list, inspector }: {
  title: string;
  question: string;
  summary: ReactNode;
  /** The tracks; left out on a phone, where the list carries the same marks. */
  chart: ReactNode;
  list: ReactNode;
  inspector: ReactNode;
}) {
  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <div className={styles.titles}>
          <h1 className={styles.title}>{title}</h1>
          <p className={styles.question}>{question}</p>
        </div>
        <RangeBar />
      </header>
      <div className={styles.summary}>{summary}</div>
      {chart ? <div className={styles.chart}>{chart}</div> : null}
      <div className={styles.side}>{inspector}</div>
      <div className={styles.list}>{list}</div>
    </div>
  );
}

function RangeBar() {
  const range = useApp((s) => s.range);
  const setRange = useApp((s) => s.setRange);
  const width = range.to - range.from;
  const toLater = () => {
    const later = Math.min(range.to + width / 2, Date.now()) - range.to;
    if (later > 0) setRange(fixedRange(range.from + later, range.to + later));
  };
  return (
    <div className={styles.rangeBar} role="group" aria-label="Time range">
      <div className={styles.presets} role="group" aria-label="Show the last">
        {PRESETS.map((p) => (
          <button key={p.id} className={styles.preset} aria-pressed={range.preset === p.id} onClick={() => setRange(presetRange(p.id))} aria-label={p.long}>
            {p.label}
          </button>
        ))}
      </div>
      <div className={styles.steps}>
        <button className={styles.step} onClick={() => setRange(fixedRange(range.from - width / 2, range.to - width / 2))} aria-label="Earlier by half the range">
          <span aria-hidden="true">‹</span> Earlier
        </button>
        <button className={styles.step} disabled={range.preset !== null} onClick={toLater} aria-label="Later by half the range">
          Later <span aria-hidden="true">›</span>
        </button>
        {range.preset ? null : <button className={styles.step} onClick={() => setRange(presetRange('7d'))}>Back to now</button>}
      </div>
      <p className={styles.rangeWords} aria-live="polite">
        {range.preset ? <>Showing {rangeTitle(range)}, to <span className="readout">{fmt.when(range.to)}</span></> : <>Showing <span className="readout">{fmt.when(range.from)}</span> to <span className="readout">{fmt.when(range.to)}</span></>}
      </p>
    </div>
  );
}

const PHONE = '(max-width: 719px)';

/** Whether the phone layout is showing: the day list alone, with the inspector as a sheet. */
export function usePhone(): boolean {
  return useSyncExternalStore(
    (change) => {
      const query = window.matchMedia(PHONE);
      query.addEventListener('change', change);
      return () => query.removeEventListener('change', change);
    },
    () => window.matchMedia(PHONE).matches,
  );
}
