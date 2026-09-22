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
  root_port: { instance_id: string; name: string };
  members: Endpoint[];
}

const reportsProblem = (member: Endpoint) => (!!member.status && member.status !== 'OK') || (!!member.problem && member.problem !== 'CM_PROB_NONE');
const stateText = (member: Endpoint) => reportsProblem(member) ? `${member.status || 'State unknown'} · ${member.problem || 'No problem code'}` : member.status === 'OK' ? 'OK' : 'State not reported';

/** Windows' returned parent groups, organized by shared link rather than physical board position. */
export function PcieMap({ groups }: { groups: PcieGroup[] }) {
  const endpoints = groups.reduce((count, group) => count + group.members.length, 0);
  const peak = Math.max(1, ...groups.map((group) => group.members.length));
  const nonOk = groups.reduce((count, group) => count + group.members.filter(reportsProblem).length, 0);

  return (
    <div className={styles.fabric}>
      <div className={styles.summary}>
        <div><strong>{groups.length}</strong><span className="readout">upstream groups</span></div>
        <div><strong>{endpoints}</strong><span className="readout">endpoints returned</span></div>
        <div><strong>{nonOk}</strong><span className="readout">endpoints with non-OK state or problem code</span></div>
      </div>
      <p className={styles.explain}>Each line joins endpoints Windows placed under one upstream parent. Its length counts returned endpoints, not link speed or fault likelihood. Open a group to inspect its devices.</p>
      {groups.length ? (
        <ol className={styles.groups}>
          {groups.map((group, index) => (
            <li key={`${group.root_port.instance_id}-${index}`} className={styles.group}>
              <details>
                <summary className={styles.root}>
                  <span className={`${styles.number} readout`}>{String(index + 1).padStart(2, '0')}</span>
                  <span className={styles.rootName}>{group.root_port.name || 'Upstream parent not named'}</span>
                  <span className={styles.count}>{group.members.length} {group.members.length === 1 ? 'endpoint' : 'endpoints'}</span>
                  <span className={styles.bar} aria-hidden="true"><span style={{ width: `${(group.members.length / peak) * 100}%` }} /></span>
                </summary>
                <p className={`${styles.rootId} readout`}>Upstream ID · {group.root_port.instance_id || 'not reported'}</p>
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
      ) : <p className={styles.empty}>No upstream groups were returned. Check the reading outcome and warnings above for what Windows answered.</p>}
      <div className={`${styles.links} readout`}><a href="#diagnostic-pcie-endpoints">Windows endpoints ↗</a><a href="#diagnostic-pcie-roots">Windows bridges ↗</a></div>
    </div>
  );
}
