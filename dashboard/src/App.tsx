import { FormEvent, ReactElement, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Unauthorized, catalog, openSession } from './api';
import { Devices } from './Devices';
import { Lockup, Mark } from './Mark';
import { NavIcon } from './NavIcon';
import { Live } from './Live';
import { canRestoreCrashView, clearHeldReadings, hasHeldReading } from './useReading';
import { useApp, VIEWS, ViewGroup, ViewId } from './store';
import { Record } from './views/Record';
import { Errors } from './views/Errors';
import { Crashes } from './views/Crashes';
import { Machine } from './views/Machine';
import { Performance } from './views/Performance';
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
  performance: Performance,
  diagnostics: Diagnostics,
  signals: Signals,
  stack: Stack,
  agents: Agents,
};
const NAV_GROUPS: ViewGroup[] = ['Evidence', 'Interpret', 'Carry'];

export function App() {
  const session = useApp((s) => s.session);
  const setSession = useApp((s) => s.setSession);
  const clearViewContext = useApp((s) => s.clearViewContext);

  useEffect(() => {
    if (session === 'closed') { clearHeldReadings(); clearViewContext(); }
  }, [session, clearViewContext]);

  // One cheap request decides whether a session exists: the catalog, which touches no PowerShell.
  useEffect(() => {
    if (session !== 'unknown') return;
    let active = true;
    catalog()
      .then(() => { if (active) setSession('open'); })
      .catch((error: unknown) => { if (active) setSession(error instanceof Unauthorized ? 'closed' : 'unreachable'); });
    return () => { active = false; };
  }, [session, setSession]);

  if (session === 'unknown') return <CheckingConnection />;
  if (session === 'unreachable') return <ConnectionUnavailable />;
  if (session === 'closed') return <SignIn />;
  return <Shell />;
}

function CheckingConnection() {
  return (
    <div className={styles.signIn}>
      <div className={styles.signInPanel} role="status">
        <Mark className={styles.signInMark} />
        <h1 className={`${styles.signInTitle} display`}>System Sentinel</h1>
        <p className={styles.signInLede}>Checking the connection…</p>
      </div>
    </div>
  );
}

function ConnectionUnavailable() {
  const setSession = useApp((s) => s.setSession);
  const retry = useRef<HTMLButtonElement>(null);
  useEffect(() => { retry.current?.focus(); }, []);
  return (
    <div className={styles.signIn}>
      <div className={styles.signInPanel}>
        <Mark className={styles.signInMark} />
        <h1 className={`${styles.signInTitle} display`}>Connection unavailable</h1>
        <p className={styles.signInLede}>The dashboard could not confirm a connection to System Sentinel. Check that the app is running, then try again.</p>
        <button ref={retry} className={styles.signInButton} onClick={() => setSession('unknown')}>Try again</button>
      </div>
    </div>
  );
}

function Shell() {
  const view = useApp((s) => s.view);
  const moment = useApp((s) => s.moment);
  const setView = useApp((s) => s.setView);
  const restoreAddress = useApp((s) => s.restoreAddress);
  const [devices, setDevices] = useState(false);
  const View = VIEW_COMPONENTS[view];
  const activeView = VIEWS.find((item) => item.id === view) ?? VIEWS[0];
  const mobileNavigation = useRef<HTMLDetailsElement>(null);
  const previousNavigation = useRef(`${view}\u0000${moment ?? ''}`);
  const closeMobileNavigation = () => { if (mobileNavigation.current) mobileNavigation.current.open = false; };

  useEffect(() => {
    window.addEventListener('popstate', restoreAddress);
    return () => window.removeEventListener('popstate', restoreAddress);
  }, [restoreAddress]);

  useEffect(() => {
    const former = history.scrollRestoration;
    history.scrollRestoration = 'manual';
    return () => { history.scrollRestoration = former; };
  }, []);

  // Restore the view's saved viewport before paint. A new view or moment starts at its title.
  useLayoutEffect(() => {
    const current = `${view}\u0000${moment ?? ''}`;
    if (previousNavigation.current === current) return;
    const formerView = previousNavigation.current.split('\u0000')[0];
    previousNavigation.current = current;
    closeMobileNavigation();
    const { viewScroll, crashesView: { stopCount, faultCount, focus, changesBefore }, recordOrigin, recordReturnKey } = useApp.getState();
    // Restore only against evidence available at first paint. The requested Crashes section
    // determines which earlier panels must also be held for the saved position to be meaningful.
    const crashesReady = canRestoreCrashView(stopCount, faultCount, focus, changesBefore);
    const returning = formerView !== view && viewScroll[view] !== undefined &&
      (view === 'crashes' ? crashesReady : view === 'signals' && hasHeldReading('signals'));
    window.scrollTo(0, returning ? viewScroll[view]! : 0);
    const main = document.querySelector('main');
    if (returning && formerView === 'record' && recordOrigin === view && recordReturnKey !== null) {
      const source = [...(main?.querySelectorAll<HTMLElement>('[data-moment-source]') ?? [])]
        .find((element) => element.dataset.momentSource === recordReturnKey);
      source?.focus({ preventScroll: true });
    }
    const focused = document.activeElement instanceof HTMLElement && main?.contains(document.activeElement)
      ? document.activeElement : null;
    if (returning && focused) {
      const box = focused.getBoundingClientRect();
      if (box.top < 0 || box.bottom > innerHeight) focused.scrollIntoView({ block: 'nearest' });
    }
    const title = document.querySelector<HTMLElement>('main h1');
    if (title && (!returning || !focused)) {
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
      <a className={styles.skipLink} href="#content">Skip to the reading</a>
      <header className={styles.header}>
        <Lockup />
        <Live />
      </header>
      <div className={styles.trace} aria-hidden="true" />
      <nav className={styles.nav} aria-label="Views">
        <NavChoices view={view} onChoose={setView} onDevices={() => setDevices(true)} />
      </nav>
      <nav className={styles.mobileNav} aria-label="Views">
        <details ref={mobileNavigation}>
          <summary className={styles.mobileSummary}>
            <span className={styles.mobileCurrent}>
              <NavIcon name={activeView.id} />
              <span><span className={`${styles.mobileEyebrow} label`}>Current view</span><span className={styles.mobileTitle}>{activeView.label}</span></span>
            </span>
            <span className={styles.mobileToggle}>All views <span className={styles.mobileChevron} aria-hidden="true" /></span>
          </summary>
          <div className={styles.mobileChoices}>
            <NavChoices view={view} onChoose={(next) => { closeMobileNavigation(); setView(next); }} onDevices={() => { closeMobileNavigation(); setDevices(true); }} />
          </div>
        </details>
      </nav>
      <main id="content" className={styles.main} tabIndex={-1}><View /></main>
      <Devices open={devices} onClose={() => setDevices(false)} />
    </div>
  );
}

function NavChoices({ view, onChoose, onDevices }: { view: ViewId; onChoose: (next: ViewId) => void; onDevices: () => void }) {
  return <>
    {NAV_GROUPS.map((group) => (
      <div className={styles.navGroup} key={group}>
        <span className={`${styles.navGroupLabel} label`}>{group}</span>
        {VIEWS.filter((item) => item.group === group).map((item) => (
          <button key={item.id} className={`${styles.navItem} ${item.id === view ? styles.navActive : ''}`} onClick={() => onChoose(item.id)} aria-current={item.id === view ? 'page' : undefined}>
            <NavIcon name={item.id} />
            <span>{item.label}</span>
          </button>
        ))}
      </div>
    ))}
    <button className={`${styles.navItem} ${styles.navAside}`} onClick={onDevices} aria-haspopup="dialog">
      <NavIcon name="device" />
      <span>Sign in another device</span>
    </button>
  </>;
}

function SignIn() {
  const setSession = useApp((s) => s.setSession);
  const [token, setToken] = useState('');
  const [problem, setProblem] = useState<'wrong' | 'unavailable' | null>(null);
  const [busy, setBusy] = useState(false);
  const field = useRef<HTMLInputElement>(null);

  // This screen is one field, so focus belongs in it. Moved here rather than by the autoFocus
  // attribute: focus is a thing that happens at a moment, and this is the moment.
  useEffect(() => { field.current?.focus(); }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      if (await openSession(token)) setSession('open');
      else setProblem('wrong');
    } catch {
      setProblem('unavailable');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.signIn}>
      <form className={styles.signInPanel} onSubmit={submit}>
        <Mark className={styles.signInMark} />
        <h1 className={`${styles.signInTitle} display`}>System Sentinel</h1>
        <p className={styles.signInLede}>A stethoscope for your computer. Enter the access token this machine created when the tool first ran.</p>
        <label className="label" htmlFor="token">Access token</label>
        <input id="token" className={`${styles.tokenInput} readout`} type="password" autoComplete="current-password" ref={field} value={token} disabled={busy} onChange={(e) => { setToken(e.target.value); setProblem(null); }} />
        {problem === 'wrong' ? <p className={`${styles.wrong} readout`} role="alert">That token was not accepted.</p> : null}
        {problem === 'unavailable' ? <p className={`${styles.wrong} readout`} role="alert">Sign-in could not complete. Check that System Sentinel is running, then try again.</p> : null}
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
