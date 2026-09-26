/**
 * The two visual channels a situation speaks in, kept orthogonal on purpose.
 *
 * Shape and word say what Sentinel knows about a reading: it observed something, it observed
 * nothing, it did not read, or the reading failed. Hue says only that there is a recent record to
 * look at. A failed reading never takes a hue, because a hole in the evidence is not a finding
 * about the machine; and a hue never appears without its glyph and its word.
 */
import { Cls, Reading } from './api';
import { Taken } from './useReading';
import styles from './Marks.module.css';

/**
 * Seven things a reading can let anyone say. Four of them are ways of not knowing, and they stay
 * apart: nobody asked yet, Sentinel could not reach an answer, the answer took too long, Windows
 * refused. Each has its own word; the shapes group them as not observed without merging them.
 */
export type Known = 'taking' | 'observed' | 'zero' | 'notasked' | 'unreached' | 'timeout' | 'denied' | 'failed';

const KNOWN_WORD: Record<Known, string> = {
  taking: 'Reading',
  observed: 'Observed',
  zero: 'None returned',
  notasked: 'Not asked yet',
  unreached: 'Not reached',
  timeout: 'Timed out',
  denied: 'Refused',
  failed: 'Failed',
};

export const knownWord = (known: Known): string => KNOWN_WORD[known];

/** Did the machine answer? Observed and zero did; every other state is a hole, never a zero. */
export const isHole = (known: Known): boolean => known !== 'observed' && known !== 'zero' && known !== 'taking';

/** What a reading's latest answer lets anyone say. */
export function knownOf(taken: Pick<Taken<unknown>, 'state' | 'reading'>): Known {
  const r = taken.reading;
  if (!r) return taken.state === 'lost' ? 'unreached' : taken.state === 'idle' ? 'notasked' : 'taking';
  return knownOfReading(r);
}

export function knownOfReading(r: Reading | null | undefined): Known {
  if (!r) return 'notasked';
  switch (r.outcome) {
    case 'ok': return 'observed';
    case 'empty': return 'zero';
    case 'failed': return 'failed';
    case 'denied': return 'denied';
    case 'timeout': return 'timeout';
    default: return 'unreached';
  }
}

/** Why a reading was not observed, in words, for a line that already says "Not read" or "Failed". */
export function whyNot(r: Reading | null | undefined, problem?: string | null): string {
  if (!r) return problem ? `the dashboard could not reach Sentinel: ${problem.split('\n')[0]}` : 'not asked yet';
  const detail = r.error?.detail ? `: ${r.error.detail.split('\n')[0]}` : '';
  switch (r.outcome) {
    case 'failed': return `Windows or PowerShell reported an error${detail}`;
    case 'denied': return `Windows refused${detail}`;
    case 'timeout': return 'the query did not finish in time';
    case 'unavailable': return `Sentinel could not get an answer from Windows${detail}`;
    default: return '';
  }
}

/** The outcome glyph: filled, ring, dashed ring, slashed ring. Never coloured by hue. */
export function OutcomeGlyph({ known }: { known: Known }) {
  return (
    <svg className={`${styles.glyph} ${isHole(known) ? styles.hole : styles[known]}`} viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      {known === 'observed' && <circle cx="6" cy="6" r="4" />}
      {known === 'zero' && <circle cx="6" cy="6" r="3.8" fill="none" strokeWidth="1.4" />}
      {known === 'notasked' && <circle cx="6" cy="6" r="3.8" fill="none" strokeWidth="1.2" strokeDasharray="0.8 2.2" strokeLinecap="round" />}
      {(known === 'unreached' || known === 'timeout') && <circle cx="6" cy="6" r="3.8" fill="none" strokeWidth="1.4" strokeDasharray="2.2 1.8" />}
      {known === 'denied' && <><circle cx="6" cy="6" r="3.8" fill="none" strokeWidth="1.4" /><path d="M3.6 6h4.8" strokeWidth="1.4" /></>}
      {known === 'failed' && <><circle cx="6" cy="6" r="3.8" fill="none" strokeWidth="1.4" /><path d="M3.2 8.8 8.8 3.2" strokeWidth="1.4" /></>}
      {known === 'taking' && <circle cx="6" cy="6" r="3.8" fill="none" strokeWidth="1.4" strokeDasharray="1 2.4" className={styles.turning} />}
    </svg>
  );
}

/** The glyph and its word together; the word is what a screen reader and a glance both need. */
export function OutcomeMark({ known, children }: { known: Known; children?: React.ReactNode }) {
  return (
    <span className={`${styles.outcome} ${isHole(known) ? styles.hole : styles[known]}`}>
      <OutcomeGlyph known={known} />
      <span className={styles.outcomeWord}>{KNOWN_WORD[known]}</span>
      {children ? <span className={styles.outcomeDetail}>{children}</span> : null}
    </span>
  );
}

export type Attention = 'stop' | 'report';

/** A stop is a triangle, reports are a diamond. Each hue is only ever drawn beside its word. */
export function AttentionGlyph({ kind }: { kind: Attention }) {
  return (
    <svg className={`${styles.glyph} ${styles[kind]}`} viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      {kind === 'stop' ? <path d="M6 1.4 10.8 10H1.2z" /> : <path d="M6 1.2 10.8 6 6 10.8 1.2 6z" />}
    </svg>
  );
}

export function AttentionMark({ kind, children }: { kind: Attention; children: React.ReactNode }) {
  return (
    <span className={`${styles.attention} ${styles[kind]}`}>
      <AttentionGlyph kind={kind} />
      <span>{children}</span>
    </span>
  );
}

/** Windows' own event levels, redundant in shape, hue and word. */
export function LevelGlyph({ level }: { level: number }) {
  const kind = level === 1 ? 'critical' : level === 2 ? 'error' : level === 3 ? 'warning' : 'info';
  return (
    <svg className={`${styles.glyph} ${styles[kind]}`} viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      {kind === 'critical' && <path d="M6 1.4 10.8 10H1.2z" />}
      {kind === 'error' && <path d="M6 1.2 10.8 6 6 10.8 1.2 6z" />}
      {kind === 'warning' && <path d="M6 2.2 10 9.4H2z" fill="none" strokeWidth="1.3" strokeLinejoin="round" />}
      {kind === 'info' && <circle cx="6" cy="6" r="2.6" fill="none" strokeWidth="1.3" />}
    </svg>
  );
}

const CLASS_WORD: Record<Cls, string> = {
  raw: 'as Windows returned it',
  derived: 'composed by Sentinel',
  invariant: 'stable across readings',
  inferred: 'a lead, not a finding',
};

/**
 * Where a piece of evidence came from, quietly: the reading an agent would call, and what kind of
 * evidence it is. The rule behind a composed section stays one disclosure down.
 */
export function Provenance({ reading, cls, basis }: { reading: string; cls?: Cls; basis?: string | null }) {
  return (
    <div className={styles.provenance}>
      <code className={styles.readingName}>{reading}</code>
      {cls ? <span className={styles.classWord}>{CLASS_WORD[cls]}</span> : null}
      {basis ? (
        <details className={styles.basis}>
          <summary>Rule</summary>
          <p>{basis}</p>
        </details>
      ) : null}
    </div>
  );
}
