/* A compact, self-describing snapshot of the displayed two-lap comparison. */
'use strict';
window.ACCCComparisonExport = (() => {
  const SIDES = ['current', 'reference'];
  const MAX_ROWS = 600, MAX_BYTES = 200 * 1024;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const n = (value, digits = 3) => finite(value) ? Number(value.toFixed(digits)) : null;
  const text = (value, limit = 320) => typeof value === 'string' ? value.slice(0, limit) : null;
  const flag = value => typeof value === 'boolean' ? value : null;
  const basename = value => {
    if (typeof value !== 'string') return null;
    const path = /^(?:[\\/]|[a-z]:[\\/]|file:|~[\\/])/i.test(value) ||
      (/[\\/]/.test(value) && /\.(mp4|mov|mkv|webm|avi|m4v|ts)$/i.test(value));
    return text(path ? value.split(/[\\/]/).at(-1) : value) || null;
  };
  const fields = (source, names, transform = n) => Object.fromEntries(names.map(key => [key, transform(source?.[key])]));
  const table = (items, columns, row, limit = 128) => ({
    columns, total_count: items?.length || 0, omitted_count: Math.max(0, (items?.length || 0) - limit),
    rows: (items || []).slice(0, limit).map(row)
  });

  function publicUrl(value) {
    try {
      const url = new URL(value);
      // Only explicitly public video-source links belong in a shareable file.
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
          !/^(www\.|m\.)?(youtube\.com|youtu\.be|twitch\.tv)$/.test(url.hostname)) return null;
      const safe = new URL(url.origin + url.pathname);
      for (const key of ['v', 't']) if (url.searchParams.has(key)) safe.searchParams.set(key, url.searchParams.get(key));
      return safe.href.slice(0, 500);
    } catch { return null; }
  }

  function runInfo(result, side) {
    const trace = result[side], session = result.sessions?.[side] || {};
    const context = result.context?.[side] || session.context || {};
    const metrics = trace.metrics || {}, selection = trace.selection || {};
    return {
      role: side === 'current' ? 'Your run; the driver seeking advice' : 'Reference run',
      recording_name: basename(session.source?.name || session.name),
      public_video_url: publicUrl(session.source?.original_url),
      context: fields(context, ['car_id', 'car_name', 'track_id', 'track_name', 'platform', 'game_version',
        'conditions', 'bop', 'identity_source'], value => text(value, 160)),
      selection: {
        start_source_s: n(selection.start_time, 6), end_source_s: n(selection.end_time, 6),
        requested_start_source_s: n(selection.requested_start_time, 6),
        requested_end_source_s: n(selection.requested_end_time, 6)
      },
      duration_s: n(trace.duration), raw_distance_m: n(trace.raw_distance, 1),
      comparison_distance_m: n(trace.distance, 1),
      metrics: {
        active_seconds: n(metrics.active_seconds), average_speed_kmh: n(metrics.average_speed_kph, 1),
        max_speed_kmh: n(metrics.max_speed_kph, 1), average_brake_pct: n(metrics.avg_brake, 1),
        average_throttle_pct: n(metrics.avg_throttle, 1),
        ...fields(metrics, ['full_throttle_pct', 'braking_pct', 'coasting_pct', 'overlap_pct'], value => n(value, 1))
      },
      quality: {
        has_gaps: flag(trace.has_gaps), distance_complete: flag(trace.distance_complete),
        distance_estimated: flag(trace.distance_estimated), distance_boundaries_complete: flag(trace.distance_boundaries_complete),
        channels: Object.fromEntries(['speed', 'brake', 'throttle', 'gear', 'steering'].map(channel => {
          const details = trace.channels?.[channel] || {};
          return [channel, {available: flag(details.available), unit: text(details.unit, 32),
            coverage_pct: n(details.coverage_pct, 2), observed_seconds: n(details.observed_seconds), samples: n(details.samples, 0)}];
        })),
        speed_gap_interpolations: table(trace.speed_gap_interpolations,
          ['start_source_s', 'end_source_s', 'missing_samples'], gap => [n(gap.start_time), n(gap.end_time), n(gap.samples, 0)], 64),
        observed_segments: table(trace.segments, ['segment', 'start_source_s', 'end_source_s'],
          segment => [n(segment.id, 0), n(segment.start_time), n(segment.end_time)], 64)
      },
      braking_events: table(trace.braking_events, [
        'start_source_s', 'end_source_s', 'start_elapsed_s', 'end_elapsed_s', 'start_distance_m', 'peak_distance_m',
        'end_distance_m', 'raw_start_distance_m', 'raw_end_distance_m', 'duration_s', 'entry_speed_kmh',
        'minimum_speed_during_braking_kmh', 'exit_speed_kmh', 'peak_brake_pct', 'peak_to_release_s',
        'reapplications', 'truncated_start', 'truncated_end', 'segment'
      ], event => [n(event.start_time), n(event.end_time), n(event.start_elapsed), n(event.end_elapsed),
        n(event.start_distance, 1), n(event.peak_distance, 1), n(event.end_distance, 1),
        n(event.raw_start_distance ?? event.start_distance, 1), n(event.raw_end_distance ?? event.end_distance, 1),
        n(event.duration), n(event.entry_speed, 1), n(event.min_speed, 1), n(event.exit_speed, 1),
        n(event.peak_brake, 1), n(event.release_seconds), n(event.reapplications, 0),
        flag(event.truncated_start), flag(event.truncated_end), n(event.segment_id, 0)]),
      gear_changes: table(trace.shifts, ['source_s', 'elapsed_s', 'distance_m', 'raw_distance_m',
        'from_gear', 'to_gear', 'speed_kmh', 'brake_pct', 'throttle_pct', 'segment'],
      event => [n(event.time), n(event.elapsed), n(event.distance, 1), n(event.raw_distance ?? event.distance, 1),
        text(event.from_gear, 2), text(event.to_gear, 2), n(event.speed, 1), n(event.brake, 1), n(event.throttle, 1), n(event.segment_id, 0)])
    };
  }

  // Retain endpoints, marker positions, data gaps, input transitions and local
  // extrema, then fill the remaining budget with evenly spaced original rows.
  // Never interpolate a new observation while reducing the download size.
  function sampleIndices(result, limit) {
    const points = result.points, count = points.length;
    if (count <= limit) return {indices: points.map((_, i) => i), omittedFeatures: 0};
    const protectedIndices = new Set([0, count - 1]), priority = new Map();
    const add = (i, score) => { if (i >= 0 && i < count) priority.set(i, Math.max(score, priority.get(i) || 0)); };
    const nearest = position => {
      if (!finite(position)) return null;
      let lo = 0, hi = count - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (points[mid].position < position) lo = mid + 1; else hi = mid; }
      return lo && Math.abs(points[lo - 1].position - position) < Math.abs(points[lo].position - position) ? lo - 1 : lo;
    };
    for (const side of SIDES) {
      const first = points.findIndex(point => point[side]), last = points.findLastIndex(point => point[side]);
      if (first >= 0) { protectedIndices.add(first); protectedIndices.add(last); }
      for (const event of result[side].braking_events || []) {
        for (const position of [event.start_position, event.end_position, result.axis === 'distance' ? event.peak_distance : event.peak_elapsed]) {
          const index = nearest(position); if (index !== null) add(index, 12);
        }
      }
    }
    for (const anchor of (result.alignment?.anchors || []).slice(0, 34)) {
      const index = nearest(result.axis === 'distance' ? anchor.reference_distance_m :
        anchor.current_time - result.current.selection.start_time);
      if (index !== null) protectedIndices.add(index);
    }
    const channelScales = {speed: 300, brake: 100, throttle: 100, steering: 200};
    for (let i = 1; i < count; i++) {
      const before = points[i - 1], point = points[i];
      if (before.delta_segment_id !== point.delta_segment_id) {add(i - 1, 20); add(i, 20);}
      for (const side of SIDES) {
        const a = before[side], b = point[side];
        if (!!a !== !!b || a?.segment_id !== b?.segment_id || a?.distance_segment_id !== b?.distance_segment_id ||
            a?.distance_interpolated !== b?.distance_interpolated ||
            Object.keys(channelScales).some(key => finite(a?.[key]) !== finite(b?.[key]))) {
          add(i - 1, 20); add(i, 20);
        }
        if (a?.gear !== b?.gear) {add(i - 1, 10); add(i, 10);}
        for (const key of ['brake', 'throttle']) for (const threshold of [3, 95]) {
          if (finite(a?.[key]) && finite(b?.[key]) && (a[key] > threshold) !== (b[key] > threshold)) {
            add(i - 1, 10); add(i, 10);
          }
        }
        const c = points[i + 1]?.[side];
        for (const [key, scale] of Object.entries(channelScales)) {
          if ([a?.[key], b?.[key], c?.[key]].every(finite) && a.segment_id === b.segment_id && b.segment_id === c.segment_id) {
            const change = Math.abs(b[key] - (a[key] + c[key]) / 2) / scale;
            if (change >= .005) add(i, 4 + change);
          }
        }
      }
      if (i + 1 < count && [before.delta_time_s, point.delta_time_s, points[i + 1].delta_time_s].every(finite) &&
          before.delta_segment_id === point.delta_segment_id && point.delta_segment_id === points[i + 1].delta_segment_id &&
          (point.delta_time_s - before.delta_time_s) * (points[i + 1].delta_time_s - point.delta_time_s) < 0) add(i, 7);
    }
    const ranked = [...priority.keys()].sort((a, b) => priority.get(b) - priority.get(a) || a - b);
    const selected = new Set(protectedIndices);
    const featureBudget = Math.max(selected.size, Math.floor(limit * .7));
    for (const index of ranked) {if (selected.size >= featureBudget) break; selected.add(index);}
    // Refine a uniform grid until it fills the budget, retaining its endpoints.
    for (let divisions = Math.max(2, limit - selected.size); selected.size < limit; divisions *= 2) {
      for (let i = 0; i <= divisions && selected.size < limit; i++) selected.add(Math.round(i * (count - 1) / divisions));
    }
    return {indices: [...selected].sort((a, b) => a - b), omittedFeatures: ranked.filter(index => !selected.has(index)).length};
  }

  const runColumns = ['source_s', 'elapsed_s', 'distance_m', 'raw_distance_m', 'speed_kmh', 'brake_pct',
    'throttle_pct', 'gear', 'steering', 'segment', 'distance_segment', 'distance_interpolated'];
  function sampleRow(point) {
    return [n(point.position, 3), n(point.delta_time_s), n(point.delta_current_time), n(point.delta_reference_time),
      text(point.delta_segment_id, 40), ...SIDES.flatMap(side => {
        const p = point[side];
        return p ? [n(p.time), n(p.elapsed), n(p.distance, 1), n(p.raw_distance, 1), n(p.speed, 1),
          n(p.brake, 1), n(p.throttle, 1), text(p.gear, 2), n(p.steering, 1),
          n(p.segment_id, 0), n(p.distance_segment_id, 0), flag(p.distance_interpolated)] : runColumns.map(() => null);
      })];
  }

  // Tables use one JSON row per line; numbers do not each consume a separate
  // line as they would with a conventional pretty-printed nested array.
  function encode(value, depth = 0) {
    const indent = '  '.repeat(depth);
    if (Array.isArray(value)) return value.length && Array.isArray(value[0]) ?
      '[\n' + value.map(row => indent + '  ' + JSON.stringify(row)).join(',\n') + '\n' + indent + ']' : JSON.stringify(value);
    if (value && typeof value === 'object') return '{\n' + Object.entries(value).map(([key, child]) =>
      indent + '  ' + JSON.stringify(key) + ': ' + encode(child, depth + 1)).join(',\n') + '\n' + indent + '}';
    return JSON.stringify(value);
  }

  function create(result) {
    if (!result?.current || !result?.reference || !Array.isArray(result.points) || !result.points.length || result.video_only) {
      throw new Error('Compare two recordings before exporting.');
    }
    const alignment = result.alignment || {}, context = result.context || {};
    const warnings = [...new Set((result.warnings || []).map(warning => text(warning)))];
    const document = {
      format: 'accc-telemetry-comparison', schema_version: 1, exported_at_utc: new Date().toISOString(),
      purpose: 'Driving advice from the two selected video-derived lap ranges.',
      suggested_request: 'Compare my current run with the reference. Prioritize braking, release, minimum speed during braking, throttle application and gear choices. Cite source timestamps or distances and explain uncertainty. Distinguish observations from hypotheses; do not invent corner names or setup causes.',
      reading_guide: {
        tables: 'Each row follows its table columns in order. current is your run; reference is the comparison run.',
        missing: 'null means unavailable, never zero. Do not connect across changed segment or distance_segment IDs, or missing values. distance_interpolated flags a bounded speed gap used only for distance; the speed reading remains null.',
        units: 'Times and durations: seconds. Distances: metres. Speed: km/h. Brake/throttle: percent. Gear: string including N/R. Steering: use each run quality.channels.steering.unit; HUD percent is not a road-wheel angle.',
        precision: 'Trace times/delta: 0.001 s; distances/speed/pedals/steering: 0.1 unit. Selection boundaries retain up to six decimals. The comparison engine may already interpolate trace values between video samples; these are not all direct frame readings or precision sensors.',
        delta: 'Positive delta means current is slower. delta_s = (delta_current_source_s - current start_source_s) - (delta_reference_source_s - reference start_source_s). These matched passages can differ from the plotted source times on a Time chart. selected_duration_delta_s compares full selected durations; it is not the last chart delta if the distances differ.',
        distance: 'raw_distance_m integrates the observed speeds plus the listed short gap estimates. distance_m follows the comparison coordinate, which can be registered to confirmed markers. Incomplete distances exclude unobserved travel; equal raw distances do not prove the same track position.',
        events: 'Braking events come from the full selected samples, not the reduced trace. minimum_speed_during_braking is not necessarily apex speed. peak_to_release_s ends when brake falls to 3% or less; it is not an independently measured trail-braking phase. Truncated events end/start at a range boundary or missing data.',
        identity: 'Recording names are labels, not verified car/track identity. Confirmed context is provided separately. Gear and HUD steering values are not directly equivalent across different cars.',
        limits: 'The trace is a reduced snapshot of the displayed full-range comparison, independent of chart zoom. Use full-range summary metrics and events for totals. Sampling can omit brief features; tables report any omitted records.'
      },
      comparison: {
        axis: result.axis, position_unit: result.axis === 'distance' ? 'm' : 's',
        selected_duration_delta_s: finite(result.current.duration) && finite(result.reference.duration) ? n(result.current.duration - result.reference.duration) : null,
        delta_available: flag(result.delta_available), delta_estimated: flag(result.delta_estimated),
        delta_basis: text(result.delta_basis, 80), delta_mode: text(result.delta_mode, 80),
        same_track: flag(context.same_track), track_basis: text(context.track_basis, 80),
        starts_confirmed: flag(result.align_start), finishes_confirmed: flag(result.align_end),
        extent: (result.extent || []).slice(0, 2).map(value => n(value)),
        alignment: {
          mode: text(alignment.mode, 40), requested_mode: text(alignment.requested_mode, 40), applied: flag(alignment.applied),
          estimated: flag(alignment.estimated), coordinate: text(alignment.coordinate, 80),
          fallback_reason: text(alignment.fallback_reason), max_correction_pct: n(alignment.max_correction_pct, 2),
          scales: (alignment.scales || []).slice(0, 33).map(value => n(value, 6)),
          anchors: table(alignment.anchors, ['label', 'current_source_s', 'reference_source_s', 'current_raw_distance_m', 'reference_raw_distance_m'],
            anchor => [text(anchor.label, 80), n(anchor.current_time, 6), n(anchor.reference_time, 6), n(anchor.current_distance_m, 1), n(anchor.reference_distance_m, 1)], 34)
        },
        observed_segments: {
          available: flag(result.observed_segments?.available), basis: text(result.observed_segments?.basis, 80),
          reason: text(result.observed_segments?.reason),
          ...table(result.observed_segments?.segments, ['start_marker', 'end_marker', 'current_s', 'reference_s', 'delta_s'],
            segment => [text(segment.start_label, 80), text(segment.end_label, 80), n(segment.current_seconds), n(segment.reference_seconds), n(segment.delta_seconds)], 33)
        }
      },
      warnings: warnings.slice(0, 40),
      warnings_omitted: Math.max(0, warnings.length - 40),
      runs: Object.fromEntries(SIDES.map(side => [side, runInfo(result, side)])),
      telemetry: {
        columns: ['position_' + (result.axis === 'distance' ? 'm' : 's'), 'delta_s', 'delta_current_source_s', 'delta_reference_source_s',
          'delta_segment', ...SIDES.flatMap(side => runColumns.map(column => side + '_' + column))],
        sampling: {method: 'Existing comparison rows; endpoints, landmarks and prioritized changes plus a uniform grid',
          input_rows: result.points.length, exported_rows: null, omitted_export_feature_rows: null,
          upstream_method: text(result.sampling?.method, 80), upstream_omitted_feature_positions: n(result.sampling?.omitted_feature_positions, 0)},
        rows: []
      }
    };
    let output, bytes;
    for (const budget of [MAX_ROWS, 480, 360, 240, 120, 80]) {
      const sampled = sampleIndices(result, budget);
      document.telemetry.rows = sampled.indices.map(index => sampleRow(result.points[index]));
      Object.assign(document.telemetry.sampling, {exported_rows: sampled.indices.length, omitted_export_feature_rows: sampled.omittedFeatures});
      output = encode(document) + '\n';
      bytes = new TextEncoder().encode(output).length;
      if (bytes <= MAX_BYTES) break;
    }
    if (bytes > MAX_BYTES) throw new Error('This comparison exceeds the compact export limit. Select shorter lap ranges.');
    const slug = value => (basename(value) || 'lap').replace(/\.[a-z0-9]{2,5}$/i, '').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').slice(0, 36) || 'lap';
    return {filename: `accc-${slug(document.runs.current.recording_name)}-vs-${slug(document.runs.reference.recording_name)}.json`,
      text: output, bytes, rowCount: document.telemetry.rows.length};
  }
  return {create};
})();
