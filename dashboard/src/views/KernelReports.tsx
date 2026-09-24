import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { type EventRecord, type Reading, type RecordId } from '../api';
import { Glyph, clock } from '../Outcome';
import { RowList, ago, byDay, part } from '../Sections';
import styles from './Errors.module.css';

export interface KernelReport {
  record_id: RecordId;
  reported_at: string | null;
  header: { severity: string; previous_session: boolean } | null;
  header_error: string | null;
}

interface WindowIdentity {
  RecordId: RecordId;
  cper: { severity: string; previous_session: boolean } | null;
  error: string | null;
}

/** A bounded WHEA window preview has the same exact-record door as a report reference. */
export function reportsFromWindow(reading: Reading | null): KernelReport[] {
  const rows = part<EventRecord[]>(reading, 'records') ?? [];
  const identities = part<WindowIdentity[]>(reading, 'identity') ?? [];
  const byId = new Map(identities.map((identity) => [String(identity.RecordId), identity]));
  return rows.map((row) => {
    const identity = byId.get(String(row.RecordId));
    return { record_id: row.RecordId, reported_at: row.TimeCreated,
      header: identity?.cper ? { severity: identity.cper.severity, previous_session: identity.cper.previous_session } : null,
      header_error: identity?.error ?? null };
  }).sort((a, b) => (b.reported_at ?? '').localeCompare(a.reported_at ?? '') || Number(b.record_id) - Number(a.record_id));
}

export interface ReportRange { from: string; to: string }

export interface ReportSource {
  returned: number;
  limit: number;
  truncated: boolean | null;
  stopped: { kind: string; detail: string } | null;
  log_enabled: boolean | null;
  log_mode?: string | null;
  log_state?: string | null;
  log_oldest?: string | null;
  oldest_state?: string | null;
}

export interface ReportReach {
  covered_from: string | null;
  covered_until?: string | null;
  complete: boolean | null;
}

const PAGE = 50;
const MAX_VISIBLE = 500;

function marker(severity: string | undefined): 'critical' | 'error' | 'warning' | 'info' | null {
  return severity === 'fatal' ? 'critical' : severity === 'recoverable' ? 'error' : severity === 'corrected' ? 'warning' : severity === 'informational' ? 'info' : null;
}

/** The selected report-time previews, with one exact read on demand. */
export function KernelReports({ reading, reports, range, source, reach, inspect }: {
  reading: Reading;
  reports: KernelReport[];
  range: ReportRange | null;
  source: ReportSource | null;
  reach: ReportReach | null;
  inspect: (report: KernelReport) => ReactNode;
}) {
  const rangeKey = range ? `${range.from}/${range.to}` : 'all';
  const [page, setPage] = useState<{ reading: Reading; rangeKey: string; limit: number } | null>(null);
  const [opened, setOpened] = useState<{ reading: Reading; rangeKey: string; id: string | null } | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const focusNew = useRef<string | null>(null);
  const limit = page?.reading === reading && page.rangeKey === rangeKey ? page.limit : PAGE;
  const matching = useMemo(() => {
    if (!range) return reports;
    const from = Date.parse(range.from);
    const to = Date.parse(range.to);
    return reports.filter((report) => {
      const at = report.reported_at ? Date.parse(report.reported_at) : NaN;
      return Number.isFinite(at) && at >= from && at < to;
    });
  }, [reports, range]);
  const visible = matching.slice(0, limit);
  const openId = opened?.reading === reading && opened.rangeKey === rangeKey && visible.some((report) => String(report.record_id) === opened.id) ? opened.id : null;
  const next = Math.min(matching.length, MAX_VISIBLE, limit + PAGE);

  useLayoutEffect(() => {
    const id = focusNew.current;
    focusNew.current = null;
    if (id) list.current?.querySelector<HTMLButtonElement>(`button[data-row-id="${id}"]`)?.focus();
  }, [limit, rangeKey, reading]);

  return (
    <div className={styles.reportList} ref={list}>
      <p className={`${styles.reportListStatus} readout`} aria-live="polite">
        Showing {visible.length} of {matching.length} returned report{matching.length === 1 ? '' : 's'}{range ? ' written in this selected stretch' : ''}. Open a row to inspect its exact record here.
      </p>
      {range && !matching.length ? <p className={`${styles.notDecoded} readout`}>No returned report has a readable time in this stretch. This does not establish that the channel was quiet.</p> : null}
      {byDay(visible, (report) => report.reported_at ?? '').map(([day, rows]) => (
        <div key={`${day}:${rows[0].record_id}`}>
          <p className={`${styles.reportDay} label`}>{day === 'undated' ? 'Report time unavailable' : day}</p>
          <RowList
            items={rows}
            idOf={(report) => String(report.record_id)}
            openId={openId}
            onOpenChange={(id) => setOpened({ reading, rangeKey, id: id === null ? null : String(id) })}
            canInspect={(report) => report.reported_at !== null && Number.isFinite(Date.parse(report.reported_at))}
            layout={styles.reportRow}
            cells={(report) => {
              const at = report.reported_at ? new Date(report.reported_at) : null;
              const known = at && !Number.isNaN(at.getTime());
              const kind = marker(report.header?.severity);
              return <>
                <span className={`${styles.reportTime} readout`}>{known ? <><span>{clock.format(at)}</span><span>{ago(report.reported_at!)}</span></> : 'time unavailable'}</span>
                <span className={styles.reportSeverity}>{kind ? <Glyph kind={kind} /> : <span aria-hidden="true">—</span>} <span>{report.header?.severity ?? 'header unreadable'}</span></span>
                <span className={`${styles.reportId} readout`}>#{report.record_id}</span>
                <span className={styles.reportMeaning}>{report.header?.previous_session ? 'Reported after restart · error from an earlier session' : report.header ? 'Windows report · occurrence time not established' : 'Header unreadable · session unknown'}{!known ? ' · exact read unavailable without a report time' : ''}</span>
              </>;
            }}
            inspect={inspect}
          />
        </div>
      ))}
      {limit < Math.min(matching.length, MAX_VISIBLE) ? (
        <button className={styles.exactRaw} type="button" onClick={() => {
          const firstNew = matching.slice(limit, next).find((report) => report.reported_at && Number.isFinite(Date.parse(report.reported_at)));
          focusNew.current = firstNew ? String(firstNew.record_id) : null;
          setPage({ reading, rangeKey, limit: next });
        }}>Show {next - limit} older returned reports</button>
      ) : null}
      {matching.length > MAX_VISIBLE && visible.length >= MAX_VISIBLE ? <p className={`${styles.reportListStatus} readout`}>This view shows at most {MAX_VISIBLE} returned reports. Select a time stretch or a shorter window to inspect another part; the full returned reading is available in the API and Stack.</p> : null}
      {source?.truncated ? <p className={`${styles.reportListStatus} readout`}>The query reached its {source.limit.toLocaleString()}-report limit. Older reports in this window were not returned.</p> : null}
      {source?.stopped ? <p className={`${styles.reportListStatus} readout`}>The query stopped after {source.returned.toLocaleString()} returned reports: {source.stopped.detail}</p> : null}
      {source?.log_enabled === false ? <p className={`${styles.reportListStatus} readout`}>This Windows channel is disabled; new reports are not being recorded there.</p> : null}
      {reach?.complete !== true ? <p className={`${styles.reportListStatus} readout`}>The requested time span is not fully established{reach?.covered_from ? `; observed channel reach begins at ${new Date(reach.covered_from).toLocaleString()}` : ''}{reach?.covered_until ? ` and ends at ${new Date(reach.covered_until).toLocaleString()}` : ''}.</p> : null}
    </div>
  );
}
