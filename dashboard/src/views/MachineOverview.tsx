import { observed } from '../api';
import { clock } from '../Outcome';
import { part } from '../Sections';
import type { Taken } from '../useReading';
import styles from './MachineOverview.module.css';

export interface Snapshot {
  os_caption: string | null;
  os_version: string | null;
  os_build: string | null;
  architecture: string | null;
  boot_time: string | null;
  uptime_seconds: number | null;
  processor_load_percent: number | null;
  memory_total_kb: number | null;
  memory_free_kb: number | null;
}

export interface Fingerprint {
  cpu: { name?: string; cores?: number; logical?: number; manufacturer?: string; description?: string } | null;
  gpu: { name?: string; driver_version?: string; vram_mb?: number; date?: string } | null;
  board: { product?: string; manufacturer?: string; version?: string; bios_version?: string; bios_date?: string } | null;
  storage: { disk0_model?: string; size_gb?: number; media_type?: string; interface?: string } | null;
}

/** A visual index over the two readings already taken by Machine; no extra host query. */
export function MachineOverview({ system, hardware }: { system: Taken<unknown>; hardware: Taken<unknown> }) {
  const snapshot = part<Snapshot>(system.reading, 'snapshot');
  const fingerprint = part<Fingerprint>(hardware.reading, 'fingerprint');
  const hasHardware = observed(hardware.reading);
  const hasSystem = observed(system.reading);
  const parts = [
    { id: 'cpu', index: '01', label: 'Processor', value: fingerprint?.cpu?.name, detail: countCores(fingerprint?.cpu) },
    { id: 'gpu', index: '02', label: 'Graphics', value: fingerprint?.gpu?.name, detail: fingerprint?.gpu?.driver_version ? `Driver ${fingerprint.gpu.driver_version}` : null },
    { id: 'board', index: '03', label: 'Board and firmware', value: join(fingerprint?.board?.manufacturer, fingerprint?.board?.product), detail: fingerprint?.board?.bios_version ? `BIOS ${fingerprint.board.bios_version}` : null },
    { id: 'storage', index: '04', label: 'Disk 0', value: fingerprint?.storage?.disk0_model, detail: fingerprint?.storage?.size_gb == null ? null : `${fingerprint.storage.size_gb} GB` },
  ];

  const load = percentage(snapshot?.processor_load_percent);
  const free = snapshot?.memory_free_kb;
  const total = snapshot?.memory_total_kb;
  const freeShare = free != null && total != null && total > 0 && free >= 0 && free <= total ? (free / total) * 100 : null;
  const readAt = system.reading && hasSystem ? clock.format(new Date(system.reading.asked_at)) : null;

  return (
    <section className={styles.overview} aria-labelledby="machine-overview-title">
      <div className={styles.heading}>
        <div>
          <p className={`${styles.eyebrow} label`}>Machine map</p>
          <h2 id="machine-overview-title" className={`${styles.title} display`}>What Windows can see</h2>
        </div>
        <p className={styles.intro}>A selected inventory and one live snapshot. Choose a part to inspect its full reading.</p>
      </div>

      <div className={styles.surface}>
        <div className={`${styles.surfaceTop} readout`}>
          <span>Hardware fingerprint · derived</span>
          <span>{hardware.state === 'taking' && !hardware.reading ? 'Taking reading…' : fingerprint ? 'Current selection' : hasHardware ? 'No inventory returned' : 'Not observed'}</span>
        </div>
        <div className={styles.grid}>
          {parts.map((item) => (
            <a className={styles.card} href={`#${item.id}`} key={item.id}>
              <span className={`${styles.cardTop} readout`}><span>{item.index} / {item.label}</span><span aria-hidden="true">↗</span></span>
              <span className={styles.cardValue}>{hasHardware ? item.value || 'Not reported' : hardware.state === 'taking' && !hardware.reading ? 'Taking reading…' : 'Not observed'}</span>
              <span className={`${styles.cardDetail} readout`}>{hasHardware ? item.detail || 'Open the full reading' : 'Open the full reading'}</span>
            </a>
          ))}
        </div>
        <p className={`${styles.selectionRule} readout`}>Selection: first processor and board · PCI display with most reported memory, or first listed · Windows disk index 0</p>
      </div>

      <div className={styles.snapshot}>
        <div className={`${styles.snapshotTop} readout`}><span>Snapshot · Windows readings</span><span>{readAt ? `Taken ${readAt}` : system.state === 'taking' && !system.reading ? 'Taking reading…' : 'Not observed'}</span></div>
        <Meter label="Processor load" value={hasSystem ? load : null} display={hasSystem ? snapshot?.processor_load_percent == null ? 'Not reported' : `${snapshot.processor_load_percent} %` : 'Not observed'} note="At the reading" />
        <Meter label="Physical memory free" value={hasSystem ? freeShare : null} display={hasSystem ? free == null || total == null ? 'Not reported' : `${gb(free)} of ${gb(total)}` : 'Not observed'} note="Free / total" />
      </div>
    </section>
  );
}

function Meter({ label, value, display, note }: { label: string; value: number | null; display: string; note: string }) {
  return (
    <div className={styles.meter}>
      <div className={styles.meterLine}><span>{label}</span><strong className="readout">{display}</strong></div>
      <div className={styles.meterTrack} role={value == null ? undefined : 'meter'} aria-label={value == null ? undefined : label} aria-valuemin={value == null ? undefined : 0} aria-valuemax={value == null ? undefined : 100} aria-valuenow={value == null ? undefined : Math.round(value)} aria-valuetext={value == null ? undefined : display}>
        {value != null ? <span style={{ width: `${value}%` }} /> : null}
      </div>
      <p className={`${styles.meterNote} readout`}>{value == null ? 'No percentage to plot' : note}</p>
    </div>
  );
}

function percentage(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}

function countCores(cpu: Fingerprint['cpu'] | undefined): string | null {
  if (!cpu?.cores) return null;
  return `${cpu.cores} cores${cpu.logical ? ` · ${cpu.logical} logical processors` : ''}`;
}

function join(...parts: (string | undefined)[]): string | null {
  return parts.filter(Boolean).join(' · ') || null;
}

function gb(kb: number): string {
  return `${(kb / 1024 / 1024).toFixed(1)} GB`;
}
