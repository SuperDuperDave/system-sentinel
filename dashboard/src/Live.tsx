import { useEffect, useState } from 'react';
import { take } from './api';
import { firstLine } from './Outcome';
import styles from './Live.module.css';

interface Health {
  bridge: { available: boolean; powershell?: string; took_ms?: number; outcome: string };
}

/**
 * The one lit word on the surface, and the bridge readout beside it.
 *
 * `live` means the log stream is connected: the tool is polling both logs and would say so if a
 * poll stopped observing the machine. A silent stream is not a healthy machine, so the word is
 * about the connection and the readout is about the machine — two facts, never conflated.
 *
 * The frames of one poll arrive in order: a `bridge` frame only when that poll did not observe
 * the machine, then always a `heartbeat`. So the heartbeat is what settles the readout, showing
 * the verdict of the poll that just finished rather than the last trouble seen at any time.
 */
export function Live() {
  const [connected, setConnected] = useState(false);
  const [health, setHealth] = useState('');
  const [trouble, setTrouble] = useState<string | null>(null);

  useEffect(() => {
    take<Health>('health')
      .then((r) => {
        const b = r.sections[0]?.data.bridge;
        setHealth(r.outcome === 'ok' && b ? `bridge · PowerShell ${b.powershell} · ${b.took_ms} ms` : `bridge · ${r.outcome}`);
      })
      .catch(() => setHealth('bridge · unreachable'));
  }, []);

  useEffect(() => {
    const source = new EventSource('/api/stream');
    let pending: string | null = null;

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.addEventListener('bridge', (event) => {
      const data = parse(event);
      const detail = data.error ? ` · ${firstLine(String(data.error))}` : '';
      pending = `bridge · ${data.outcome ?? 'not observed'}${detail}`;
    });
    source.addEventListener('heartbeat', () => {
      setConnected(true);
      setTrouble(pending);
      pending = null;
    });

    return () => source.close();
  }, []);

  return (
    <span className={styles.zone}>
      <span
        className={`${styles.word} ${connected ? styles.lit : styles.dark} readout`}
        role="status"
        title={connected ? 'the log stream is connected; each poll reports whether it observed the machine' : 'the log stream is not connected'}
      >
        {connected ? 'live' : 'offline'}
      </span>
      <span className={`${styles.bridge} readout`} title={trouble ?? undefined}>{trouble ? shorten(trouble) : health}</span>
    </span>
  );
}

/** The header carries one line. What a failure said in full stays on the readout, one hover away. */
function shorten(text: string): string {
  return text.length > 76 ? `${text.slice(0, 75)}…` : text;
}

function parse(event: Event): Record<string, unknown> {
  try {
    return JSON.parse((event as MessageEvent).data);
  } catch {
    return {};
  }
}
