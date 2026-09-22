import { AddToStack } from '../AddToStack';
import { observed, section } from '../api';
import { OutcomeLine } from '../Outcome';
import { Head, MomentLink, RowList, Section, Tree } from '../Sections';
import { useReading } from '../useReading';
import styles from './Signals.module.css';

interface Signal {
  id: string;
  class: string;
  title: string;
  summary: string;
  evidence: Record<string, unknown>;
  readings: string[];
}

interface Input {
  name: string;
  params: Record<string, unknown>;
  outcome: string;
  took_ms: number;
}

/**
 * Signals: what the tool noticed across several readings at once.
 *
 * Every row here is a lead. None of them is a diagnosis, and none is styled as one — no level
 * glyph, no alarm, no ranking by severity — because the evidence for each is one tap away and
 * the reading belongs to whoever holds it. What makes a signal trustworthy is that it names the
 * readings it came from and what those readings returned, so a lead built on an input that was
 * never observed cannot pass for one built on the machine.
 */
export function Signals() {
  const taken = useReading<Signal[]>('signals');
  const signals = section(taken.reading, 'signals') ?? [];
  const head = observed(taken.reading) ? taken.reading?.sections.find((s) => s.name === 'signals') : undefined;
  const inputs = ((taken.reading?.method ?? {}) as { readings?: Input[] }).readings ?? [];
  const missingInputs = inputs.some((input) => input.outcome !== 'ok' && input.outcome !== 'empty');
  const groups = CLASSES.map((cls) => [cls, signals.filter((s) => s.class === cls)] as const);
  const silent = groups.filter(([, found]) => found.length === 0).map(([cls]) => cls);

  return (
    <section>
      <Head title="Signals">{taken.reading ? <AddToStack item={{ kind: 'reading', envelope: taken.reading }} label="Stack this reading" /> : null}</Head>
      <p className={styles.lede}>
        Patterns the tool noticed across several readings at once. Each one is a lead to follow, never a finding about what is wrong; the rule that
        produced it and the evidence under it are both here.
      </p>
      <OutcomeLine taken={taken} noun="signals" singular="signal" emptyText={missingInputs ? 'No signal found in the inputs that answered; missing inputs limit this reading' : 'No signal found in the inputs that answered'} />
      {taken.reading && !observed(taken.reading) ? (
        <p className={styles.unobserved}>No input could be observed, so no rule could run. Signals are read from other readings, not from the machine directly.</p>
      ) : null}
      {head ? <SignalOverview groups={groups} /> : null}
      {inputs.length ? <Inputs inputs={inputs} /> : null}

      {head ? (
        <div className={styles.section}>
          <Section title="What was noticed" cls={head.class} basis={head.basis}>
            {groups.map(([cls, found]) => (found.length ? <Group key={cls} cls={cls} signals={found} /> : null))}
            {signals.length && silent.length ? <p className={`${styles.silent} readout`}>No signal in {silent.join(', ')}.</p> : null}
          </Section>
        </div>
      ) : null}
    </section>
  );
}

/** One class of signal: what the class looks for, then the leads that fired under it. */
function Group({ cls, signals }: { cls: string; signals: Signal[] }) {
  return (
    <div className={styles.group} id={`signal-${cls}`}>
      <h3 className={`${styles.groupTitle} label`}>{cls}</h3>
      <p className={styles.groupWhat}>{WHAT[cls]}</p>
      <RowList
        items={signals}
        idOf={(s) => s.id}
        layout={styles.signalRow}
        cells={(s) => (
          <>
            <span className={styles.signalTitle}>{s.title}</span>
            <span className={styles.summary}>{s.summary}</span>
          </>
        )}
        inspect={(s) => (
          <div className={styles.evidence}>
            <p className="label">Evidence</p>
            <Tree value={s.evidence} />
            <Jumps evidence={s.evidence} />
            <p className={`${styles.from} readout`}>read from {s.readings.join(', ')} · {s.id}</p>
          </div>
        )}
      />
    </div>
  );
}

/** The five rule families in one scan, with exact counts and anchors to the evidence below. */
function SignalOverview({ groups }: { groups: readonly (readonly [string, Signal[]])[] }) {
  const max = Math.max(1, ...groups.map(([, found]) => found.length));
  const total = groups.reduce((n, [, found]) => n + found.length, 0);
  return (
    <section className={styles.overview} aria-labelledby="signal-map-title">
      <div className={styles.overviewHead}>
        <div>
          <p className="label">Pattern map</p>
          <h2 id="signal-map-title" className="display">Where rules found leads</h2>
        </div>
        <p className={styles.overviewNote}>{total ? `${total} leads to inspect.` : 'No rule matched the observed inputs.'} Counts show patterns, not health or severity.</p>
      </div>
      <div className={styles.classGrid}>
        {groups.map(([cls, found], index) => {
          const contents = (
            <>
              <span className={`${styles.classTop} readout`}>{String(index + 1).padStart(2, '0')} / {cls}</span>
              <strong className={styles.classCount}>{found.length}</strong>
              <span className={styles.classBar} aria-hidden="true">{found.length ? <span style={{ width: `${(found.length / max) * 100}%` }} /> : null}</span>
              <span className={`${styles.classAction} readout`}>{found.length ? `Inspect ${found.length === 1 ? 'lead' : 'leads'} ↗` : 'No lead returned'}</span>
            </>
          );
          return found.length ? <a href={`#signal-${cls}`} className={styles.classCard} key={cls}>{contents}</a> : <div className={styles.classCard} key={cls}>{contents}</div>;
        })}
      </div>
    </section>
  );
}

/**
 * The moments a lead points at: the end of the window it counted, and the stops it named. A lead
 * is followed by reading the log where it happened, so the evidence carries the way there —
 * still a jump, still labelled, and still the only thing in the panel that moves anyone.
 */
function Jumps({ evidence }: { evidence: Record<string, unknown> }) {
  const moments = momentsIn(evidence);
  if (!moments.length) return null;
  return (
    <div className={styles.jumps}>
      {moments.map((at) => (
        <MomentLink key={at} at={at} />
      ))}
    </div>
  );
}

/** The keys whose value is a moment in the log: a window's last record, and a stop's start. */
const MOMENT_KEYS = ['last', 'started_at'];

function momentsIn(evidence: Record<string, unknown>): string[] {
  const found: string[] = [];
  const walk = (value: unknown, depth: number) => {
    if (depth > 4 || value === null || typeof value !== 'object') return;
    if (Array.isArray(value)) {
      value.forEach((entry) => walk(entry, depth + 1));
      return;
    }
    for (const [key, held] of Object.entries(value as Record<string, unknown>)) {
      if (MOMENT_KEYS.includes(key) && typeof held === 'string' && !Number.isNaN(Date.parse(held))) {
        if (!found.includes(held)) found.push(held);
      } else {
        walk(held, depth + 1);
      }
    }
  };
  walk(evidence, 0);
  return found;
}

/** What each reading returned when the rules were run over it: a lead is only as observed as its inputs. */
function Inputs({ inputs }: { inputs: Input[] }) {
  const answered = inputs.filter((i) => i.outcome === 'ok' || i.outcome === 'empty').length;
  return (
    <div className={styles.inputs}>
      <p className="label">Inputs · {answered} of {inputs.length} readings answered{answered < inputs.length ? ' · missing inputs limit these rules' : ''}</p>
      <ul className={styles.inputList}>
        {inputs.map((i) => (
          <li key={i.name} className={`${styles.input} readout`}>
            <span className={i.outcome === 'ok' || i.outcome === 'empty' ? styles.inputMarkOk : styles.inputMarkLost} aria-hidden="true" />
            <span className={styles.inputName}>{i.name}</span>
            <span className={i.outcome === 'ok' || i.outcome === 'empty' ? styles.inputOk : styles.inputLost}>
              {i.outcome === 'ok' ? 'answered' : i.outcome === 'empty' ? 'answered · empty' : `not observed · ${i.outcome}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const CLASSES = ['suppressions', 'gaps', 'pressure', 'transitions', 'mismatches'] as const;

const WHAT: Record<string, string> = {
  suppressions: 'Settings that would keep a fault from showing itself.',
  gaps: 'Places the record has a hole: something that cannot report.',
  pressure: 'What is filling the recent log.',
  transitions: 'What the machine did between one power state and the next.',
  mismatches: 'Where two readings of the same thing do not agree.',
};
