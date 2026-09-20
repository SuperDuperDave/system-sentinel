import { FormEvent, ReactElement, useEffect, useState } from 'react';
import { openSession, take } from './api';
import { Lockup, Mark } from './Mark';
import { Live } from './Live';
import { useApp, VIEWS, ViewId } from './store';
import { Record } from './views/Record';
import { Errors } from './views/Errors';
import { Dumps } from './views/Dumps';
import { Machine } from './views/Machine';
import { Diagnostics } from './views/Diagnostics';
import { Signals } from './views/Signals';
import { Stack } from './views/Stack';
import { Agents } from './views/Agents';
import styles from './App.module.css';

const VIEW_COMPONENTS: { [K in ViewId]: () => ReactElement } = {
  record: Record,
  errors: Errors,
  dumps: Dumps,
  machine: Machine,
  diagnostics: Diagnostics,
  signals: Signals,
  stack: Stack,
  agents: Agents,
};

export function App() {
  const session = useApp((s) => s.session);
  const setSession = useApp((s) => s.setSession);

  // One cheap request decides whether a session exists; the health reading is that request.
  useEffect(() => {
    if (session !== 'unknown') return;
    take('health').then(() => setSession('open')).catch(() => setSession('closed'));
  }, [session, setSession]);

  if (session === 'closed') return <SignIn />;
  return <Shell />;
}

function Shell() {
  const view = useApp((s) => s.view);
  const setView = useApp((s) => s.setView);
  const View = VIEW_COMPONENTS[view];
  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <Lockup />
        <Live />
      </header>
      <div className={styles.trace} aria-hidden="true" />
      <nav className={styles.nav} aria-label="Views">
        {VIEWS.map((v) => (
          <button key={v.id} className={`${styles.navItem} ${v.id === view ? styles.navActive : ''}`} onClick={() => setView(v.id)} aria-current={v.id === view ? 'page' : undefined}>
            {v.label}
          </button>
        ))}
      </nav>
      <main className={styles.main}><View /></main>
    </div>
  );
}

function SignIn() {
  const setSession = useApp((s) => s.setSession);
  const [token, setToken] = useState('');
  const [wrong, setWrong] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const ok = await openSession(token);
    setBusy(false);
    if (ok) setSession('open');
    else setWrong(true);
  }

  return (
    <div className={styles.signIn}>
      <form className={styles.signInPanel} onSubmit={submit}>
        <Mark className={styles.signInMark} />
        <h1 className={`${styles.signInTitle} display`}>System Sentinel</h1>
        <p className={styles.signInLede}>A stethoscope for your computer. Enter the access token this machine created when the tool first ran.</p>
        <label className="label" htmlFor="token">Access token</label>
        <input id="token" className={`${styles.tokenInput} readout`} type="password" autoComplete="current-password" value={token} onChange={(e) => { setToken(e.target.value); setWrong(false); }} autoFocus />
        {wrong ? <p className={`${styles.wrong} readout`}>That token was not accepted.</p> : null}
        <button className={styles.signInButton} type="submit" disabled={busy || !token.trim()}>Open</button>
        <p className={`${styles.hint} readout`}>On the machine: <code>system-sentinel token</code></p>
      </form>
    </div>
  );
}
