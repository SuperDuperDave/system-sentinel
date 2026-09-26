import { useState } from 'react';
import styles from './Appearance.module.css';

type Choice = 'auto' | 'light' | 'dark';
const KEY = 'sentinel-appearance';

/** A per-viewer convenience: kept in this browser only, and the page is whole without it. */
export function readAppearance(): Choice {
  try {
    const stored = localStorage.getItem(KEY);
    return stored === 'light' || stored === 'dark' ? stored : 'auto';
  } catch { return 'auto'; }
}

export function applyAppearance(choice: Choice) {
  if (choice === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
}

/** Paper or the instrument field. Auto follows the system, which is the strong default. */
export function Appearance() {
  const [choice, setChoice] = useState<Choice>(readAppearance);
  function choose(next: Choice) {
    setChoice(next);
    applyAppearance(next);
    try { if (next === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, next); } catch { /* the choice still applies to this page */ }
  }
  return (
    <div className={styles.group} role="group" aria-label="Appearance">
      {(['auto', 'light', 'dark'] as Choice[]).map((c) => (
        <button key={c} className={styles.choice} aria-pressed={choice === c} onClick={() => choose(c)}>
          {c === 'auto' ? 'Auto' : c === 'light' ? 'Paper' : 'Field'}
        </button>
      ))}
    </div>
  );
}
