import { useState } from 'react';
import { Unauthorized } from './api';
import { useApp } from './store';
import { Duplicate, NewItem, addItem } from './stack';
import styles from './AddToStack.module.css';

/**
 * The one control every view uses to hand evidence to the stack: the reading it is looking at,
 * or the records the person picked out of it. What is stacked is the envelope as it was read,
 * so its outcome and its method travel with it and a failed reading cannot enter a handoff
 * disguised as a finding.
 *
 * It answers in place. Nothing opens, nothing moves, and the answer distinguishes the three
 * things that can happen: it went on the stack, the stack already holds this same evidence
 * (the boundary's own rule: same reading, same parameters, same records), or the add failed.
 */
export function AddToStack({ item, label = 'Add to stack' }: { item: NewItem; label?: string }) {
  const [state, setState] = useState<'ready' | 'adding' | 'added' | 'duplicate' | 'failed'>('ready');
  const [problem, setProblem] = useState<string | null>(null);
  const setSession = useApp((s) => s.setSession);
  const key = signature(item);
  const [seen, setSeen] = useState(key);

  // The same control offered for different evidence is a fresh offer: a new reading was taken,
  // or another record was picked. The signature is the boundary's duplicate rule, so the reset
  // and the refusal cannot disagree.
  if (seen !== key) {
    setSeen(key);
    setState('ready');
    setProblem(null);
  }

  async function add() {
    setState('adding');
    try {
      await addItem(item);
      setState('added');
    } catch (err: unknown) {
      if (err instanceof Duplicate) return setState('duplicate');
      if (err instanceof Unauthorized) return setSession('closed');
      setProblem(err instanceof Error ? err.message : String(err));
      setState('failed');
    }
  }

  if (state === 'added' || state === 'duplicate') {
    return (
      <span className={`${styles.done} readout`} role="status">
        {state === 'added' ? 'In the stack' : 'Already in the stack'}
      </span>
    );
  }

  return (
    <span className={styles.wrap}>
      <button className={styles.button} onClick={add} disabled={state === 'adding'}>
        {state === 'adding' ? 'Adding…' : label}
      </button>
      {state === 'failed' ? (
        <span className={`${styles.problem} readout`} role="status">
          {problem}
        </span>
      ) : null}
    </span>
  );
}

/** What makes two offers the same evidence: the boundary refuses a second one on exactly this. */
function signature(item: NewItem): string {
  if (item.kind === 'note') return `note:${item.note}`;
  const ids = 'ids' in item ? item.ids : null;
  if ('envelope' in item) return JSON.stringify([item.kind, item.envelope.reading, item.envelope.params, ids]);
  return JSON.stringify([item.kind, item.take.name, item.take.params, ids]);
}
