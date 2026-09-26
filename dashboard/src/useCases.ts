import { useEffect, useState } from 'react';
import { Unauthorized } from './api';
import { Case, CaseSummary, CasesUnavailable, getCase, listCases } from './cases';
import { useApp } from './store';

export type Loaded<T> = { state: 'loading' } | { state: 'ok'; value: T } | { state: 'unavailable' } | { state: 'failed'; problem: string };

/** Read one case route again whenever any case changes; a 401 closes the session like any reading. */
function useCaseRoute<T>(load: (() => Promise<T>) | null, key: string): Loaded<T> {
  const version = useApp((s) => s.casesVersion);
  const setSession = useApp((s) => s.setSession);
  const [result, setResult] = useState<{ key: string; loaded: Loaded<T> }>({ key, loaded: { state: 'loading' } });
  useEffect(() => {
    if (!load) return;
    let active = true;
    load()
      .then((value) => { if (active) setResult({ key, loaded: { state: 'ok', value } }); })
      .catch((error: unknown) => {
        if (!active) return;
        if (error instanceof Unauthorized) { setSession('closed'); return; }
        setResult({ key, loaded: error instanceof CasesUnavailable ? { state: 'unavailable' } : { state: 'failed', problem: error instanceof Error ? error.message : String(error) } });
      });
    return () => { active = false; };
    // load is identified by key, deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version, setSession]);
  return result.key === key ? result.loaded : { state: 'loading' };
}

export function useCaseList(): Loaded<CaseSummary[]> {
  return useCaseRoute(listCases, 'list');
}

export function useCase(id: string | null): Loaded<Case> {
  return useCaseRoute(id ? () => getCase(id) : null, `case:${id}`);
}
