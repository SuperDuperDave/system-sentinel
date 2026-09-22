import { useCallback, useEffect, useState } from 'react';
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

type Result<T> = Pick<Taken<T>, 'state' | 'reading' | 'problem'> & { key: string };

/** Take a reading when its parameters change, and on demand. The envelope's outcome is the view's to show. */
export function useReading<T = unknown>(name: string, params: Record<string, ParamValue> = {}, enabled = true): Taken<T> {
  const key = JSON.stringify([name, params]);
  const [result, setResult] = useState<Result<T>>({ key, state: 'idle', reading: null, problem: null });
  const [nonce, setNonce] = useState(0);
  const setSession = useApp((s) => s.setSession);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setResult((previous) => ({ key, state: 'taking', reading: previous.key === key ? previous.reading : null, problem: null }));
    take<T>(name, params)
      .then((r) => {
        if (!active) return;
        setResult({ key, state: 'done', reading: r, problem: null });
        setSession('open');
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof Unauthorized) {
          setSession('closed');
          setResult({ key, state: 'idle', reading: null, problem: null });
          return;
        }
        setResult({ key, state: 'lost', reading: null, problem: err instanceof Error ? err.message : String(err) });
      });
    return () => { active = false; };
    // params is captured by key, deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce, enabled]);

  const retake = useCallback(() => setNonce((n) => n + 1), []);
  return result.key === key
    ? { state: result.state, reading: result.reading, problem: result.problem, retake }
    : { state: enabled ? 'taking' : 'idle', reading: null, problem: null, retake };
}
