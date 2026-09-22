import { FormEvent, ReactElement, useEffect, useRef, useState } from 'react';
import { catalog, openSession } from './api';
import { Devices } from './Devices';
import { Lockup, Mark } from './Mark';
import { NavIcon } from './NavIcon';
import { Live } from './Live';
import { useApp, VIEWS, ViewGroup, ViewId } from './store';
import { Record } from './views/Record';
import { Errors } from './views/Errors';
import { Crashes } from './views/Crashes';
import { Machine } from './views/Machine';
import { Diagnostics } from './views/Diagnostics';
import { Signals } from './views/Signals';
import { Stack } from './views/Stack';
import { Agents } from './views/Agents';
import styles from './App.module.css';

const VIEW_COMPONENTS: { [K in ViewId]: () => ReactElement } = {
  record: Record,
  errors: Errors,
  crashes: Crashes,
  machine: Machine,
  diagnostics: Diagnostics,
  signals: Signals,
  stack: Stack,
  agents: Agents,
};
const NAV_GROUPS: ViewGroup[] = ['Evidence', 'Interpret', 'Carry'];

export function App() {
  const session = useApp((s) => s.session);
  const setSession = useApp((s) => s.setSession);

  // One cheap request decides whether a session exists: the catalog, which touches no PowerShell.
  useEffect(() => {
    if (session !== 'unknown') return;
    catalog().then(() => setSession('open')).catch(() => setSession('closed'));
  }, [session, setSession]);

  if (session === 'closed') return <SignIn />;
  return <Shell />;
}

function Shell() {
  const view = useApp((s) => s.view);
  const moment = useApp((s) => s.moment);
  const setView = useApp((s) => s.setView);
  const restoreAddress = useApp((s) => s.restoreAddress);
  const [devices, setDevices] = useState(false);
  const View = VIEW_COMPONENTS[view];
  const previousNavigation = useRef(`${view}\u0000${moment ?? ''}`);

  useEffect(() => {
    window.addEventListener('popstate', restoreAddress);
    return () => window.removeEventListener('popstate', restoreAddress);
  }, [restoreAddress]);

  // A view change or moment jump is navigation for a keyboard reader too. Focus the new title,
  // and put it on screen even when the action came from far down another view.
  useEffect(() => {
    const current = `${view}\u0000${moment ?? ''}`;
    if (previousNavigation.current === current) return;
    previousNavigation.current = current;
    window.scrollTo(0, 0);
    const title = document.querySelector<HTMLElement>('main h1');
    if (title) {
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
    }
  }, [view, moment]);

  // A copied section link may name a row created only after its reading answers. Wait for that
  // element, then let the browser land on it; ordinary in-page anchor clicks stay native.
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (!hash || hash === 'link') return;
    let id: string;
    try { id = decodeURIComponent(hash); } catch { return; }
    const reveal = () => {
      const target = document.getElementById(id);
      if (!target) return false;
      target.scrollIntoView({ block: 'start' });
      return true;
    };
    if (reveal()) return;
    const main = document.querySelector('main');
    if (!main) return;
    const observer = new MutationObserver(() => { if (reveal()) observer.disconnect(); });
    observer.observe(main, { childList: true, subtree: true });
    const expiry = window.setTimeout(() => observer.disconnect(), 15_000);
    return () => { observer.disconnect(); window.clearTimeout(expiry); };
  }, [view]);

  // The tray hands a phone over by landing here on #link. The hash is spent like the code that
  // came with it, so a reload is the dashboard and not this dialog again.
  useEffect(() => {
    if (window.location.hash !== '#link') return;
    setDevices(true);
    history.replaceState(null, '', window.location.pathname + window.location.search);
  }, []);

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <Lockup />
        <Live />
      </header>
      <div className={styles.trace} aria-hidden="true" />
      <nav className={styles.nav} aria-label="Views">
        {NAV_GROUPS.map((group) => (
          <div className={styles.navGroup} key={group}>
            <span className={`${styles.navGroupLabel} label`}>{group}</span>
            {VIEWS.filter((v) => v.group === group).map((v) => (
              <button key={v.id} className={`${styles.navItem} ${v.id === view ? styles.navActive : ''}`} onClick={() => setView(v.id)} aria-current={v.id === view ? 'page' : undefined}>
                <NavIcon name={v.id} />
                <span>{v.label}</span>
              </button>
            ))}
          </div>
        ))}
        <button className={`${styles.navItem} ${styles.navAside}`} onClick={() => setDevices(true)} aria-haspopup="dialog">
          <NavIcon name="device" />
          <span>Sign in another device</span>
        </button>
      </nav>
      <main className={styles.main}><View /></main>
      <Devices open={devices} onClose={() => setDevices(false)} />
    </div>
  );
}

function SignIn() {
  const setSession = useApp((s) => s.setSession);
  const [token, setToken] = useState('');
  const [wrong, setWrong] = useState(false);
  const [busy, setBusy] = useState(false);
  const field = useRef<HTMLInputElement>(null);

  // This screen is one field, so focus belongs in it. Moved here rather than by the autoFocus
  // attribute: focus is a thing that happens at a moment, and this is the moment.
  useEffect(() => { field.current?.focus(); }, []);

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
        <input id="token" className={`${styles.tokenInput} readout`} type="password" autoComplete="current-password" ref={field} value={token} onChange={(e) => { setToken(e.target.value); setWrong(false); }} />
        {wrong ? <p className={`${styles.wrong} readout`}>That token was not accepted.</p> : null}
        <button className={styles.signInButton} type="submit" disabled={busy || !token.trim()}>Open</button>
        {/* Two ways in, and the token is the second one. The executable signs a browser in by
            itself, so anyone reading this screen is either on a browser it did not open or
            running the tool from source. */}
        <p className={`${styles.hint} readout`}>
          The tray's Open dashboard, or double-clicking the file again, signs a browser in.
          From source: <code>system-sentinel token</code>.
        </p>
      </form>
    </div>
  );
}
