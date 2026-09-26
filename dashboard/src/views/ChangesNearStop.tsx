import { useState } from 'react';
import { AddEvidence } from '../AddEvidence';
import { Reading, observed } from '../api';
import { OutcomeLine } from '../Outcome';
import { duration, part, useKeepButtonInPlace } from '../Sections';
import { useApp } from '../store';
import { useReading } from '../useReading';
import styles from './ChangesNearStop.module.css';

interface Change {
  at: string | null;
  source: string;
  ref: { log: string; record_id: number | string };
  kind: string;
  subject: string | null;
  version: string | null;
  publisher: string | null;
  outside_window: boolean | null;
  error?: string;
}

interface RawChange { Log: string; RecordId: number | string; Id: number; TimeCreated: string; Data: Record<string, unknown> }
interface Source { outcome: string; returned: number; limit: number; truncated: boolean | null }
interface Reach { complete: boolean | null }

const SOURCES: [string, string][] = [
  ['windows_update', 'Windows Update'],
  ['device_configuration', 'Device configuration'],
  ['msi', 'MSI installation'],
];

const KINDS: Record<string, string> = {
  update_installed: 'Update installed', update_failed: 'Update failed',
  device_configured: 'Device configured',
  msi_install_succeeded: 'Installation succeeded', msi_install_failed: 'Installation failed', msi_install_unknown: 'Installation result unknown',
  msi_removal_succeeded: 'Removal succeeded', msi_removal_failed: 'Removal failed', msi_removal_unknown: 'Removal result unknown',
  unmapped_event: 'Result not decoded',
};

/** Looking at nearby changes stays inside the stop; no request occurs until this is opened. */
export function ChangesNearStop({ stopId, before }: { stopId: string; before: string }) {
  const open = useApp((state) => state.crashesView.changesStopId === stopId && state.crashesView.changesBefore === before);
  const setCrashesView = useApp((state) => state.setCrashesView);
  const keepButtonInPlace = useKeepButtonInPlace();
  return <section className={styles.region} aria-label="Changes before the estimated stop">
    <button type="button" className={styles.trigger} aria-expanded={open} onClick={(event) => {
      keepButtonInPlace(event.currentTarget);
      setCrashesView({ changesStopId: open ? null : stopId, changesBefore: open ? null : before });
    }}>
      <span>What changed before Windows’ stop estimate?</span>
      <span className={styles.action}>{open ? 'Hide change history' : 'Read change history'}</span>
    </button>
    {open ? <ChangeEvidence before={before} /> : null}
  </section>;
}

function ChangeEvidence({ before }: { before: string }) {
  const taken = useReading('changes', { before, hours: 168, count: 100 }, true, { hold: 'same-params' });
  const reading = taken.reading;
  const changes = part<Change[]>(reading, 'changes') ?? [];
  const raw = part<RawChange[]>(reading, 'records') ?? [];
  const collection = part<Record<string, Source>>(reading, 'collection');
  const coverage = part<Record<string, Reach>>(reading, 'coverage');
  const rawByRef = new Map<string, RawChange[]>();
  for (const row of raw) {
    const key = `${row.Log}:${row.RecordId}`;
    const matches = rawByRef.get(key);
    if (matches) matches.push(row);
    else rawByRef.set(key, [row]);
  }

  return <div className={styles.body}>
    <p className={styles.scope}>Windows Update, device configuration and MSI results in the seven days before Windows’ estimated stop time. A nearby change is a lead to inspect, not proof of a cause.</p>
    <p className={styles.boundary}>Window ends before <time dateTime={before}>{before}</time></p>
    <OutcomeLine taken={taken} noun="change results" singular="change result" emptyText="No matching change result returned in this requested window" />
    {collection ? <ul className={styles.sources} aria-label="Change source coverage">
      {SOURCES.map(([key, label]) => {
        const source = collection[key];
        const reach = coverage?.[key];
        return <li key={key}>
          <strong>{label}</strong>
          <span>{source?.outcome === 'ok' || source?.outcome === 'empty'
            ? `${source.returned} returned${source.truncated ? ` · ${source.limit}-record limit reached` : ''}`
            : `not read · ${source?.outcome ?? 'unknown'}`}</span>
          <span>{reach?.complete === true ? 'window covered' : reach?.complete === false ? 'window incomplete' : 'window reach unknown'}</span>
        </li>;
      })}
    </ul> : null}
    {observed(reading) && changes.length ? <>
      <p className={styles.listLabel}>Interpreted results · derived from returned rows · newest first · {changes.length}</p>
      <ol className={styles.list}>{[...changes].reverse().map((item, index) => {
        const matches = rawByRef.get(`${item.ref.log}:${item.ref.record_id}`) ?? [];
        return <li key={`${item.ref.log}:${item.ref.record_id}:${index}`}>
          <div className={styles.itemTop}><strong>{KINDS[item.kind] ?? item.kind}</strong>
            <time dateTime={item.at ?? undefined} title={item.at ?? undefined}>{item.outside_window === true ? 'outside the requested window'
              : item.outside_window === null ? 'window placement unverified' : placeBefore(item.at, before)}</time></div>
          <p className={styles.subject}>{item.subject ?? 'The returned event did not name a supported subject.'}</p>
          {item.version || item.publisher ? <p className={styles.extra}>{[item.version, item.publisher].filter(Boolean).join(' · ')}</p> : null}
          {item.outside_window === true ? <p className={styles.issue}>This returned row falls outside the requested window; do not use it as before-stop evidence.</p> : null}
          {item.outside_window === null ? <p className={styles.issue}>The row’s place in the requested window could not be verified.</p> : null}
          {item.error ? <p className={styles.issue}>{item.error}</p> : null}
          <RawRow row={matches.length === 1 ? matches[0] : undefined} refName={`${item.ref.log} record ${item.ref.record_id}`}
            missing={matches.length > 1 ? 'More than one raw row returned with this identity.' : 'Raw row not returned with this projection.'} />
        </li>;
      })}</ol>
    </> : null}
    {reading ? <div className={styles.actions}>
      <AddEvidence item={{ kind: 'reading', envelope: reading, title: 'Changes before an estimated stop' }} label="Stack this change history" />
      <FullReading reading={reading} />
    </div> : null}
  </div>;
}

function FullReading({ reading }: { reading: Reading }) {
  const [open, setOpen] = useState(false);
  return <details className={styles.full} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Full change reading · evidence and method</summary>
    {open ? <pre>{JSON.stringify(reading, null, 2)}</pre> : null}
  </details>;
}

function RawRow({ row, refName, missing }: { row: RawChange | undefined; refName: string; missing: string }) {
  const [open, setOpen] = useState(false);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Source row · {refName}</summary>
    {open ? <pre>{JSON.stringify(row ?? { ref: refName, status: missing }, null, 2)}</pre> : null}
  </details>;
}

function placeBefore(at: string | null, boundary: string): string {
  if (!at) return 'time unavailable';
  const seconds = Math.floor((Date.parse(boundary) - Date.parse(at)) / 1000);
  if (!Number.isFinite(seconds)) return 'time unavailable';
  return seconds < 0 ? 'after the estimated stop' : seconds < 60 ? 'under a minute before the estimated stop'
    : `${duration(seconds)} before the estimated stop`;
}
