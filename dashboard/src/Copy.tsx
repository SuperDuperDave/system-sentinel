import { RefObject, useEffect, useRef, useState } from 'react';
import styles from './Copy.module.css';

/**
 * Copy a block of text, with the fallback the remote path needs. The clipboard API is only
 * granted in a secure context, and a phone reaching this machine over plain http on a private
 * network is not one; when it refuses, the text itself is selected so the person can copy it by
 * hand. The control says which of the two happened rather than failing silently.
 */
export function CopyButton({ text, selectRef, label = 'Copy' }: { text: string; selectRef?: RefObject<HTMLElement | null>; label?: string }) {
  const [feedback, setFeedback] = useState<{ text: string; kind: 'copied' | 'select' } | null>(null);
  const state = feedback?.text === text ? feedback.kind : 'ready';
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  async function copy() {
    if (timer.current) clearTimeout(timer.current);
    try {
      await navigator.clipboard.writeText(text);
      setFeedback({ text, kind: 'copied' });
      timer.current = setTimeout(() => setFeedback(null), 2500);
    } catch {
      selectAll(selectRef?.current ?? null);
      setFeedback({ text, kind: 'select' });
    }
  }

  return (
    <button className={styles.button} onClick={copy} aria-live="polite">
      {state === 'copied' ? 'Copied' : state === 'select' ? 'Selected — copy it' : label}
    </button>
  );
}

function selectAll(element: HTMLElement | null): void {
  const selection = window.getSelection();
  if (!element || !selection) return;
  const range = document.createRange();
  range.selectNodeContents(element);
  selection.removeAllRanges();
  selection.addRange(range);
}
