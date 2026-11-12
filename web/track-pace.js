/* Compare local passage durations at matching circuit progress, not lap lead. */
'use strict';
window.ACCCTrackPace = (() => {
  const epsilon = 1e-9;
  const valid = point => point && Number.isFinite(point.progress) &&
    point.progress >= 0 && point.progress <= 1 && Number.isFinite(point.time);

  function passageRuns(passages) {
    const runs = [];
    let run = null;
    for (const point of Array.isArray(passages) ? passages : []) {
      if (!valid(point)) {run = null; continue;}
      const previous = run?.points.at(-1);
      // Exact duplicate observations add no passage or ambiguity. A stop at
      // the same progress with a different timestamp still splits the run.
      if (previous && point.segment === previous.segment &&
          point.progress === previous.progress && point.time === previous.time) continue;
      if (!previous || point.segment !== previous.segment ||
          point.progress <= previous.progress || point.time <= previous.time) {
        run = {points: [], start: point.progress, end: point.progress};
        runs.push(run);
      }
      run.points.push({progress: point.progress, time: point.time, segment: point.segment});
      run.end = point.progress;
    }
    return runs;
  }

  function timeAt(run, progress) {
    const points = run.points;
    if (progress < run.start - epsilon || progress > run.end + epsilon) return null;
    if (progress <= run.start) return points[0].time;
    if (progress >= run.end) return points.at(-1).time;
    let lo = 0, hi = points.length - 1;
    while (lo < hi) {
      const middle = Math.floor((lo + hi) / 2);
      if (points[middle].progress < progress) lo = middle + 1;
      else hi = middle;
    }
    const last = points[lo], first = points[Math.max(0, lo - 1)];
    if (last.progress === first.progress) return last.time;
    const fraction = (progress - first.progress) / (last.progress - first.progress);
    return first.time + fraction * (last.time - first.time);
  }

  function passageDuration(runs, start, end) {
    // A full bin must lie in one continuous passage. Joining independently
    // valid endpoints would otherwise conceal a cut or missing interior data.
    let covering = null;
    for (const run of runs) {
      if (run.start <= start + epsilon && run.end >= end - epsilon) {
        if (covering) return null;
        covering = run;
      }
    }
    if (!covering) return null;
    for (const other of runs) {
      if (other === covering || other.end < start - epsilon || other.start > end + epsilon) continue;
      const left = Math.max(start, other.start), right = Math.min(end, other.end);
      // A repeated lap or overlapping partial pass cannot be silently chosen.
      if (right - left > epsilon) return null;
      // Touching boundaries are harmless only when passage time is unique.
      // This also catches an isolated observation from a second pass or stop.
      const location = Math.max(start, Math.min(end, (left + right) / 2));
      const primary = timeAt(covering, location), alternative = timeAt(other, location);
      if (primary === null || alternative === null || Math.abs(primary - alternative) > epsilon) return null;
    }
    const before = timeAt(covering, start), after = timeAt(covering, end);
    return before !== null && after !== null && after > before ? after - before : null;
  }

  function build(current, reference, {bins = 120, threshold = .01} = {}) {
    const count = Number.isInteger(bins) && bins > 0 ? bins : 120;
    const tolerance = Number.isFinite(threshold) ? Math.max(0, threshold) : .01;
    const a = passageRuns(current), b = passageRuns(reference);
    return Array.from({length: count}, (_, index) => {
      const start = index / count, end = (index + 1) / count;
      const currentTime = passageDuration(a, start, end);
      const referenceTime = passageDuration(b, start, end);
      let gain = null, winner = 'unavailable';
      if (currentTime !== null && referenceTime !== null) {
        gain = referenceTime - currentTime;
        if (Math.abs(gain) < epsilon) gain = 0;
        winner = Math.abs(gain) <= tolerance + epsilon ? 'equal' : gain > 0 ? 'current' : 'reference';
      }
      return {start, end, currentTime, referenceTime, gain, winner};
    });
  }

  return {build};
})();
