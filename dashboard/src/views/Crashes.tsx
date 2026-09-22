import { ReactNode, useState } from 'react';
import { AddToStack } from '../AddToStack';
import { EventRecord, Reading, observed } from '../api';
import { OutcomeLine, clock } from '../Outcome';
import { Facts, Head, MomentLink, RowList, Section, Segmented, Value, ago, basisOf, byDay, duration, part, size } from '../Sections';
import { useReading } from '../useReading';
import styles from './Crashes.module.css';

/** What a bug check is, wherever the code was found: the record that named it says so. */
interface Bugcheck {
  code: string | null;
  name: string | null;
  parameters: string[];
  source?: string;
  bucket: string | null;
}

/** The dump that belongs to a stop, and how it was matched to it. A file the inventory no longer holds keeps its path and loses its size. */
interface Dump {
  name: string | null;
  path: string | null;
  bytes: number | null;
  modified: string | null;
  matched_by: string;
}

/** The machine's last word before a stop: the last System record written before it started again. */
interface LastRecord {
  RecordId: number | null;
  TimeCreated: string | null;
  ProviderName: string | null;
  Id: number | null;
  LevelDisplayName: string | null;
  Message: string | null;
}

interface Stop {
  started_at: string | null;
  announced_at: string | null;
  stopped_at: string | null;
  /** When Windows Error Reporting filed the report: the one time a stop the System log no longer holds still carries. */
  reported_at: string | null;
  down_seconds: number | null;
  bugcheck: Bugcheck | null;
  no_bugcheck_recorded: boolean;
  power: { sleep_in_progress?: unknown; power_button_timestamp?: unknown; whea_boot_error_count?: unknown; boot_app_status?: unknown; checkpoint?: unknown } | null;
  dump: Dump | null;
  last_record_before: LastRecord | null;
  quiet_seconds: number | null;
  records: { start: number | null; power_41: number | null; eventlog_6008: number | null; wer_1001: number | null; report: number[] };
}

/** One fault decoded: a program that crashed or hung, or one live kernel report folded from its records. */
interface Fault {
  RecordId: number;
  Log?: string;
  kind: string;
  fields: Record<string, unknown>;
  exception?: { code: string | null; name: string | null };
  report?: { id: string | null; code: string | null; name: string | null; parameters: string[]; bucket: string | null; dump_path: string | null; records: number[] };
}

/** One file under the Windows minidump, full dump and live kernel report locations. */
interface DumpFile {
  name: string;
  path: string;
  bytes: number;
  modified: string;
}

interface DumpInspection {
  format: string;
  header_status: string;
  architecture?: string | null;
  bugcheck?: { code: string; name: string | null; parameters: string[] };
  limit: string;
}

const STOP_COUNTS = [5, 20];
const FAULT_COUNTS = [30, 100];

const KIND_WORD: Record<string, string> = {
  'application crash': 'crash',
  'application hang': 'hang',
  'live kernel event': 'kernel',
};

/**
 * Crashes: the stops the machine did not plan, what went wrong while it kept running, and the
 * dumps on disk.
 *
 * Three readings, three outcome lines, because they answer three questions and fail apart: a
 * machine that has not stopped still has faults, and an empty dump inventory says something about
 * the configuration rather than about the stops. The order is the order of the founding question
 * — it froze at 02:14 — so the stops come first and every one of them offers the two things that
 * answer it: the record the machine wrote before it, and the evidence handed to the stack.
 *
 * Nothing here is styled as a verdict. A stop with no bug check reads as a stop with no bug check,
 * which is itself the finding; the bucket WER named is shown as WER's words, not as a cause.
 */
export function Crashes() {
  const [stopCount, setStopCount] = useState(5);
  const [faultCount, setFaultCount] = useState(30);

  const crash = useReading('crash', { count: stopCount });
  const faults = useReading('faults', { count: faultCount });
  const dumps = useReading('dumps');

  const stops = part<Stop[]>(crash.reading, 'stops') ?? [];
  const faultRecords = part<EventRecord[]>(faults.reading, 'records') ?? [];
  const decoded = part<Fault[]>(faults.reading, 'decoded') ?? [];
  const files = part<DumpFile[]>(dumps.reading, 'files') ?? [];
  const times = new Map(faultRecords.map((r) => [r.RecordId, r.TimeCreated]));

  return (
    <section>
      <Head title="Crashes">
        <Segmented value={stopCount} onChange={setStopCount} options={STOP_COUNTS.map((c) => ({ value: c, label: `last ${c}` }))} label="How many stops" />
        {crash.reading ? <AddToStack item={{ kind: 'reading', envelope: crash.reading, title: `Unplanned stops, last ${stopCount}` }} label="Stack this reading" /> : null}
      </Head>
      <OutcomeLine taken={crash} noun="stops" emptyText="No unplanned stop among the starts read" />

      {observed(crash.reading) && stops.length > 0 ? (
        <Section title="Stops" cls="derived" basis={basisOf(crash.reading, 'stops')} note="newest first">
          {byDay(stops, whenOf).map(([label, rows]) => (
            <div key={label}>
              <p className={`${styles.day} label`}>{label}</p>
              <RowList
                items={rows}
                layout={styles.stopRow}
                cells={(s) => (
                  <>
                    <span className={`${styles.time} readout`}>{at(whenOf(s))}</span>
                    <span className={`${styles.down} readout`}>{s.down_seconds == null ? '' : `down ${howLong(s.down_seconds)}`}</span>
                    <span className={styles.check}>
                      {s.bugcheck?.name ?? s.bugcheck?.code ?? <span className={styles.quiet}>{s.no_bugcheck_recorded ? 'no bug check recorded' : 'no bug check named'}</span>}
                      {s.bugcheck?.name && s.bugcheck.code ? <span className={`${styles.code} readout`}>{s.bugcheck.code}</span> : null}
                    </span>
                    <span className={`${styles.dumpName} readout`}>{s.dump?.name ?? ''}</span>
                  </>
                )}
                inspect={(s) => <StopDetail stop={s} envelope={crash.reading} />}
              />
            </div>
          ))}
        </Section>
      ) : null}

      <Section
        title="Programs and the kernel's live reports"
        cls="derived"
        basis={basisOf(faults.reading, 'decoded')}
        controls={
          <>
            <Segmented value={faultCount} onChange={setFaultCount} options={FAULT_COUNTS.map((c) => ({ value: c, label: `last ${c}` }))} label="How many records" />
            {faults.reading ? <AddToStack item={{ kind: 'reading', envelope: faults.reading, title: `Faults, last ${faultCount}` }} /> : null}
          </>
        }
      >
        <OutcomeLine taken={faults} noun="records" emptyText="No application crash, hang or live kernel report in the Application log" />
        {observed(faults.reading) && decoded.length > 0 ? (
          <RowList
            items={decoded}
            idOf={(f) => f.RecordId}
            layout={styles.faultRow}
            cells={(f) => (
              <>
                <span className={`${styles.time} readout`}>{at(times.get(f.RecordId))}</span>
                <span className={`${styles.kind} readout`}>{KIND_WORD[f.kind] ?? f.kind}</span>
                <span className={styles.app}>{appOf(f)}</span>
                <span className={`${styles.module} readout`}>{moduleOf(f)}</span>
                <span className={`${styles.exception} readout`}>{exceptionOf(f)}</span>
              </>
            )}
            inspect={(f) => <FaultDetail fault={f} at={times.get(f.RecordId)} envelope={faults.reading} />}
          />
        ) : null}
      </Section>

      <Section
        title="Dump files"
        cls="raw"
        note={files.length ? `newest first · ${size(files.reduce((n, f) => n + f.bytes, 0))} on disk` : undefined}
        controls={dumps.reading ? <AddToStack item={{ kind: 'reading', envelope: dumps.reading, title: 'Crash dump inventory' }} /> : null}
      >
        <OutcomeLine taken={dumps} noun="dump files" emptyText="No dump files under the Windows dump locations" />
        {observed(dumps.reading) && files.length > 0
          ? byDay(files, (f) => f.modified).map(([label, rows]) => (
              <div key={label}>
                <p className={`${styles.day} label`}>{label}</p>
                <RowList
                  items={rows}
                  layout={styles.fileRow}
                  cells={(f) => (
                    <>
                      <span className={`${styles.fileName} readout`}>{f.name}</span>
                      <span className={`${styles.fileSize} readout`}>{size(f.bytes)}</span>
                      <span className={`${styles.fileWhen} readout`}>
                        {at(f.modified)} · {ago(f.modified)}
                      </span>
                    </>
                  )}
                  inspect={(f) => (
                    <>
                      <Facts
                        rows={[
                          ['Path', <span className={styles.path}>{f.path}</span>],
                          ['Size', `${f.bytes.toLocaleString()} bytes`],
                          ['Written', f.modified],
                        ]}
                      />
                      <DumpHeaderDetail path={f.path} />
                      <div className={styles.actions}>
                        <MomentLink at={f.modified} />
                      </div>
                    </>
                  )}
                />
              </div>
            ))
          : null}
      </Section>
    </section>
  );
}

/**
 * One stop in full: what Windows estimated, what it recorded, the dump it matched and the last
 * thing the machine said. Then the two moves that follow from it — the record before the stop, and
 * the stop itself onto the stack — because from here the question is always one of those two.
 */
function StopDetail({ stop, envelope }: { stop: Stop; envelope: Reading | null }) {
  const moment = stop.started_at ?? stop.announced_at ?? stop.reported_at;
  const ids = recordIds(stop);
  const rows: [string, ReactNode][] = [
    ['Stopped at', stop.stopped_at ? <Value value={`${stop.stopped_at} · ${ago(stop.stopped_at)}`} /> : <Value value={null} />],
    ['Started at', stop.started_at ? <Value value={`${stop.started_at} · ${ago(stop.started_at)}`} /> : <Value value={null} />],
    ['Announced at', <Value value={stop.announced_at} />],
    ['Reported at', <Value value={stop.reported_at} />],
    ['Down for', stop.down_seconds == null ? <Value value={null} /> : <Value value={howLong(stop.down_seconds)} />],
  ];
  if (stop.bugcheck) {
    rows.push(['Bug check', <Value value={[stop.bugcheck.code, stop.bugcheck.name].filter(Boolean).join(' · ')} />]);
    rows.push(['Parameters', <Value value={stop.bugcheck.parameters} />]);
    rows.push(['Named by', <Value value={stop.bugcheck.source ?? null} />]);
    rows.push(['Bucket', <Value value={stop.bugcheck.bucket} />]);
  } else {
    rows.push([
      'Bug check',
      stop.no_bugcheck_recorded ? <span className={styles.quiet}>none recorded: the Kernel-Power 41 carried bug check code 0</span> : <Value value={null} />,
    ]);
  }
  rows.push(['Dump', stop.dump ? <Value value={stop.dump.name} /> : <span className={styles.quiet}>none matched</span>]);
  if (stop.dump) {
    rows.push(['Dump file', <span className={styles.path}>{stop.dump.path}</span>]);
    rows.push(['Dump size', stop.dump.bytes == null ? <span className={styles.quiet}>the report named it; the inventory does not hold it</span> : <Value value={size(stop.dump.bytes)} />]);
    rows.push(['Matched by', <Value value={stop.dump.matched_by} />]);
  }
  if (stop.last_record_before) {
    const last = stop.last_record_before;
    rows.push(['Last record before', <Value value={[last.TimeCreated, last.ProviderName, last.Id, last.LevelDisplayName].filter((v) => v != null).join(' · ')} />]);
    rows.push(['Quiet for', stop.quiet_seconds == null ? <Value value={null} /> : <Value value={howLong(stop.quiet_seconds)} />]);
  }
  // The one field of the 41 that points somewhere else: hardware errors counted at that boot are
  // the Errors view's subject, and a stop that carries them is worth reading there too.
  if (stop.power?.whea_boot_error_count != null) rows.push(['WHEA errors at boot', <Value value={stop.power.whea_boot_error_count} />]);

  return (
    <>
      <Facts rows={rows} />
      {stop.dump?.path && stop.dump.bytes != null ? <DumpHeaderDetail path={stop.dump.path} /> : null}
      {stop.last_record_before?.Message ? <p className={styles.lastWord}>{stop.last_record_before.Message}</p> : null}
      <div className={styles.actions}>
        <MomentLink at={moment} />
        {envelope && ids.length ? (
          <AddToStack item={{ kind: 'selection', envelope, ids, title: `Stop at ${moment ?? stop.stopped_at ?? 'an unknown time'}` }} label="Stack this stop" />
        ) : null}
      </div>
    </>
  );
}

/** Read just the selected file's header when the person opens its detail. */
function DumpHeaderDetail({ path }: { path: string }) {
  const taken = useReading('dump_header', { path });
  const info = part<DumpInspection>(taken.reading, 'inspection');
  const rows: [string, ReactNode][] = info ? [
    ['Format', info.format],
    ['Header', info.header_status],
  ] : [];
  if (info?.architecture) rows.push(['Architecture', info.architecture]);
  if (info?.bugcheck) {
    rows.push(['In file: bug check', [info.bugcheck.code, info.bugcheck.name].filter(Boolean).join(' · ')]);
    rows.push(['In file: parameters', <Value value={info.bugcheck.parameters} />]);
  }
  if (info) rows.push(['Limit', info.limit]);
  return (
    <>
      <p className="label">Inside the dump</p>
      <OutcomeLine taken={taken} noun="dump header" emptyText="This file is no longer in the dump inventory" />
      {observed(taken.reading) && info ? <Facts rows={rows} /> : null}
    </>
  );
}

/** One fault named field by field, in the words of the record it came from, and the moment it happened. */
function FaultDetail({ fault, at: moment, envelope }: { fault: Fault; at?: string; envelope: Reading | null }) {
  const f = fault.fields;
  const rows: [string, ReactNode][] = [];
  if (fault.report) {
    rows.push(['Code', <Value value={[fault.report.code, fault.report.name].filter(Boolean).join(' · ') || null} />]);
    rows.push(['Bucket', <Value value={fault.report.bucket} />]);
    rows.push(['Parameters', <Value value={fault.report.parameters} />]);
    rows.push(['Dump', fault.report.dump_path ? <span className={styles.path}>{fault.report.dump_path}</span> : <Value value={null} />]);
    rows.push(['Report id', <Value value={fault.report.id} />]);
    rows.push(['Written across', <Value value={fault.report.records} />]);
  } else {
    rows.push(['Application', <Value value={text(f.AppName) ?? text(f.ExeFileName)} />]);
    rows.push(['Version', <Value value={text(f.AppVersion)} />]);
    if (fault.kind === 'application crash') {
      rows.push(['Module', <Value value={text(f.ModuleName)} />]);
      rows.push(['Module version', <Value value={text(f.ModuleVersion)} />]);
      rows.push(['Exception', <Value value={[fault.exception?.code, fault.exception?.name].filter(Boolean).join(' · ') || text(f.ExceptionCode)} />]);
      rows.push(['Faulting offset', <Value value={text(f.FaultingOffset)} />]);
      rows.push(['Path', f.AppPath ? <span className={styles.path}>{String(f.AppPath)}</span> : <Value value={null} />]);
      rows.push(['Module path', f.ModulePath ? <span className={styles.path}>{String(f.ModulePath)}</span> : <Value value={null} />]);
    } else {
      rows.push(['Started', <Value value={text(f.StartTime)} />]);
      rows.push(['Terminated', <Value value={text(f.TerminationTime)} />]);
      rows.push(['Hang type', <Value value={text(f.HangType)} />]);
      rows.push(['Report id', <Value value={text(f.ReportId)} />]);
    }
    rows.push(['Process id', <Value value={text(f.ProcessId)} />]);
  }
  rows.push(['Time', <Value value={moment ?? null} />]);
  rows.push(['Record', <Value value={fault.RecordId} />]);

  return (
    <>
      <Facts rows={rows} />
      <div className={styles.actions}>
        <MomentLink at={moment} />
        {envelope ? (
          <AddToStack
            item={{ kind: 'selection', envelope, ids: fault.report?.records ?? [fault.RecordId], title: `${KIND_WORD[fault.kind] ?? fault.kind} at ${moment ?? 'an unknown time'}` }}
            label="Stack this record"
          />
        ) : null}
      </div>
    </>
  );
}

/** The moment a stop belongs to: the start it was announced at, else what is left of it, else when its report was filed. */
function whenOf(stop: Stop): string {
  return stop.started_at ?? stop.announced_at ?? stop.stopped_at ?? stop.reported_at ?? '';
}

/** Every record the stop was composed from, so stacking it hands over the evidence and not the conclusion. */
function recordIds(stop: Stop): number[] {
  const { start, power_41, eventlog_6008, wer_1001, report } = stop.records;
  return [start, power_41, eventlog_6008, wer_1001, ...(report ?? [])].filter((id): id is number => typeof id === 'number');
}

function appOf(fault: Fault): string {
  if (fault.report) return fault.report.name ?? fault.report.code ?? 'live kernel report';
  return text(fault.fields.AppName) ?? text(fault.fields.ExeFileName) ?? 'unnamed';
}

function moduleOf(fault: Fault): string {
  if (fault.report) return fault.report.bucket ?? '';
  return text(fault.fields.ModuleName) ?? '';
}

function exceptionOf(fault: Fault): string {
  if (fault.report) return '';
  return fault.exception?.name ?? fault.exception?.code ?? text(fault.fields.ExceptionCode) ?? '';
}

function text(value: unknown): string | null {
  const s = typeof value === 'string' ? value.trim() : value == null ? '' : String(value);
  return s ? s : null;
}

/** A local time, or a dash where the record carries no moment. */
function at(moment: string | null | undefined): string {
  const t = moment ? new Date(moment) : null;
  return t && !Number.isNaN(t.getTime()) ? clock.format(t) : '—';
}

/** How long something lasted. Under a minute and a half the seconds are the point: a machine down
 *  40 seconds restarted, and "0 min" would lose that. */
function howLong(seconds: number): string {
  return seconds < 90 ? `${seconds} s` : duration(seconds);
}
