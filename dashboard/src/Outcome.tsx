import { useEffect, useRef, useState } from 'react';
import { Reading } from './api';
import { CopyButton } from './Copy';
import { Taken } from './useReading';
import styles from './Outcome.module.css';

const NOT_OBSERVED: Record<string, string> = {
  failed: 'Windows or PowerShell reported an error',
  unavailable: 'Sentinel could not get an answer from Windows',
  denied: 'Windows refused',
  timeout: 'the query did not finish in time',
};

export const clock = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

function seconds(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

function age(iso: string): string {
  const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 60_000));
  if (!Number.isFinite(minutes)) return 'time unknown';
  if (minutes < 1) return 'less than a minute ago';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

/**
 * The line under a view's title: what was asked, what came back, and whether the machine was observed.
 * A failure reads as a failure; an empty result reads as a finding; both name the method on request.
 */
export function OutcomeLine<T>({ taken, noun = 'records', singular, emptyText }: { taken: Taken<T>; noun?: string; singular?: string; emptyText?: string }) {
  const [showMethod, setShowMethod] = useState(false);
  const [, updateAge] = useState(0);
  useEffect(() => {
    if (!taken.held) return;
    const timer = window.setInterval(() => updateAge((value) => value + 1), 60_000);
    return () => window.clearInterval(timer);
  }, [taken.held]);
  const r = taken.reading;

  if (taken.state === 'lost' && !r) {
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
    const missed = r.outcome === 'unavailable' && r.error?.kind === 'local_store'
      ? 'Sentinel could not read local performance history'
      : NOT_OBSERVED[r.outcome] ?? r.outcome;
    body = (
      <>
        <Glyph kind="warn" /> Not observed: {missed}
        {r.error?.detail ? <span className={styles.detail}> · {firstLine(r.error.detail)}</span> : null}
      </>
    );
  }

  return (
    <div className={styles.block}>
      <p className={`${styles.line} readout`}>
        {body}
        {taken.state === 'taking' ? <span className={styles.taking}> · taking again…</span> : null}
        {taken.held ? <span className={styles.taking}> · held reading · {age(r.asked_at)}</span> : null}
        <span className={styles.sep} />
        <span className={styles.actions}>
          <button className={styles.action} onClick={() => { if (taken.state !== 'taking') taken.retake(); }} aria-disabled={taken.state === 'taking'}>Take again</button>
          <button className={styles.action} onClick={() => setShowMethod((v) => !v)} aria-expanded={showMethod}>{showMethod ? 'Hide method' : 'Method'}</button>
        </span>
      </p>
      {taken.held && taken.heldParams?.count !== undefined && taken.heldParams.count !== taken.requestedParams?.count ? (
        <p className={`${styles.line} readout`}>Showing last {taken.heldParams.count} while asking for last {taken.requestedParams?.count}. The visible rows belong to the earlier reading.</p>
      ) : null}
      {taken.held && taken.state === 'lost' ? <p className={`${styles.line} readout`} role="status"><Glyph kind="warn" /> Latest take could not reach Sentinel: {taken.problem}. The held reading remains visible.</p> : null}
      {taken.held && taken.latestFailure ? <p className={`${styles.line} readout`} role="status"><Glyph kind="warn" /> Latest take was not observed: {NOT_OBSERVED[taken.latestFailure.outcome] ?? taken.latestFailure.outcome}{taken.latestFailure.error?.detail ? ` · ${firstLine(taken.latestFailure.error.detail)}` : ''}. The held reading remains visible.</p> : null}
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
