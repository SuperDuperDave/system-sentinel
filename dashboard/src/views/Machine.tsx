import { useState } from 'react';
import { Reading, observed } from '../api';
import { AddEvidence } from '../AddEvidence';
import { OutcomeLine } from '../Outcome';
import { Facts, Head, RowList, Section, Tree, Value, basisOf, byDay, duration, part } from '../Sections';
import { useReading } from '../useReading';
import { Fingerprint, MachineOverview, Snapshot } from './MachineOverview';
import styles from './Machine.module.css';

interface Risk {
  id: string;
  observation: string;
  domain: string;
}

interface Driver {
  device_name: string;
  device_class: string | null;
  driver_version: string | null;
  driver_provider: string | null;
  driver_date: string | null;
  inf_name: string | null;
}

/** The heavy readings, in the order the machine is usually read: what computes, what draws, what it all sits on. */
const PARTS = [
  { id: 'cpu', reading: 'hardware.cpu', title: 'Processor', noun: 'the processor and platform detail', hint: 'Identity and topology, the cache inventory, and whether virtualization-based security and Hyper-V are running.' },
  { id: 'gpu', reading: 'hardware.gpu', title: 'Graphics', noun: 'the display adapters', hint: 'Display adapters, video memory, the driver and its age, and the timeout detection and recovery settings a freeze investigation asks about.' },
  { id: 'board', reading: 'hardware.board', title: 'Board and firmware', noun: 'the board and firmware', hint: 'The board and its BIOS, the TPM state, the firmware type, and whether Secure Boot is on.' },
  { id: 'storage', reading: 'hardware.storage', title: 'Storage', noun: 'the disks and volumes', hint: 'Physical disks with whatever reliability counters the drive exposes, and the volumes carried on each.' },
  { id: 'network', reading: 'hardware.network', title: 'Network', noun: 'the network adapters', hint: 'Adapters, link and media state, addresses and DNS, the driver and its age, and whether a device is disabled.' },
];

const JUMPS = [
  { id: 'snapshot', label: 'Snapshot' },
  { id: 'fingerprint', label: 'Fingerprint' },
  { id: 'configuration', label: 'Configuration' },
  { id: 'observations', label: 'Observations' },
  ...PARTS.map((p) => ({ id: p.id, label: p.title })),
  { id: 'drivers', label: 'Drivers' },
];

/**
 * The machine: one scrolling view from what it is doing now, through what it is made of, to what
 * changed under it. Three readings arrive with the view — the snapshot, the fingerprint with its
 * configuration and observations, and the recent driver dates — and the five heavy ones are not
 * taken until their section is opened, because each spends seconds on the host and most visits
 * want none of them.
 *
 * A serial number arrives as a placeholder. The field stays where it is: that a serial exists is
 * evidence, and hiding the field would make a machine that has one look like a machine that does
 * not. The strip at the top is the whole sequence in one glance, so a phone can reach the end of
 * the page without scrolling through it.
 */
export function Machine() {
  const system = useReading('system');
  const hardware = useReading('hardware');
  const drivers = useReading('drivers', { count: 30 });

  const snapshot = part<Snapshot>(system.reading, 'snapshot');
  const fingerprint = part<Fingerprint>(hardware.reading, 'fingerprint');
  const config = part<Record<string, unknown>>(hardware.reading, 'config');
  const risks = part<Risk[]>(hardware.reading, 'risks');
  const driverRows = part<Driver[]>(drivers.reading, 'drivers') ?? [];

  return (
    <section>
      <Head title="Machine" />
      <nav className={styles.strip} aria-label="Sections of this view">
        {JUMPS.map((j) => (
          <a key={j.id} className={`${styles.jump} readout`} href={`#${j.id}`}>
            {j.label}
          </a>
        ))}
      </nav>

      <MachineOverview system={system} hardware={hardware} />

      <h2 className={`${styles.part} display`} id="snapshot">
        Snapshot
      </h2>
      <OutcomeLine taken={system} noun="the snapshot" emptyText="Windows returned no snapshot" />
      {observed(system.reading) && snapshot ? (
        <Section
          title="What the machine is doing now"
          cls="raw"
          controls={system.reading ? <AddEvidence item={{ kind: 'reading', envelope: system.reading, title: 'System snapshot' }} /> : null}
        >
          <Facts
            rows={[
              ['Windows', <Value value={snapshot.os_caption} />],
              ['Version', <Value value={snapshot.os_version ? `${snapshot.os_version} · build ${snapshot.os_build ?? '?'}` : null} />],
              ['Architecture', <Value value={snapshot.architecture} />],
              ['Last started', <Value value={snapshot.boot_time} />],
              ['Up for', snapshot.uptime_seconds == null ? <Value value={null} /> : <Value value={duration(snapshot.uptime_seconds)} />],
              ['Processor load', <Value value={snapshot.processor_load_percent == null ? null : `${snapshot.processor_load_percent} %`} />],
              ['Memory', <Value value={gb(snapshot.memory_total_kb)} />],
              ['Memory free', <Value value={gb(snapshot.memory_free_kb)} />],
            ]}
          />
          <Everything value={snapshot} cls="raw" />
        </Section>
      ) : null}

      <h2 className={`${styles.part} display`} id="fingerprint">
        Fingerprint and configuration
      </h2>
      <OutcomeLine taken={hardware} noun="the fingerprint and configuration" emptyText="Windows returned no hardware detail" />
      {observed(hardware.reading) ? (
        <>
          <Section
            title="The parts this machine is made of"
            cls="derived"
            basis={basisOf(hardware.reading, 'fingerprint')}
            note="selected parts and versions at this reading"
            controls={hardware.reading ? <AddEvidence item={{ kind: 'reading', envelope: hardware.reading, title: 'Hardware fingerprint and configuration' }} /> : null}
          >
            {fingerprint ? (
              <Facts
                rows={[
                  ['Processor', <Value value={join([fingerprint.cpu?.name, cores(fingerprint.cpu)])} />],
                  ['Graphics', <Value value={join([fingerprint.gpu?.name, fingerprint.gpu?.driver_version && `driver ${fingerprint.gpu.driver_version}`, fingerprint.gpu?.date])} />],
                  ['Board', <Value value={join([fingerprint.board?.manufacturer, fingerprint.board?.product])} />],
                  ['Firmware', <Value value={join([fingerprint.board?.bios_version, fingerprint.board?.bios_date])} />],
                  ['Disk 0', <Value value={join([fingerprint.storage?.disk0_model, fingerprint.storage?.size_gb && `${fingerprint.storage.size_gb} GB`, fingerprint.storage?.interface])} />],
                ]}
              />
            ) : null}
            {fingerprint ? <Everything value={fingerprint} cls="derived" /> : null}
          </Section>

          <div id="configuration" className={styles.anchor} />
          <Section title="Configuration" cls="raw" note="the settings that shape how the machine behaves">
            {config ? <Tree value={config} /> : null}
          </Section>

          <div id="observations" className={styles.anchor} />
          <Section
            title="Observations"
            cls="inferred"
            basis={basisOf(hardware.reading, 'risks')}
            note="what those settings support saying; a lead to look at, not a diagnosis"
          >
            {risks && risks.length ? (
              <ul className={styles.leads}>
                {risks.map((r) => (
                  <li key={r.id} className={styles.lead}>
                    <span className={`${styles.leadDomain} label`}>{r.domain}</span>
                    <span className={styles.leadText}>{r.observation}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={`${styles.none} readout`}>None of the rules matched this configuration.</p>
            )}
          </Section>
        </>
      ) : null}

      {PARTS.map((p) => (
        <Part key={p.id} {...p} />
      ))}

      <h2 className={`${styles.part} display`} id="drivers">
        Current signed drivers
      </h2>
      <OutcomeLine taken={drivers} noun="drivers" singular="driver" emptyText="Windows listed no signed drivers" />
      {observed(drivers.reading) && driverRows.length ? (
        <>
          <Section
            title="Most recently dated first"
            cls="raw"
            note="This is each driver's authored date, not when Windows installed it on this machine."
            controls={drivers.reading ? <AddEvidence item={{ kind: 'reading', envelope: drivers.reading, title: 'Current signed drivers' }} /> : null}
          />
          {byDay(driverRows, (d) => local(d.driver_date)).map(([label, rows]) => (
            <div key={label}>
              <p className={`${styles.day} label`}>{label}</p>
              <RowList
                items={rows}
                snapshot={driverRows}
                layout={styles.driverRow}
                cells={(d) => (
                  <>
                    <span className={styles.deviceName}>{d.device_name}</span>
                    <span className={`${styles.deviceClass} readout`}>{d.device_class}</span>
                    <span className={`${styles.driverVersion} readout`}>{d.driver_version}</span>
                  </>
                )}
                inspect={(d) => (
                  <Facts
                    rows={[
                      ['Device', <Value value={d.device_name} />],
                      ['Class', <Value value={d.device_class} />],
                      ['Version', <Value value={d.driver_version} />],
                      ['Provider', <Value value={d.driver_provider} />],
                      ['Dated', <Value value={d.driver_date} />],
                      ['INF', <Value value={d.inf_name} />],
                    ]}
                  />
                )}
              />
            </div>
          ))}
        </>
      ) : null}
    </section>
  );
}

/**
 * One heavy reading, behind its own header. Nothing is taken until the section is opened, and
 * opening it again takes it again: a reading is a moment, and the outcome line says which moment.
 */
function Part({ id, reading, title, noun, hint }: { id: string; reading: string; title: string; noun: string; hint: string }) {
  const [open, setOpen] = useState(false);
  const taken = useReading(reading, {}, open);
  return (
    <>
      <div className={styles.partHead} id={id}>
        <button className={styles.partButton} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <span className={styles.chevron} aria-hidden="true">
            ›
          </span>
          <span className={`${styles.partTitle} display`}>{title}</span>
          <span className={`${styles.take} label`}>{open ? 'Hide' : 'Take'}</span>
        </button>
      </div>
      {open ? (
        <>
          <OutcomeLine taken={taken} noun={noun} emptyText={`Windows returned nothing for ${noun}`} />
          {observed(taken.reading) ? <Payload reading={taken.reading} title={title} /> : null}
        </>
      ) : (
        <p className={styles.hint}>{hint}</p>
      )}
    </>
  );
}

/** Every section of a reading in the envelope's own order, each under its class. */
function Payload({ reading, title }: { reading: Reading | null; title: string }) {
  if (!reading) return null;
  return (
    <>
      {reading.sections.map((s, i) => (
        <Section
          key={s.name}
          title={s.name === s.class ? undefined : s.name}
          cls={s.class}
          basis={s.basis}
          controls={i === 0 ? <AddEvidence item={{ kind: 'reading', envelope: reading, title }} /> : null}
        >
          <Tree value={s.data} />
        </Section>
      ))}
    </>
  );
}

/** The shaped values above are a selection; this is the section as the reading returned it. */
function Everything({ value, cls }: { value: unknown; cls: string }) {
  return (
    <details className={styles.everything}>
      <summary className="label">Every field · {cls}</summary>
      <div className={styles.everythingBody}>
        <Tree value={value} />
      </div>
    </details>
  );
}

const gb = (kb: number | null | undefined): string | null => (kb == null ? null : `${(kb / 1024 / 1024).toFixed(2)} GB`);

const cores = (cpu: Fingerprint['cpu']): string | null =>
  cpu?.cores ? `${cpu.cores} cores${cpu.logical ? ` / ${cpu.logical} threads` : ''}` : null;

/** Join the parts of a composed value, dropping what the machine did not report. */
function join(parts: (string | number | null | undefined | false)[]): string | null {
  const kept = parts.filter((p): p is string | number => p !== null && p !== undefined && p !== false && String(p).trim() !== '');
  return kept.length ? kept.map((p) => String(p).trim()).join(' · ') : null;
}

/** A date-only field is the machine's local day; parsing it as UTC would move it. */
function local(stamp: string | null): string {
  if (!stamp) return '';
  return stamp.length === 10 ? `${stamp}T00:00:00` : stamp;
}
