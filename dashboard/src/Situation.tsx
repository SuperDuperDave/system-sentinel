/**
 * What every situation is made of: the person's question as its title, the door's own reading
 * under it, numbered steps in a fixed order, and the same steps handed to an agent.
 *
 * Disclosure runs in the identity note's order: the question, then what was found, then the exact
 * evidence, then the method. The time a reading was taken and the way to take it again are real
 * controls, so they are here, but after the finding rather than before it.
 */
import { ReactNode, useRef, useState } from 'react';
import { Cls, Reading } from './api';
import { CopyButton } from './Copy';
import { Known, OutcomeMark, Provenance, isHole } from './Marks';
import { RecipeStep, recipeText } from './situations';
import { Taken } from './useReading';
import styles from './Situation.module.css';

const CLOCK = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

export function SituationHead({ question, lede, children }: { question: string; lede: ReactNode; children?: ReactNode }) {
  return (
    <header className={styles.head}>
      <p className={styles.eyebrow}>You came with</p>
      <h1 className={`${styles.title} display`}>{question}</h1>
      <p className={styles.lede}>{lede}</p>
      {children}
    </header>
  );
}

/** When the reading was taken, what it cost, and the two instrument controls, after the finding. */
export function ReadingFooter({ taken, label = 'Take again' }: { taken: Taken<unknown>; label?: string }) {
  const [method, setMethod] = useState(false);
  const r = taken.reading;
  return (
    <div className={styles.footer}>
      {r ? <span className={styles.footerFacts}>taken {CLOCK.format(Date.parse(r.asked_at))} · {r.took_ms < 1000 ? `${r.took_ms} ms` : `${(r.took_ms / 1000).toFixed(1)} s`}{taken.held ? ' · held from earlier' : ''}</span> : null}
      <button className="button quiet" onClick={() => { if (taken.state !== 'taking') taken.retake(); }} aria-disabled={taken.state === 'taking'}>
        {taken.state === 'taking' ? 'Taking…' : label}
      </button>
      {r ? <button className="button quiet" onClick={() => setMethod((v) => !v)} aria-expanded={method}>{method ? 'Hide method' : 'Method'}</button> : null}
      {method && r ? <Method reading={r} /> : null}
    </div>
  );
}

function Method({ reading }: { reading: Reading }) {
  const queries = reading.method.queries ?? (reading.method.query ? [reading.method.query] : []);
  return (
    <div className={styles.method}>
      <p>How this was read · {reading.method.kind}{reading.redacted.length ? ` · redacted: ${reading.redacted.join(', ')}` : ''}</p>
      {queries.map((query, index) => <pre key={index} className="readout">{query}</pre>)}
      <details>
        <summary>Complete returned reading · JSON</summary>
        <pre className="readout">{JSON.stringify(reading, null, 2)}</pre>
      </details>
    </div>
  );
}

/**
 * One step of a composition: its number, its question, what it found, where that came from, and
 * its evidence below. A step that was not read says so in its own shape rather than going blank.
 */
export function Step({ n, question, known, finding, reading, cls, basis, children, id }: {
  n: number;
  question: string;
  known: Known;
  finding: ReactNode;
  reading: string;
  cls?: Cls;
  basis?: string | null;
  children?: ReactNode;
  id?: string;
}) {
  return (
    <li className={`${styles.step} ${isHole(known) ? styles.hole : ''}`} aria-labelledby={id ? `${id}-title` : undefined}>
      <span className={styles.stepNumber} aria-hidden="true">{n}</span>
      <div className={styles.stepBody}>
        <h3 id={id ? `${id}-title` : undefined} className={styles.stepTitle}><span className="srOnly">Step {n}: </span>{question}</h3>
        <p className={styles.finding}><OutcomeMark known={known}>{finding}</OutcomeMark></p>
        <Provenance reading={reading} cls={cls} basis={basis} />
        {children ? <div className={styles.evidence}>{children}</div> : null}
      </div>
    </li>
  );
}

/** The same composition, as an agent is handed it: the calls in order, values filled in. */
export function AgentRecipe({ question, steps, children }: { question: string; steps: RecipeStep[]; children?: ReactNode }) {
  const text = recipeText(question, steps);
  const held = useRef<HTMLPreElement>(null);
  return (
    <section className={styles.recipe} aria-labelledby="recipe-title">
      <div className={styles.recipeHead}>
        <div>
          <h2 id="recipe-title" className={styles.recipeTitle}>Your agent can ask for exactly this</h2>
          <p className={styles.recipeLede}>The same readings in the same order, with this page’s values filled in. An agent connected at <code>/mcp</code> gets the same envelopes you see here.</p>
        </div>
        <div className={styles.recipeActions}>
          <CopyButton text={text} selectRef={held} label="Copy for an agent" />
          {children}
        </div>
      </div>
      <ol className={styles.recipeSteps}>
        {steps.map((step, index) => (
          <li key={index}>
            <code className={styles.recipeCall}>{step.reading}<span className={styles.recipeParams}>{Object.keys(step.params).length ? ` ${JSON.stringify(step.params)}` : ''}</span></code>
            <span className={styles.recipeAnswers}>{step.answers}</span>
          </li>
        ))}
      </ol>
      <pre ref={held} className="srOnly">{text}</pre>
    </section>
  );
}
