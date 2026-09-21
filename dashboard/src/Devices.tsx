import { ReactNode, RefObject, useCallback, useEffect, useRef, useState } from 'react';
import { SignInLink, Unauthorized, signInLink } from './api';
import { CopyButton } from './Copy';
import { useApp } from './store';
import styles from './Devices.module.css';

const PHONE_NOTE = 'https://github.com/SuperDuperDave/system-sentinel/blob/main/docs/DEPLOY.md#optional-reach-it-from-your-phone';

/**
 * Sign in another device.
 *
 * A phone cannot be told a token — reading one off a screen is how tokens end up in photographs —
 * so the machine is asked how it is reached, mints a code that lasts five minutes and is spent by
 * the first device to follow it, and draws the two as a QR code a camera carries across. Nothing
 * on this surface holds the token.
 *
 * What comes back is a reading, so the dialog keeps its answers apart: an address; a machine that
 * answered and publishes nothing, which is a thing the person can fix and the words say how, for
 * the case that is true; and a machine that could not be asked, which is not a no. Each of those
 * either offers a fresh link or offers the same question again, so there is one action beside
 * Close and never two. It opens over
 * the work and closes back onto it: nothing behind it moves, and no view is left changed by having
 * looked.
 */
export function Devices({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const held = useRef<HTMLElement>(null);
  const [link, setLink] = useState<SignInLink | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const setSession = useApp((s) => s.setSession);

  // The dialog element owns modality: the backdrop, the focus trap and Escape are the browser's.
  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let mine = true;
    setLink(null);
    setProblem(null);
    signInLink()
      .then((answer) => mine && setLink(answer))
      .catch((err: unknown) => {
        if (!mine) return;
        if (err instanceof Unauthorized) return setSession('closed');
        setProblem(err instanceof Error ? err.message : String(err));
      });
    return () => {
      mine = false;
    };
  }, [open, nonce, setSession]);

  const again = useCallback(() => setNonce((n) => n + 1), []);
  const { body, action } = answer(link, problem, held);

  return (
    <dialog ref={dialog} className={styles.dialog} onClose={onClose} aria-labelledby="devices-title">
      <h2 id="devices-title" className={`${styles.title} display`}>Sign in another device</h2>
      {body}
      <div className={styles.actions}>
        {action ? <button className={styles.action} onClick={again}>{action}</button> : null}
        <button className={styles.action} onClick={() => dialog.current?.close()}>Close</button>
      </div>
    </dialog>
  );
}

/** What the dialog says, and the one word for asking again — the same handler either way. */
function answer(link: SignInLink | null, problem: string | null, held: RefObject<HTMLElement | null>): { body: ReactNode; action: string | null } {
  if (problem) return { body: <p className={styles.line}>Could not ask this machine: {problem}</p>, action: 'Try again' };
  if (!link) return { body: <p className={styles.line}>Asking this machine how it is reached…</p>, action: null };

  if (link.outcome === 'ok' && link.url) {
    return {
      action: 'New link',
      body: (
        <>
          {/* The server drew this from the same link the text below shows; nothing here parses it. */}
          <div className={styles.tile} dangerouslySetInnerHTML={{ __html: link.qr ?? '' }} />
          <div className={styles.linkRow}>
            <code className={`${styles.url} readout`} ref={held}>{link.url}</code>
            <CopyButton text={link.url} selectRef={held} label="Copy link" />
          </div>
          <p className={styles.line}>Scan it on a phone on your private network. It opens the dashboard signed in, once, within five minutes.</p>
          <p className={`${styles.note} readout`}>Reaches this machine through {link.via ?? 'a private network'} at {host(link.address)}.</p>
        </>
      ),
    };
  }

  if (link.outcome === 'empty') {
    const serve = <code className={styles.inline}>tailscale serve --bg {link.port}</code>;
    return {
      action: 'Ask again',
      body: (
        <>
          <p className={styles.line}>
            {link.installed
              ? <>Tailscale is on this machine but does not publish the dashboard yet. On this machine, sign in to Tailscale if you have not, then run {serve}.</>
              : <>This machine is not on a private network yet. Install Tailscale on this machine, sign in, then run {serve}.</>}
          </p>
          <p className={styles.note}>
            <a href={PHONE_NOTE} target="_blank" rel="noreferrer">How to reach it from your phone</a>
          </p>
        </>
      ),
    };
  }

  return { body: <p className={styles.line}>Could not ask this machine: {link.detail}</p>, action: 'Try again' };
}

/** The address without its scheme: what the person would recognise as this machine on the tailnet. */
function host(address: string | null): string {
  return (address ?? '').replace(/^https?:\/\//, '');
}
