import { FormEvent, ReactElement, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Unauthorized, catalog, openSession } from './api';
import { Devices } from './Devices';
import { Lockup, Mark } from './Mark';
import { NavIcon } from './NavIcon';
import { Live } from './Live';
import { canRestoreCrashView, clearHeldReadings, hasHeldReading } from './useReading';
import { useApp, VIEWS, ViewId } from './store';
import { Record } from './views/Record';
import { Errors } from './views/Errors';
import { Crashes } from './views/Crashes';
import { Machine } from './views/Machine';
import { Performance } from './views/Performance';
import { Space } from './views/Space';
import { Diagnostics } from './views/Diagnostics';
import { Signals } from './views/Signals';
import { Stack } from './views/Stack';
import { Agents } from './views/Agents';
import { Home } from './views/Home';
import { CaseView } from './views/Case';
import { Appearance } from './Appearance';
import { useCase, useCaseList } from './useCases';
import styles from './App.module.css';

const VIEW_COMPONENTS: { [K in ViewId]: () => ReactElement } = {
  home: Home,
  case: CaseView,
  record: Record,
  errors: Errors,
  crashes: Crashes,
  machine: Machine,
  performance: Performance,
  space: Space,
  diagnostics: Diagnostics,
  signals: Signals,
  stack: Stack,
  agents: Agents,
};

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
  const caseId = useApp((s) => s.caseId);
  const setView = useApp((s) => s.setView);
  const restoreAddress = useApp((s) => s.restoreAddress);
  const [devices, setDevices] = useState(false);
  const View = VIEW_COMPONENTS[view];
  const activeView = VIEWS.find((item) => item.id === view) ?? VIEWS[0];
  const mobileNavigation = useRef<HTMLDetailsElement>(null);
  const previousNavigation = useRef(`${view}\u0000${moment ?? ''}\u0000${view === 'case' ? caseId : ''}`);
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
    const current = `${view}\u0000${moment ?? ''}\u0000${view === 'case' ? caseId : ''}`;
    if (previousNavigation.current === current) return;
    const formerView = previousNavigation.current.split('\u0000')[0];
    previousNavigation.current = current;
    closeMobileNavigation();
    const { viewScroll, crashesView: { stopCount, faultCount, focus, changesBefore }, recordOrigin, recordReturnKey, spaceView } = useApp.getState();
    // Restore only against evidence available at first paint. The requested Crashes section
    // determines which earlier panels must also be held for the saved position to be meaningful.
    const crashesReady = canRestoreCrashView(stopCount, faultCount, focus, changesBefore);
    const returning = formerView !== view && viewScroll[view] !== undefined &&
      (view === 'crashes' ? crashesReady : view === 'signals' ? hasHeldReading('signals')
        // Space draws its held walk from the store at first paint, so its saved position is meaningful.
        : view === 'space' && spaceView.levels.some((level) => level.reading !== null));
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
  }, [view, moment, caseId]);

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

  const lens = caseId && view !== 'case' && view !== 'home';

  return (
    <div className={styles.shell}>
      <a className={styles.skipLink} href="#content">Skip to the page</a>
      <aside className={styles.rail}>
        <div className={styles.railHead}><Lockup /></div>
        <nav className={styles.nav} aria-label="Views">
          <NavChoices view={view} onChoose={setView} onDevices={() => setDevices(true)} />
        </nav>
        <div className={styles.railFoot}>
          <Live />
          <Appearance />
        </div>
      </aside>
      <header className={styles.phoneBar}>
        <Lockup />
        <Live />
      </header>
      <nav className={styles.mobileNav} aria-label="Views">
        <details ref={mobileNavigation}>
          <summary className={styles.mobileSummary}>
            <span className={styles.mobileCurrent}>
              <NavIcon name={activeView.id} />
              <span className={styles.mobileTitle}>{view === 'case' ? 'Case' : activeView.label}</span>
            </span>
            <span className={styles.mobileToggle}>Menu <span className={styles.mobileChevron} aria-hidden="true" /></span>
          </summary>
          <div className={styles.mobileChoices}>
            <NavChoices view={view} onChoose={(next) => { closeMobileNavigation(); setView(next); }} onDevices={() => { closeMobileNavigation(); setDevices(true); }} onCase={closeMobileNavigation} />
            <Appearance />
          </div>
        </details>
      </nav>
      <main id="content" className={styles.main} tabIndex={-1}>
        {lens ? <CaseBar id={caseId} /> : null}
        <div className={lens ? styles.lens : undefined}><View /></div>
      </main>
      <Devices open={devices} onClose={() => setDevices(false)} />
    </div>
  );
}

/**
 * Where "Add" puts things while a person reads for evidence. It holds orientation (which case)
 * and the way back, and nothing else, so it can stay in view while the reading scrolls.
 */
function CaseBar({ id }: { id: string }) {
  const openCase = useApp((s) => s.openCase);
  const releaseCase = useApp((s) => s.releaseCase);
  const heldTitle = useApp((s) => s.caseTitle);
  const holdCase = useApp((s) => s.holdCase);
  const loaded = useCase(heldTitle ? null : id);
  useEffect(() => { if (loaded.state === 'ok') holdCase(id, loaded.value.title); }, [loaded, id, holdCase]);
  const title = heldTitle ?? (loaded.state === 'ok' ? loaded.value.title : loaded.state === 'loading' ? 'Opening the case…' : 'A case this server did not return');
  return (
    <div className={styles.caseBar} role="region" aria-label="Case in hand">
      <span className={styles.caseBarWhat}>
        <span className="label">Adding evidence to</span>
        <span className={styles.caseBarTitle}>{title}</span>
      </span>
      <span className={styles.caseBarActions}>
        <button className={styles.caseBarBack} onClick={() => openCase(id)}>Back to the case</button>
        <button className={styles.caseBarRelease} onClick={releaseCase}>Set it down</button>
      </span>
    </div>
  );
}

const READING_ORDER: ViewId[] = ['crashes', 'record', 'errors', 'signals', 'machine', 'performance', 'space', 'diagnostics'];

function NavChoices({ view, onChoose, onDevices, onCase }: { view: ViewId; onChoose: (next: ViewId) => void; onDevices: () => void; onCase?: () => void }) {
  const cases = useCaseList();
  const caseId = useApp((s) => s.caseId);
  const openCase = useApp((s) => s.openCase);
  const open = cases.state === 'ok' ? cases.value.filter((c) => c.state === 'open') : [];
  const item = (id: ViewId) => {
    const meta = VIEWS.find((v) => v.id === id)!;
    return (
      <button key={id} className={`${styles.navItem} ${id === view ? styles.navActive : ''}`} onClick={() => onChoose(id)} aria-current={id === view ? 'page' : undefined}>
        <NavIcon name={id} />
        <span>{meta.label}</span>
      </button>
    );
  };
  return <>
    <div className={styles.navGroup}>{item('home')}</div>
    <div className={styles.navGroup}>
      <span className={styles.navGroupLabel}>Open cases</span>
      {open.map((c) => {
        const current = view === 'case' && caseId === c.id;
        return (
          <button key={c.id} className={`${styles.navCase} ${current ? styles.navActive : ''}`} onClick={() => { onCase?.(); openCase(c.id); }} aria-current={current ? 'page' : undefined}>
            <span className={styles.navCaseTitle}>{c.title}</span>
            {caseId === c.id && view !== 'case' ? <span className={styles.navCaseHeld}>In hand: “Add” puts evidence here</span> : null}
            {c.pending.length ? <span className={styles.navCaseReview}>{c.pending.length} to review</span> : null}
          </button>
        );
      })}
      {cases.state === 'ok' && !open.length ? <span className={styles.navNone}>None open</span> : null}
      {cases.state === 'unavailable' ? <span className={styles.navNone}>This server has no case routes</span> : null}
    </div>
    <div className={styles.navGroup}>
      <span className={styles.navGroupLabel}>Readings</span>
      {READING_ORDER.map(item)}
    </div>
    <div className={styles.navGroup}>
      <span className={styles.navGroupLabel}>Agent</span>
      {item('agents')}
      {item('stack')}
      <button className={styles.navItem} onClick={onDevices} aria-haspopup="dialog">
        <NavIcon name="device" />
        <span>Sign in another device</span>
      </button>
    </div>
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
