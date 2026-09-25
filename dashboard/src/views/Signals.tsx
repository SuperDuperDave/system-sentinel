import { useLayoutEffect, useRef } from 'react';
import { AddToStack } from '../AddToStack';
import { CitedRecord, eventRefGroups, evidenceWithoutRefRows } from '../CitedRecord';
import { Reading, observed, section } from '../api';
import { OutcomeLine } from '../Outcome';
import { Head, MomentLink, RowList, Section, Tree } from '../Sections';
import { hasHeldReading, useReading } from '../useReading';
import { useApp } from '../store';
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
  warnings?: string[];
  warnings_total?: number;
}

type ClassContentInputs = Record<string, string[]>;

interface ClassReach {
  state: 'all' | 'partial' | 'none' | 'unrecorded';
  missed: Input[];
  warned: Input[];
}

function classReach(cls: string, inputs: Input[], byClass: ClassContentInputs | undefined): ClassReach {
  const names = byClass?.[cls];
  if (!Array.isArray(names) || !names.length || !names.every((name) => typeof name === 'string')) {
    return { state: 'unrecorded', missed: [], warned: [] };
  }
  const found = names.map((name) => inputs.find((input) => input.name === name));
  if (found.some((input) => !input)) return { state: 'unrecorded', missed: [], warned: [] };
  const selected = found as Input[];
  const missed = selected.filter((input) => input.outcome !== 'ok' && input.outcome !== 'empty');
  const warned = selected.filter((input) => (input.outcome === 'ok' || input.outcome === 'empty') && warningCount(input) > 0);
  return { state: missed.length === selected.length ? 'none' : missed.length ? 'partial' : 'all', missed, warned };
}

function quietLabel(reach: ClassReach): string {
  if (reach.state === 'unrecorded') return 'No lead returned · class input reach unrecorded';
  const missing = reach.missed.map((input) => `${input.name} ${input.outcome}`).join(', ');
  const warned = reach.warned.length ? `${reach.warned.map((input) => input.name).join(', ')} warned` : '';
  if (reach.state === 'none') return `Could not assess · ${missing}`;
  if (reach.state === 'partial') return `No lead from answered inputs · ${missing}${warned ? ` · ${warned}` : ''}`;
  return warned ? `No lead · ${warned}` : 'No lead returned';
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
  const signalId = useApp((s) => s.signalId);
  const setSignalId = useApp((s) => s.setSignalId);
  const returnTo = useRef(hasHeldReading('signals') ? signalId : null);
  const taken = useReading<Signal[]>('signals', {}, true, { hold: 'same-params' });
  const reading = taken.reading;
  const signals = section(reading, 'signals') ?? [];
  const head = observed(reading) ? reading?.sections.find((s) => s.name === 'signals') : undefined;
  const method = (reading?.method ?? {}) as { readings?: Input[]; class_content_inputs?: ClassContentInputs };
  const inputs = method.readings ?? [];
  const missingInputs = inputs.some((input) => input.outcome !== 'ok' && input.outcome !== 'empty');
  const warnedInputs = inputs.some((input) => warningCount(input) > 0);
  const emptyLimit = missingInputs && warnedInputs ? 'missing inputs and input warnings limit this reading' : missingInputs ? 'missing inputs limit this reading' : warnedInputs ? 'input warnings may limit this reading' : '';
  const groups = CLASSES.map((cls) => [cls, signals.filter((s) => s.class === cls)] as const);

  useLayoutEffect(() => {
    const saved = returnTo.current;
    if (!saved || !observed(reading)) return;
    returnTo.current = null;
    const divider = saved.indexOf(':');
    const group = document.getElementById(`signal-${saved.slice(0, divider)}`);
    const button = [...(group?.querySelectorAll<HTMLButtonElement>('button[data-row-id]') ?? [])]
      .find((row) => row.dataset.rowId === saved.slice(divider + 1));
    button?.focus({ preventScroll: true });
  }, [reading]);

  return (
    <section>
      <Head title="Signals">{taken.reading ? <AddToStack item={{ kind: 'reading', envelope: taken.reading }} label="Stack this reading" /> : null}</Head>
      <p className={styles.lede}>
        Patterns the tool noticed across several readings at once. Each one is a lead to follow, never a finding about what is wrong; the rule that
        produced it and the evidence under it are both here.
      </p>
      <OutcomeLine taken={taken} noun="signals" singular="signal" emptyText={`No signal found in the inputs that answered${emptyLimit ? `; ${emptyLimit}` : ''}`} />
      {taken.reading && !observed(taken.reading) ? (
        <p className={styles.unobserved}>No input could be observed, so no rule could run. Signals are read from other readings, not from the machine directly.</p>
      ) : null}
      {head ? <SignalOverview groups={groups} inputs={inputs} classContentInputs={method.class_content_inputs} /> : null}
      {inputs.length ? <Inputs inputs={inputs} /> : null}

      {head && reading ? (
        <div className={styles.section}>
          <Section title="What was noticed" cls={head.class} basis={head.basis}>
            {groups.map(([cls, found]) => (found.length ? <Group key={cls} cls={cls} signals={found} reading={reading} openId={signalId?.startsWith(`${cls}:`) ? signalId.slice(cls.length + 1) : null} onOpenChange={(id) => setSignalId(id === null ? null : `${cls}:${id}`)} /> : null))}
          </Section>
        </div>
      ) : null}
    </section>
  );
}

/** One class of signal: what the class looks for, then the leads that fired under it. */
function Group({ cls, signals, reading, openId, onOpenChange }: { cls: string; signals: Signal[]; reading: Reading; openId: string | null; onOpenChange: (id: string | null) => void }) {
  return (
    <div className={styles.group} id={`signal-${cls}`}>
      <h3 className={`${styles.groupTitle} label`}>{cls}</h3>
      <p className={styles.groupWhat}>{WHAT[cls]}</p>
      <RowList
        items={signals}
        idOf={(s) => s.id}
        openId={openId}
        onOpenChange={(id) => onOpenChange(id === null ? null : String(id))}
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
            <Tree value={evidenceWithoutRefRows(s.evidence)} />
            <Citations evidence={s.evidence} />
            <Jumps evidence={s.evidence} sourceKey={`signal:${s.id}`} />
            <p className={`${styles.from} readout`}>{s.id === 'gap:inputs' ? 'input status checked for' : 'read from'} {s.readings.join(', ')} · {s.id}</p>
            <div className={styles.stackLead}><AddToStack item={{ kind: 'selection', envelope: reading, ids: [s.id], title: s.title }} label="Stack this lead" /></div>
          </div>
        )}
      />
    </div>
  );
}

function Citations({ evidence }: { evidence: Record<string, unknown> }) {
  const groups = eventRefGroups(evidence);
  const count = groups.reduce((total, group) => total + group.refs.length, 0);
  const unusable = groups.reduce((total, group) => total + group.unusable, 0);
  if (!groups.length) return null;
  return <div className={styles.citations}>
    <p className="label">Exact rows cited by this lead · {count}</p>
    <p className={`${styles.citationNote} readout`}>Open one to ask the current log for the same record. This is a new observation; the lead above keeps the evidence from its original reading. The counts above and full evidence below name references that could not be made or were omitted by the cap.</p>
    {unusable ? <p className={`${styles.citationNote} readout`}>{unusable} returned {unusable === 1 ? 'reference is' : 'references are'} incomplete and cannot be checked here.</p> : null}
    {groups.map((group, index) => <div key={`${group.label ?? 'lead'}:${index}`}>
      {group.label ? <p className={`${styles.groupLabel} readout`}>{group.label}</p> : null}
      {!group.refs.length ? <p className={`${styles.citationNote} readout`}>No usable exact reference in this group. The returned evidence above names any missing references.</p> : null}
      <ol>{group.refs.map((ref, position) => <li key={`${ref.params.log}:${ref.params.record_id}:${ref.params.time_created}:${position}`}><CitedRecord citation={ref} /></li>)}</ol>
    </div>)}
    <details className={styles.fullEvidence}><summary className="readout">Full lead evidence · every field Signals returned (inferred)</summary><Tree value={evidence} /></details>
  </div>;
}

/** The five rule families in one scan, with exact counts and anchors to the evidence below. */
function SignalOverview({ groups, inputs, classContentInputs }: { groups: readonly (readonly [string, Signal[]])[]; inputs: Input[]; classContentInputs?: ClassContentInputs }) {
  const max = Math.max(1, ...groups.map(([, found]) => found.length));
  const total = groups.reduce((n, [, found]) => n + found.length, 0);
  return (
    <section className={styles.overview} aria-labelledby="signal-map-title">
      <div className={styles.overviewHead}>
        <div>
          <p className="label">Pattern map</p>
          <h2 id="signal-map-title" className="display">Where rules found leads</h2>
        </div>
        <p className={styles.overviewNote}>{total ? `${total} ${total === 1 ? 'lead' : 'leads'} to inspect.` : 'No rule matched the observed inputs.'} Counts show patterns, not health or severity.</p>
      </div>
      <div className={styles.classGrid}>
        {groups.map(([cls, found], index) => {
          const reach = classReach(cls, inputs, classContentInputs);
          const contents = (
            <>
              <span className={`${styles.classTop} readout`}>{String(index + 1).padStart(2, '0')} / {cls}</span>
              <strong className={styles.classCount}>{!found.length && reach.state === 'none' ? '—' : found.length}</strong>
              <span className={styles.classBar} aria-hidden="true">{found.length ? <span style={{ width: `${(found.length / max) * 100}%` }} /> : null}</span>
              <span className={`${styles.classAction} readout`}>{found.length ? `Inspect ${found.length === 1 ? 'lead' : 'leads'} ↗` : quietLabel(reach)}</span>
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
function Jumps({ evidence, sourceKey }: { evidence: Record<string, unknown>; sourceKey: string }) {
  const moments = momentsIn(evidence);
  if (!moments.length) return null;
  return (
    <div className={styles.jumps}>
      {moments.map((at) => (
        <MomentLink key={at} at={at} sourceKey={`${sourceKey}:${at}`} />
      ))}
    </div>
  );
}

/** The keys whose value frames the log: a window's last record or a stop's best returned anchor. */
const MOMENT_KEYS = ['last', 'started_at', 'anchor_at'];

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
const warningCount = (input: Input): number => input.warnings_total ?? input.warnings?.length ?? 0;

function Inputs({ inputs }: { inputs: Input[] }) {
  const answered = inputs.filter((i) => i.outcome === 'ok' || i.outcome === 'empty').length;
  const warned = inputs.filter((i) => warningCount(i) > 0);
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
              {warningCount(i) > 0 ? ` · ${warningCount(i)} ${warningCount(i) === 1 ? 'warning' : 'warnings'}` : ''}
            </span>
          </li>
        ))}
      </ul>
      {warned.length ? (
        <div className={styles.inputWarnings}>
          <p className="label">What the inputs warned about</p>
          {warned.map((i) => (
            <details key={i.name}>
              <summary className="readout">{i.name} · {warningCount(i)} {warningCount(i) === 1 ? 'warning' : 'warnings'}</summary>
              <ul className="readout">
                {(i.warnings ?? []).map((warning, index) => <li key={index}>{warning}</li>)}
                {warningCount(i) > (i.warnings?.length ?? 0) ? <li>More warnings are in the {i.name} reading.</li> : null}
              </ul>
            </details>
          ))}
          <p className={`${styles.warningNote} readout`}>These excerpts are bounded. Take an input reading for its full warning text and evidence.</p>
        </div>
      ) : null}
    </div>
  );
}

const CLASSES = ['suppressions', 'gaps', 'pressure', 'transitions', 'mismatches'] as const;

const WHAT: Record<string, string> = {
  suppressions: 'Settings that would keep a fault from showing itself.',
  gaps: 'Places the record has a hole: something that cannot report.',
  pressure: 'Which sources wrote the largest share of returned log records.',
  transitions: 'What the machine did between one power state and the next.',
  mismatches: 'Returned configuration or device state that stands out.',
};
