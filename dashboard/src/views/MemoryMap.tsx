import styles from './MemoryMap.module.css';

interface Module {
  locator: string;
  capacity_gb: number | null;
  rated_mhz: number | null;
  configured_mhz: number | null;
  error_correction: boolean | null;
}

export interface MemorySummary {
  installed_gb: number | null;
  slots_used: number;
  slots_total: number | null;
  slots_free: number | null;
  modules: Module[];
}

/** A visual entry into the derived memory ledger; exact raw and derived fields stay below. */
export function MemoryMap({ data }: { data: MemorySummary }) {
  const { modules, slots_total: total, slots_used: used } = data;
  const occupancy = total != null && total > 0 && used > 0 ? Math.min(100, (used / total) * 100) : null;

  return (
    <section className={styles.map} aria-labelledby="memory-map-title">
      <div className={styles.head}>
        <div>
          <p className="label">Memory map · computed from Windows inventory</p>
          <h3 id="memory-map-title" className="display">Modules and reported slots</h3>
        </div>
        <p>Each card is a module Windows returned. The slot bar counts modules; it does not show their position on the board.</p>
      </div>
      <div className={styles.summary}>
        <div className={styles.capacity}>
          <strong>{data.installed_gb == null ? 'Unknown' : `${data.installed_gb} GB`}</strong>
          <span className="readout">installed capacity</span>
        </div>
        <div className={styles.population}>
          <div className={`${styles.populationLine} readout`}><span>{used} {used === 1 ? 'module' : 'modules'} returned</span><span>{total == null ? 'Total slots unknown' : `${total} slots reported`}</span></div>
          <div className={styles.track} role={occupancy == null ? undefined : 'meter'} aria-label={occupancy == null ? undefined : 'Reported slot population'} aria-valuemin={occupancy == null ? undefined : 0} aria-valuemax={occupancy == null ? undefined : total ?? undefined} aria-valuenow={occupancy == null ? undefined : used} aria-valuetext={occupancy == null ? undefined : `${used} of ${total} reported slots have returned modules`}>
            {occupancy == null ? null : <span style={{ width: `${occupancy}%` }} />}
          </div>
          <p className={`${styles.free} readout`}>{data.slots_free == null ? 'Free slot count not established' : `${data.slots_free} ${data.slots_free === 1 ? 'slot' : 'slots'} not populated in this inventory`}</p>
        </div>
      </div>
      {modules.length ? (
        <div className={styles.modules}>
          {modules.map((module, index) => {
            const rated = module.rated_mhz;
            const configured = module.configured_mhz;
            const speedShare = rated && configured && rated > 0 && configured > 0 ? Math.min(100, (configured / rated) * 100) : null;
            return (
              <article className={styles.module} key={`${module.locator}-${index}`}>
                <div className={`${styles.moduleTop} readout`}><span>Module {String(index + 1).padStart(2, '0')}</span><span>{module.locator}</span></div>
                <strong className={styles.moduleCapacity}>{module.capacity_gb == null ? 'Capacity unknown' : `${module.capacity_gb} GB`}</strong>
                <div className={`${styles.speedLine} readout`}><span>Configured speed</span><span>{configured == null ? 'Not reported' : `${configured} MHz`}</span></div>
                <div className={styles.speedTrack} role={speedShare == null ? undefined : 'meter'} aria-label={speedShare == null ? undefined : `${module.locator} configured speed against module rating`} aria-valuemin={speedShare == null ? undefined : 0} aria-valuemax={speedShare == null ? undefined : 100} aria-valuenow={speedShare == null ? undefined : Math.round(speedShare)} aria-valuetext={speedShare == null ? undefined : `${configured} of ${rated} MHz`}>
                  {speedShare == null ? null : <span style={{ width: `${speedShare}%` }} />}
                </div>
                <p className={`${styles.moduleFoot} readout`}>Rated {rated == null ? 'unknown' : `${rated} MHz`}{rated && configured && configured < rated ? ' · below module rating' : ''}{module.error_correction === true ? ' · ECC width reported' : ''}</p>
              </article>
            );
          })}
        </div>
      ) : <p className={styles.noModules}>No memory module inventory was returned.</p>}
      {modules.length ? <p className={`${styles.speedNote} readout`}>Speed bars compare configured and rated clocks. They do not measure performance or health.</p> : null}
      <div className={`${styles.links} readout`}><a href="#diagnostic-memory-derived">Derived fields ↗</a><a href="#diagnostic-memory-raw">Windows fields ↗</a></div>
    </section>
  );
}
