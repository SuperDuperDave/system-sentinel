import styles from './PcieMap.module.css';

interface Endpoint {
  name: string;
  instance_id: string;
  class: string;
  status: string;
  problem: string;
  address: { address?: string } | null;
}

export interface PcieGroup {
  kind: 'root_port' | 'non_pci_parent';
  upstream: { instance_id: string; name: string };
  members: Endpoint[];
}

export interface PcieCoverage {
  relations: 'complete' | 'partial' | 'none';
  returned_devices: number;
  placed_devices: number;
  unplaced: { instance_id: string; reason: string }[];
}

const reportsProblem = (member: Endpoint) => (!!member.status && member.status !== 'OK') || (!!member.problem && member.problem !== 'CM_PROB_NONE');
const stateText = (member: Endpoint) => reportsProblem(member) ? `${member.status || 'State unknown'} · ${member.problem || 'No problem code'}` : member.status === 'OK' ? 'OK' : 'State not reported';
const coverageText = (coverage: PcieCoverage | null): string => {
  if (!coverage) return 'Parent relation coverage was not reported.';
  if (coverage.returned_devices === 0) return 'Windows returned no present PCI devices, so no parent chains are needed.';
  if (coverage.relations === 'complete') return 'Windows reported a parent chain for every returned PCI device.';
  if (coverage.relations === 'none') return 'Windows did not provide enough parent relationships to draw upstream groups.';
  if (coverage.unplaced.length === 0) return 'The relation source did not finish cleanly. Returned devices have reported chains, but source completeness is uncertain.';
  return `Parent relation coverage is partial. ${coverage.unplaced.length} returned PCI devices could not be placed; reported groups may be incomplete.`;
};

/** Groups backed by reported parent chains; a non-PCI parent does not imply a shared PCIe link. */
export function PcieMap({ groups, coverage }: { groups: PcieGroup[] | null; coverage: PcieCoverage | null }) {
  const placedGroups = groups ?? [];
  const peak = Math.max(1, ...placedGroups.map((group) => group.members.length));
  const nonOk = placedGroups.reduce((count, group) => count + group.members.filter(reportsProblem).length, 0);

  return (
    <div className={styles.fabric}>
      <div className={styles.summary}>
        <div><strong>{coverage?.returned_devices ?? '—'}</strong><span className="readout">PCI devices returned</span></div>
        <div><strong>{coverage?.placed_devices ?? '—'}</strong><span className="readout">devices with complete parent chains</span></div>
        <div><strong>{groups === null ? '—' : placedGroups.length}</strong><span className="readout">reported upstream groups</span></div>
      </div>
      <p className={styles.explain}>
        {coverageText(coverage)}
        {' '}Only root-port groups establish a shared PCIe link. Bar length counts placed members, not link speed or fault likelihood.
        {groups !== null && nonOk > 0 ? ` ${nonOk} placed ${nonOk === 1 ? 'member has' : 'members have'} a non-OK state or problem code.` : ''}
      </p>
      {placedGroups.length ? (
        <ol className={styles.groups}>
          {placedGroups.map((group, index) => (
            <li key={`${group.upstream.instance_id}-${index}`} className={styles.group}>
              <details>
                <summary className={styles.root}>
                  <span className={`${styles.number} readout`}>{String(index + 1).padStart(2, '0')}</span>
                  <span className={styles.rootName}>{group.upstream.name || 'Upstream parent not named'} <small>({group.kind === 'root_port' ? 'root port' : 'non-PCI parent'})</small></span>
                  <span className={styles.count}>{group.members.length} {group.members.length === 1 ? 'member' : 'members'}</span>
                  <span className={styles.bar} aria-hidden="true"><span style={{ width: `${(group.members.length / peak) * 100}%` }} /></span>
                </summary>
                <p className={`${styles.rootId} readout`}>Upstream ID · {group.upstream.instance_id || 'not reported'}</p>
                <ul className={styles.members}>
                  {group.members.map((member, memberIndex) => (
                    <li key={`${member.instance_id}-${memberIndex}`} className={styles.member}>
                      <span className={styles.memberName}>{member.name || 'Unnamed endpoint'}</span>
                      <span className={`${styles.memberClass} readout`}>{member.class || 'Class not reported'}{member.address?.address ? ` · ${member.address.address}` : ''}</span>
                      <span className={`${styles.memberState} readout`}>{stateText(member)}</span>
                      <span className={`${styles.instance} readout`}>{member.instance_id}</span>
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          ))}
        </ol>
      ) : <p className={styles.empty}>{groups === null ? 'Upstream groups are unknown. The raw PCI device inventory remains available below.' : 'No groups could be formed from the returned parent chains.'}</p>}
      <div className={`${styles.links} readout`}><a href="#diagnostic-pcie-devices">Windows PCI devices ↗</a><a href="#diagnostic-pcie-coverage">Relation coverage ↗</a></div>
    </div>
  );
}
