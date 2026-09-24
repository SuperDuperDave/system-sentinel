import { useEffect, useState } from 'react';
import { HttpError, Unauthorized, take } from './api';
import { firstLine } from './Outcome';
import { useApp } from './store';
import styles from './Live.module.css';

interface Health {
  bridge: { powershell?: string };
}

const CHECK_MS = 5_000;
const RETRY_MS = 20_000;
type Status = 'checking' | 'live' | 'issue' | 'offline';

/** The header checks Sentinel's bridge; log records belong to the record stream's consumers. */
export function Live() {
  const setSession = useApp((s) => s.setSession);
  const [status, setStatus] = useState<Status>('checking');
  const [readout, setReadout] = useState('bridge · checking…');

  useEffect(() => {
    let active = true;
    let pending = false;
    let refreshOnVisible = false;
    let timer: number | undefined;
    let lastChecked: string | null = null;

    function schedule(delay: number) {
      if (!active || document.hidden) return;
      timer = window.setTimeout(() => { void check(); }, delay);
    }

    async function check() {
      if (!active || pending || document.hidden) return;
      pending = true;
      refreshOnVisible = false;
      let delay = CHECK_MS;
      try {
        const reading = await take<Health>('health');
        if (!active || refreshOnVisible) return;
        lastChecked = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        setStatus('live'); // Sentinel answered; its bridge outcome is stated separately.
        if (reading.outcome === 'ok') {
          const bridge = reading.sections[0]?.data.bridge;
          setReadout(`bridge · PowerShell ${bridge?.powershell ?? 'answered'} · ${reading.took_ms} ms`);
        } else {
          const busy = reading.error?.kind === 'busy' ? ' (Sentinel busy)' : '';
          const detail = reading.error?.detail ? ` · ${firstLine(reading.error.detail)}` : '';
          setReadout(`bridge · ${reading.outcome}${busy}${detail}`);
          delay = RETRY_MS;
        }
      } catch (error) {
        if (!active) return;
        if (error instanceof Unauthorized) {
          active = false;
          setSession('closed');
          return;
        }
        if (!refreshOnVisible) {
          if (error instanceof HttpError) {
            setStatus('issue');
            if (error.code === 'redaction_withheld') {
              setReadout(`name lookup failed · redacted answers withheld · retry in ${error.retryAfter ?? 60} s`);
              delay = Math.max(RETRY_MS, (error.retryAfter ?? 60) * 1_000);
            } else {
              setReadout(`bridge · check failed (${error.status})`);
            }
          } else {
            setStatus('offline');
            setReadout(lastChecked ? `bridge · not checked since ${lastChecked}` : 'bridge · unreachable');
          }
        }
        delay = RETRY_MS;
      } finally {
        pending = false;
        if (refreshOnVisible && active && !document.hidden) {
          refreshOnVisible = false;
          void check();
        } else {
          schedule(delay);
        }
      }
    }

    function onVisibility() {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      if (document.hidden) return;
      setStatus('checking');
      setReadout('bridge · checking…');
      if (pending) refreshOnVisible = true;
      else void check();
    }

    document.addEventListener('visibilitychange', onVisibility);
    void check();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [setSession]);

  return (
    <span className={styles.zone}>
      <span
        className={`${styles.word} ${status === 'live' ? styles.lit : styles.dark} readout`}
        role="status"
        title={status === 'live' ? 'Sentinel returned the latest Health reading; see the readout for the bridge outcome' : status === 'checking' ? 'Checking Sentinel now' : status === 'issue' && readout.startsWith('name lookup failed') ? 'Sentinel withheld redacted data until it can retry learning this computer’s names' : status === 'issue' ? 'The Health check returned an HTTP error; Sentinel or a gateway may have answered' : 'Sentinel did not answer the latest check'}
      >
        {status}
      </span>
      <span className={`${styles.bridge} readout`} title={readout}>{shorten(readout)}</span>
    </span>
  );
}

/** The header carries one line. A longer failure stays on the readout, one hover away. */
function shorten(text: string): string {
  return text.length > 76 ? `${text.slice(0, 75)}…` : text;
}
