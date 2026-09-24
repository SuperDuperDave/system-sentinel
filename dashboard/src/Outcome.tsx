import { useRef, useState } from 'react';
import { Reading } from './api';
import { CopyButton } from './Copy';
import { Taken } from './useReading';
import styles from './Outcome.module.css';

const NOT_OBSERVED: Record<string, string> = {
  failed: 'Windows or PowerShell reported an error',
  unavailable: 'the bridge to Windows was not there',
  denied: 'Windows refused',
  timeout: 'the query did not finish in time',
};

export const clock = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

function seconds(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

/**
 * The line under a view's title: what was asked, what came back, and whether the machine was observed.
 * A failure reads as a failure; an empty result reads as a finding; both name the method on request.
 */
export function OutcomeLine<T>({ taken, noun = 'records', singular, emptyText }: { taken: Taken<T>; noun?: string; singular?: string; emptyText?: string }) {
  const [showMethod, setShowMethod] = useState(false);
  const r = taken.reading;

  if (taken.state === 'lost') {
    return <p className={`${styles.line} readout`}><Glyph kind="warn" /> The dashboard could not get this reading: {taken.problem}. <button className={styles.action} onClick={taken.retake}>Try again</button></p>;
  }
  if (!r) {
    return <p className={`${styles.line} readout`}>{taken.state === 'taking' ? 'Taking the reading…' : ''}</p>;
  }

  const when = clock.format(new Date(r.asked_at));
  const cost = seconds(r.took_ms);
  let body: React.ReactNode;
  if (r.outcome === 'ok') {
    const countedNoun = r.count === 1 ? singular ?? noun : noun;
    body = <>{r.count == null ? noun : `${r.count} ${countedNoun}`} · taken {when} · {cost}</>;
  } else if (r.outcome === 'empty') {
    body = <>{emptyText ?? `No ${noun}`} · taken {when} · {cost}</>;
  } else {
    body = (
      <>
        <Glyph kind="warn" /> Not observed: {NOT_OBSERVED[r.outcome] ?? r.outcome}
        {r.error?.detail ? <span className={styles.detail}> · {firstLine(r.error.detail)}</span> : null}
      </>
    );
  }

  return (
    <div className={styles.block}>
      <p className={`${styles.line} readout`}>
        {body}
        {taken.state === 'taking' ? <span className={styles.taking}> · taking again…</span> : null}
        <span className={styles.sep} />
        <span className={styles.actions}>
          <button className={styles.action} onClick={taken.retake} disabled={taken.state === 'taking'}>Take again</button>
          <button className={styles.action} onClick={() => setShowMethod((v) => !v)} aria-expanded={showMethod}>{showMethod ? 'Hide method' : 'Method'}</button>
        </span>
      </p>
      {r.warnings.length ? (
        <ul className={styles.warnings} aria-label="What did not answer">
          {r.warnings.map((w, i) => (
            <li key={i} className={`${styles.warning} readout`}><Glyph kind="warn" /> {firstLine(w)}</li>
          ))}
        </ul>
      ) : null}
      {showMethod ? <Method reading={r} /> : null}
    </div>
  );
}

/** How the reading was taken: one query, several, or the readings it drew on. */
function Method({ reading }: { reading: Reading }) {
  const [showReading, setShowReading] = useState(false);
  const readingText = useRef<HTMLPreElement>(null);
  const m = reading.method;
  const queries = m.queries ?? (m.query ? [m.query] : []);
  const completeJson = showReading ? JSON.stringify(reading, null, 2) : null;
  return (
    <div className={styles.method}>
      <p className="label">How this was read · {m.kind}{reading.redacted.length ? ` · redacted: ${reading.redacted.join(', ')}` : ''}</p>
      {queries.map((q, i) => <CopyableQuery key={i} query={q} label={queries.length === 1 ? 'Copy query' : `Copy query ${i + 1}`} />)}
      {m.source ? <p className={`${styles.query} readout`}>{m.source}</p> : null}
      {m.readings ? <pre className={`${styles.query} readout`}>{JSON.stringify(m.readings, null, 1)}</pre> : null}
      <details className={styles.complete} onToggle={(event) => setShowReading(event.currentTarget.open)}>
        <summary className="readout">Complete returned reading · JSON</summary>
        {completeJson !== null ? (
          <>
            <p className={`${styles.completeNote} readout`}>The exact response this dashboard received. Default redaction applies.</p>
            <CopyButton text={completeJson} selectRef={readingText} label="Copy reading JSON" />
            <pre ref={readingText} className={`${styles.query} readout`}>{completeJson}</pre>
          </>
        ) : null}
      </details>
    </div>
  );
}

function CopyableQuery({ query, label }: { query: string; label: string }) {
  const held = useRef<HTMLPreElement>(null);
  return (
    <div className={styles.queryBlock}>
      <div className={styles.queryAction}><CopyButton text={query} selectRef={held} label={label} /></div>
      <pre ref={held} className={`${styles.query} readout`}>{query}</pre>
    </div>
  );
}

export function Glyph({ kind }: { kind: 'critical' | 'error' | 'warning' | 'info' | 'warn' }) {
  const k = kind === 'warn' ? 'warning' : kind;
  return (
    <svg className={styles.glyph} viewBox="0 0 10 10" aria-hidden="true">
      {k === 'critical' && <path d="M5 1.2 9.2 8.8H.8z" />}
      {k === 'error' && <circle cx="5" cy="5" r="3.6" />}
      {k === 'warning' && <circle cx="5" cy="5" r="3.2" fill="none" strokeWidth="1.3" />}
      {k === 'info' && <circle cx="5" cy="5" r="1.6" />}
    </svg>
  );
}

export function firstLine(s: string): string {
  return s.split('\n')[0];
}
