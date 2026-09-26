import { useApp } from '../store';
import { TrackId, useTracks } from '../timeline';
import { Chart } from '../chart/Chart';
import { DayList } from '../chart/DayList';
import { Inspector } from '../chart/Inspector';
import { AxisPage, usePhone } from '../chart/Page';
import { summary } from '../chart/words';
import { Summary } from '../chart/Summary';
import { StopFacts } from './Stops';

const TRACKS: TrackId[] = ['stops', 'hardware', 'kernel', 'faults', 'log', 'changes', 'performance', 'reliability'];

/**
 * Home: what happened, when, and what was read around it. Every source the machine keeps, on one
 * axis, with a few sentences above it for a person who does not read charts, and the same marks
 * day by day below it for a phone and a screen reader.
 */
export function Timeline() {
  const range = useApp((s) => s.range);
  const phone = usePhone();
  const tracks = useTracks(range, TRACKS);
  return (
    <AxisPage
      title="Timeline"
      question="What happened on this computer, when, and what was read around it."
      summary={<Summary lines={summary(tracks, range)} />}
      chart={phone ? null : <Chart range={range} tracks={tracks} label="Every source on one time axis" />}
      inspector={<Inspector tracks={tracks} range={range} sheet={phone} detail={(mark) => mark.stop ? <StopFacts stop={mark.stop} /> : null} />}
      list={<DayList tracks={tracks} range={range} unreadNote={false} />}
    />
  );
}
