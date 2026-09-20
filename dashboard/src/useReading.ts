import { useCallback, useEffect, useRef, useState } from 'react';
import { Reading, Unauthorized, take } from './api';
import { useApp } from './store';

type ParamValue = string | number | boolean | number[];

export interface Taken<T> {
  /** idle: not asked yet; taking: in flight; done: an envelope arrived (any outcome); lost: the request itself failed. */
  state: 'idle' | 'taking' | 'done' | 'lost';
  reading: Reading<T> | null;
  problem: string | null;
  retake: () => void;
}

/** Take a reading when its parameters change, and on demand. The envelope's outcome is the view's to show. */
export function useReading<T = unknown>(name: string, params: Record<string, ParamValue> = {}, enabled = true): Taken<T> {
  const key = JSON.stringify([name, params]);
  const [state, setState] = useState<Taken<T>['state']>('idle');
  const [reading, setReading] = useState<Reading<T> | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const setSession = useApp((s) => s.setSession);
  const latest = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    const mine = ++latest.current;
    setState('taking');
    setProblem(null);
    take<T>(name, params)
      .then((r) => {
        if (mine !== latest.current) return;
        setReading(r);
        setState('done');
        setSession('open');
      })
      .catch((err: unknown) => {
        if (mine !== latest.current) return;
        if (err instanceof Unauthorized) {
          setSession('closed');
          setState('idle');
          return;
        }
        setProblem(err instanceof Error ? err.message : String(err));
        setState('lost');
      });
    // params is captured by key, deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce, enabled]);

  const retake = useCallback(() => setNonce((n) => n + 1), []);
  return { state, reading, problem, retake };
}
