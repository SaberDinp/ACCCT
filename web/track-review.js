/* Route position is a distance-based review aid, never a measured racing line. */
'use strict';
window.ACCCTrackReview = (() => {
  const $ = id => document.getElementById(id);
  const finite = x => x !== null && x !== undefined && Number.isFinite(Number(x));
  const wrap = x => ((x % 1) + 1) % 1;
  const clamp = x => Math.max(0, Math.min(1, x));
  const sides = ['current', 'reference'];
  const markerId = side => side === 'current' ? 'trackCarMarker' : 'trackReferenceCarMarker';
  const paceColors = {current: '#6dccdf', reference: '#e0bf79', equal: '#b7c4bd', unavailable: '#4d5e54'};
  const state = {track: null, result: null, point: null, sourceCursor: null, trackSource: 'none', trackChoice: '', tracksReady: false, detectionMessage: '', progressSources: {}, playback: {playing: false, available: false}, ids: {}, frames: {}, media: {}, pairKey: '', selectionKey: ''};
  let helpers, resize;
  const timeLabel = value => finite(value) ? `${Math.floor(value / 60)}:${(value % 60).toFixed(2).padStart(5, '0')}` : '--:--';

  function init(context) {
    helpers = context;
    const section = document.createElement('section');
    section.id = 'trackReview';
    section.className = 'track-review-section';
    section.innerHTML = `<div class="track-video-column"><div class="track-video-pair">${['current', 'reference'].map(side => `<figure class="track-video ${side}"><figcaption><span class="track-source-label">${side === 'current' ? 'YOUR RUN' : 'REFERENCE'}</span><span id="track${side}Time">--:--</span></figcaption><button class="track-video-button" id="track${side}Inspect" aria-label="Enlarge ${side === 'current' ? 'your' : 'reference'} corner frame" title="Inspect full source frame"><video id="track${side}Native" muted playsinline preload="auto" hidden></video><img id="track${side}Image" alt="${side === 'current' ? 'Your' : 'Reference'} car and track at the review cursor" hidden><span id="track${side}Empty">No observed frame</span></button><div class="track-frame-values" id="track${side}Values">No telemetry at cursor</div></figure>`).join('')}</div><div class="track-distance-control"><button id="trackReviewPlay" class="icon-button" aria-label="Play comparison" title="Play comparison" disabled><i data-lucide="play"></i></button><span id="trackCursorDistance">-- m</span><input id="trackReviewDistance" type="range" min="0" max="1" step="1" value="0" disabled aria-label="Corner review distance"><span id="trackReviewAxisLabel">Review distance</span></div></div>
      <div class="track-review-body"><div class="track-map-column"><div class="track-review-heading"><div><span class="eyebrow">CIRCUIT GUIDE</span><h2>Track &amp; corner review</h2></div><span class="track-limit">Estimated position</span></div><select id="trackSelect" aria-label="Track layout"><option value="">Select track layout</option></select><p id="trackDetectionStatus" class="track-detection-status" role="status" hidden></p><div class="track-map-stage"><canvas id="trackMap" tabindex="0" aria-label="Circuit centerline diagram. Colored sections show which run gains time; hover for section times. Hover or focus corner numbers for names. Lap progress is estimated."></canvas><div id="trackAnnotations" class="track-annotations"></div>${["current", "reference"].map(side => `<button id="${markerId(side)}" class="track-car-marker ${side}" data-side="${side}" hidden><svg class="track-car-shape" viewBox="0 0 32 20" aria-hidden="true"><path class="track-car-tyres" d="M7 1h5v4H7zM21 1h5v4h-5zM7 15h5v4H7zM21 15h5v4h-5z"/><path class="track-car-body" d="M4 5Q3 5 3 10t1 5h21q4-1 4-5t-4-5Z"/><path class="track-car-window" d="m17 6 5 1v6l-5 1z"/><path class="track-car-tail" d="M5 4v12"/></svg><span class="track-annotation-tip" id="${side === "current" ? "trackCarTip" : "trackReferenceCarTip"}"></span></button>`).join('')}</div><div class="track-map-caption"><span id="trackLength">-- m</span><span id="trackOriginStatus">Lap start / finish</span></div><div class="track-map-legend"><span class="track-legend-finish">Start / finish</span><span class="track-legend-sector">Sector split</span></div><div class="track-review-status" id="trackReviewStatus" role="status">Select a circuit to see its layout.</div><p class="track-measurement-note">Cars follow estimated progress along the circuit. Select a lap to start and finish at the checkered line.</p><details class="track-map-details"><summary>Map sources and accuracy</summary><div class="track-credit" id="trackCredit"></div><p id="trackAnnotationNote"></p></details></div><div class="track-telemetry-column" id="trackTelemetry"></div></div>`;
    const workspace = $('referenceWorkspace');
    workspace.querySelector('.compare-toolbar').insertAdjacentElement('beforebegin', section);
    for (const selector of ['.compare-toolbar', '.comparison-legend', '.comparison-empty-status', '.comparison-body', '.compare-summary-line']) {
      $('trackTelemetry').appendChild(workspace.querySelector(selector));
    }
    const registration = workspace.querySelector('.lap-registration');
    if (registration) section.before(registration);
    document.querySelector('.track-review-heading').insertAdjacentHTML('beforeend', '<button class="icon-button" id="trackExpand" aria-label="Expand circuit map" title="Expand circuit map"><i data-lucide="maximize-2"></i></button>');
    document.querySelector('.track-map-stage').insertAdjacentHTML('beforeend', '<button class="button subtle compact track-map-exit" id="trackCollapse" aria-label="Close expanded circuit map">Close map</button>');
    document.querySelector('.track-map-stage').insertAdjacentHTML('beforeend', '<div id="trackPaceTip" class="track-pace-tip" role="tooltip" hidden></div>');
    $('trackSelect').insertAdjacentHTML('afterend', '<div id="trackPaceLegend" class="track-pace-legend" aria-label="Local section pace" hidden><span class="track-pace-current" id="trackPaceCurrent">Your run faster</span><span class="track-pace-reference" id="trackPaceReference">Reference faster</span><span class="track-pace-equal" title="Section times within 0.010 seconds">Even</span><span class="track-pace-unavailable">No comparison</span></div><p id="trackPaceStatus" class="track-pace-status" hidden></p>');
    $('trackExpand').onclick = () => document.querySelector('.track-map-stage').requestFullscreen?.();
    $('trackCollapse').onclick = () => document.exitFullscreen?.();
    document.addEventListener('fullscreenchange', () => {
      // Keep the key visible when the circuit is expanded.
      if (document.fullscreenElement === document.querySelector('.track-map-stage')) document.fullscreenElement.append($('trackPaceLegend'));
      else $('trackSelect').after($('trackPaceLegend'));
      hidePaceTip(); draw();
    });
    for (const side of sides) $(markerId(side)).onclick = event => event.stopPropagation();
    $('trackReviewPlay').onclick = () => helpers.playback?.toggle();
    for (const side of ['current', 'reference']) {
      const figure = $(`track${side}Inspect`).closest('figure');
      figure.id = `track${side}Video`;
      figure.hidden = true;
      const video = $(`track${side}Native`);
      video.addEventListener('loadedmetadata', () => syncNative(side));
      video.addEventListener('seeked', () => showNative(side));
      video.addEventListener('loadeddata', () => showNative(side));
      video.addEventListener('error', () => {video.hidden = true; if (state.media[side]) state.media[side].failed = true;});
    }
    helpers.icons();
    $('trackSelect').onchange = () => {
      state.inferenceRequest = (state.inferenceRequest || 0) + 1;
      state.trackSource = 'manual'; state.detectionMessage = '';
      loadTrack($('trackSelect').value);
    };
    $('trackMap').onclick = choosePosition;
    $('trackMap').onpointermove = hoverPace;
    $('trackMap').onpointerleave = hidePaceTip;
    $('trackMap').onblur = hidePaceTip;
    $('trackMap').onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || !mapped()) return;
      event.preventDefault();
      seekDistance((state.point?.position || 0) + (event.key === 'ArrowRight' ? 1 : -1) * 25);
    };
    $('trackReviewDistance').oninput = () => helpers.seek(Number($('trackReviewDistance').value));
    for (const side of ['current', 'reference']) $(`track${side}Inspect`).onclick = () => {
      const time = sourceSample(side)?.time;
      if (finite(time)) {helpers.playback?.pause(); helpers.inspect(side, time);}
    };
    resize = new ResizeObserver(draw);
    resize.observe($('trackMap'));
    context.api('/api/tracks').then(tracks => {
      for (const track of tracks) $('trackSelect').add(new Option(track.name, track.id));
      state.tracksReady = true; restore();
    }).catch(() => {$('trackReviewStatus').textContent = 'Track library unavailable. Source frames remain available.';});
    render();
  }

  // Retain circuit choices, but never restore old manual map positions or modes.
  function settingsKey() {return `accc.track-review.v2.${state.pairKey}`;}
  function save() {
    if (!state.ids.current && !state.ids.reference) return;
    const value = JSON.stringify({track: state.trackChoice, selectionSource: state.trackSource});
    try {window.ACCCDesktopPreferences?.setItem(settingsKey(), value);} catch {}
    try {localStorage.setItem(settingsKey(), value);} catch {}
  }
  function restore() {
    if (!state.tracksReady || (!state.ids.current && !state.ids.reference)) return;
    let saved;
    try {
      const desktop = window.ACCCDesktopPreferences;
      let value;
      try {value = desktop?.getItem(settingsKey());} catch {}
      value ??= localStorage.getItem(settingsKey()) ?? localStorage.getItem(`apex.track-review.v2.${state.pairKey}`);
      saved = JSON.parse(value ?? '{}') || {};
      try {if (value !== null) desktop?.setItem(settingsKey(), value);} catch {}
    } catch {saved = {};}
    const matching = trackAvailable(saved.track);
    // Older saves did not distinguish automatic and explicit track choices.
    // Preserve their selected circuit as a user choice, without manual geometry.
    if ((matching && !saved.selectionSource) || ((matching || saved.track === '') && saved.selectionSource === 'manual')) {
      state.trackSource = 'manual';
      loadTrack(saved.track);
    } else {
      updateAutomaticTrack();
    }
  }
  function trackAvailable(id) {
    return Boolean(id) && [...$('trackSelect').options].some(option => option.value === id);
  }
  async function updateAutomaticTrack() {
    if (!state.tracksReady || !state.result || state.trackSource === 'manual') return;
    const labelled = helpers.trackId?.();
    if (trackAvailable(labelled)) {
      state.inferenceRequest = (state.inferenceRequest || 0) + 1;
      state.trackSource = 'recording_label';
      state.detectionMessage = 'Circuit from the recording details.';
      if (state.trackChoice !== labelled || !state.track) loadTrack(labelled);
      else render();
      return;
    }
    // A confident complete-lap match still identifies this recording when its
    // selected range is subsequently shortened. A different pair starts fresh.
    if (state.trackSource === 'telemetry' && state.trackChoice) return;
    const key = `${state.pairKey}:${state.selectionKey}`;
    if (key === state.inferenceKey) return;
    const requests = sides.flatMap(side => {
      const id = state.ids[side], info = helpers.rangeInfo?.(side) || {};
      const selection = state.result[side]?.selection || state.result.source_review?.[side]?.selection;
      if (!id || !finite(selection?.start_time) || !finite(selection?.end_time) || selection.end_time <= selection.start_time) return [];
      const range = {start_time: Number(selection.start_time), end_time: Number(selection.end_time)};
      if (info.segment !== null && info.segment !== undefined) range.segment_id = info.segment;
      return [{session_id: id, range, range_is_lap: info.lap === true}];
    });
    if (!requests.length) return;
    state.inferenceKey = key;
    const token = state.inferenceRequest = (state.inferenceRequest || 0) + 1;
    state.detectionMessage = 'Checking the circuit from lap telemetry…';
    render();
    try {
      const results = await Promise.all(requests.map(body => helpers.api('/api/track-inference', {
        method: 'POST', body: JSON.stringify(body)
      })));
      if (token !== state.inferenceRequest || state.trackSource === 'manual') return;
      const matches = results.filter(result => result.auto_select === true && result.confidence === 'high' && trackAvailable(result.track_id));
      const tracks = [...new Set(matches.map(result => result.track_id))];
      if (tracks.length === 1) {
        state.trackSource = matches.some(result => result.source === 'recording_label') ? 'recording_label' : 'telemetry';
        state.detectionMessage = state.trackSource === 'telemetry' ? 'Circuit matched from lap telemetry. You can choose another circuit.' : 'Circuit from the recording details.';
        loadTrack(tracks[0]);
      } else {
        state.detectionMessage = tracks.length > 1 ? 'The recordings suggest different circuits. Choose a circuit to review.' : 'No confident circuit match. Choose a circuit, or select a complete lap.';
        render();
      }
    } catch {
      if (token !== state.inferenceRequest || state.trackSource === 'manual') return;
      state.detectionMessage = 'Circuit detection is unavailable. Choose a circuit to continue.';
      render();
    }
  }
  async function loadTrack(id) {
    const token = state.trackRequest = (state.trackRequest || 0) + 1;
    state.mapMessage = null;
    state.track = null;
    state.trackChoice = id;
    $('trackSelect').value = id;
    save();
    render();
    if (!id) {save(); return;}
    try {
      const track = await helpers.api(`/api/tracks/${encodeURIComponent(id)}`);
      if (token !== state.trackRequest) return;
      state.track = track;
      state.annotationTrack = null;
      save(); render();
    } catch (error) {if (token === state.trackRequest) $('trackReviewStatus').textContent = error.message;}
  }
  function setComparison(result, ids = {}) {
    if (!helpers) return;
    state.mapMessage = null;
    const pairKey = `${ids.current || ''}:${ids.reference || ''}`;
    const selectionKey = result ? JSON.stringify(sides.map(side => [result[side]?.selection || result.source_review?.[side]?.selection, helpers.rangeInfo?.(side)?.lap === true])) : '';
    const changed = pairKey !== state.pairKey;
    if (changed) {
      state.trackRequest = (state.trackRequest || 0) + 1;
      state.inferenceRequest = (state.inferenceRequest || 0) + 1;
      state.track = null; state.trackChoice = ''; state.trackSource = 'none'; state.inferenceKey = null; state.detectionMessage = '';
      state.frames = {}; $('trackSelect').value = '';
      for (const side of ['current', 'reference']) clearFrame(side);
    } else if (selectionKey !== state.selectionKey) {
      state.inferenceRequest = (state.inferenceRequest || 0) + 1;
      state.inferenceKey = null;
    }
    state.pairKey = pairKey; state.selectionKey = selectionKey; state.ids = {...ids}; state.result = result;
    for (const side of ['current', 'reference']) selectNative(side, ids[side]);
    state.point = null;
    prepareProgress();
    if (changed) restore();
    else updateAutomaticTrack();
    render();
    setCursor(null);
  }
  function sourceSample(side) {
    return state.sourceCursor ? state.sourceCursor.sources[side] : state.point?.[side];
  }
  function setCursor(point, sourceCursor = null) {
    if (!helpers) return;
    state.mapMessage = null;
    state.point = point;
    state.sourceCursor = sourceCursor;
    for (const side of ['current', 'reference']) {
      const sample = state.result?.[side] ? point?.[side] : null;
      const source = sourceSample(side);
      if (!source || !finite(source.time)) clearFrame(side, state.ids[side] ? 'No matching distance. Select Time to review this video.' : 'No observed frame');
      else if (!state.playback.playing && (!state.frames[side] || Math.abs(state.frames[side].time - source.time) > .06)) clearFrame(side, 'Loading frame...');
      const value = key => finite(sample?.[key]) ? Math.round(sample[key]) : '--';
      $(`track${side}Values`).textContent = source?.ended ? 'End of selected range' : sample ? `${value('speed')} km/h  /  gear ${sample.gear ?? '--'}  /  brake ${value('brake')}%  /  throttle ${value('throttle')}%` : 'HUD telemetry unavailable at this time';
      const media = state.media[side];
      if (media) {
        clearTimeout(media.timer);
        if (state.playback.playing) syncNative(side);
        else media.timer = setTimeout(() => syncNative(side), 75);
      }
    }
    renderCursor(); draw();
  }
  function clearFrame(side, label = 'No observed frame') {
    delete state.frames[side];
    $(`track${side}Image`).hidden = true;
    $(`track${side}Image`).removeAttribute('src');
    $(`track${side}Empty`).hidden = false;
    $(`track${side}Empty`).textContent = label;
    $(`track${side}Time`).textContent = '--:--';
    $(`track${side}Inspect`).disabled = true;
    $(`track${side}Native`).hidden = true;
  }
  function setFrame(side, url, time, id) {
    const source = sourceSample(side);
    if (!helpers || id !== state.ids[side] || !source || (!state.playback.playing && Math.abs(time - source.time) > .06)) return;
    if (!url) {clearFrame(side); return;}
    state.frames[side] = {url, time, id};
    const image = $(`track${side}Image`);
    image.src = url; image.hidden = false;
    $(`track${side}Empty`).hidden = true;
    $(`track${side}Time`).textContent = timeLabel(time);
    $(`track${side}Inspect`).disabled = false;
    showNative(side);
  }
  async function selectNative(side, id) {
    if (state.media[side]?.id === id) return;
    const previous = state.media[side];
    if (previous) clearTimeout(previous.timer);
    const video = $(`track${side}Native`);
    video.pause(); video.hidden = true; video.removeAttribute('src'); video.load();
    const media = state.media[side] = {id};
    if (!id) return;
    try {
      const metadata = await helpers.api(`/api/sessions/${encodeURIComponent(id)}/video`);
      if (state.media[side] !== media || metadata.state !== 'ready' || !metadata.url) return;
      Object.assign(media, metadata);
      video.src = metadata.url;
      video.load();
    } catch { /* A decoded source frame remains available when native media cannot play. */ }
  }
  function syncNative(side) {
    const media = state.media[side], sample = sourceSample(side), video = $(`track${side}Native`);
    if (!sample) video.pause();
    if (!media || media.failed || media.id !== state.ids[side] || !sample || !finite(sample.time) || video.readyState < 1) return;
    // Original video preserves its presentation timestamps. Only remuxed audio
    // starts at zero; subtracting source_start here seeks the wrong video frame.
    const stamp = Number(sample.time);
    if (stamp < Number(media.source_start || 0) || (finite(video.duration) && stamp >= video.duration)) {video.hidden = true; return;}
    const playing = state.playback.playing && sample.active !== false && !sample.ended;
    if (playing) {
      const drift = stamp - video.currentTime;
      if (!video.seeking && Math.abs(drift) > .5) video.currentTime = stamp;
      const rate = nativeRate(side);
      video.playbackRate = Math.max(.25, Math.min(4, rate + Math.max(-.15, Math.min(.15, drift * .3))));
      video.play().catch(() => {});
      showNative(side);
      return;
    }
    video.pause();
    if (Math.abs(video.currentTime - stamp) > .025) {
      video.hidden = true;
      video.currentTime = stamp;
    } else showNative(side);
  }
  function nativeRate(side) {
    if (state.result?.axis === 'elapsed') return 1;
    const points = state.result?.points || [], position = state.point?.position || 0;
    let lo = 0, hi = points.length - 1;
    while (lo < hi) {const mid = Math.ceil((lo + hi) / 2); if (points[mid].position <= position) lo = mid; else hi = mid - 1;}
    const a = points[lo], b = points[Math.min(lo + 1, points.length - 1)];
    const lead = a?.current && b?.current ? 'current' : 'reference';
    const dt = b?.[lead]?.time - a?.[lead]?.time, own = b?.[side]?.time - a?.[side]?.time;
    return dt > 0 && own > 0 ? Math.max(.25, Math.min(4, own / dt)) : 1;
  }
  function showNative(side) {
    const media = state.media[side], sample = sourceSample(side), video = $(`track${side}Native`);
    const ready = media && !media.failed && media.id === state.ids[side] && sample && video.readyState >= 2 && !video.seeking &&
      Math.abs(video.currentTime - Number(sample.time)) < (state.playback.playing ? .55 : .06);
    video.hidden = !ready;
    if (ready) {
      $(`track${side}Empty`).hidden = true;
      $(`track${side}Inspect`).disabled = false;
      $(`track${side}Time`).textContent = timeLabel(sample.time);
    }
  }
  function setPlayback(playback) {
    const wasPlaying = state.playback.playing;
    state.playback = {...state.playback, ...playback};
    const button = $('trackReviewPlay');
    if (!button) return;
    const label = state.playback.playing ? 'Pause comparison' : 'Play comparison';
    button.disabled = !state.playback.available;
    button.setAttribute('aria-label', label); button.title = label;
    button.setAttribute('aria-pressed', String(state.playback.playing));
    if (wasPlaying !== state.playback.playing || !button.dataset.ready) {
      button.innerHTML = `<i data-lucide="${state.playback.playing ? 'pause' : 'play'}"></i>`;
      button.dataset.ready = 'true'; helpers.icons();
    }
    if (wasPlaying !== state.playback.playing) for (const side of sides) syncNative(side);
  }
  function renderCursor() {
    const distanceAxis = state.result?.axis !== 'elapsed';
    $('trackReviewDistance').disabled = !state.result?.points?.length;
    $('trackReviewDistance').min = state.result?.extent?.[0] || 0;
    $('trackReviewDistance').max = state.result?.extent?.[1] || 1;
    $('trackReviewDistance').step = 'any';
    const position = state.sourceCursor?.position ?? state.point?.position;
    $('trackReviewDistance').value = position || 0;
    $('trackReviewDistance').setAttribute('aria-label', distanceAxis ? 'Corner review distance' : 'Corner review time');
    $('trackCursorDistance').textContent = state.result && finite(position) ? distanceAxis ? `${Math.round(position).toLocaleString()} m` : `${Number(position).toFixed(2)} s` : '--';
    $('trackReviewAxisLabel').textContent = distanceAxis ? 'Review distance' : 'Review time';
    if (state.result && state.track && !trackMismatch() && !state.mapMessage) {
      $('trackReviewStatus').textContent = mapped() ? 'Play or scrub to follow each run around its lap.' : 'Select a lap, or set its start and end, to show automatic lap progress.';
    }
  }
  function sampleAt(points, time) {
    if (!points?.length || time < points[0].time - .001 || time > points.at(-1).time + .001) return null;
    let lo = 0, hi = points.length - 1;
    while (lo < hi) {const mid = Math.floor((lo + hi) / 2); if (points[mid].time < time) lo = mid + 1; else hi = mid;}
    const b = points[lo], a = points[Math.max(0, lo - 1)];
    if (Math.abs(time - b.time) < .001) return b;
    if (b.time <= a.time || b.gaps !== a.gaps) return null;
    const fraction = clamp((time - a.time) / (b.time - a.time));
    return {...a, time, distance: a.distance + (b.distance - a.distance) * fraction};
  }
  function prepareProgress() {
    state.progressSources = {};
    for (const side of sides) {
      const trace = state.result?.[side];
      if (!trace) continue;
      const info = helpers.rangeInfo?.(side) || {};
      const plot = (state.result.points || []).filter(point => point[side] && finite(point[side].time));
      // Full recording samples let a custom slice retain its place within a lap.
      // Integrate once, with the same one-second discontinuity bound as review.
      const groups = new Map();
      for (const sample of helpers.sourceSamples?.(side) || []) {
        if (!finite(sample.time)) continue;
        const key = sample.segment_id ?? 'default';
        if (!groups.has(key)) groups.set(key, {points: [], distance: 0, gaps: 0, previous: null});
        const group = groups.get(key), previous = group.previous;
        const valid = sample.active !== false && finite(sample.speed) && Number(sample.speed) >= 0;
        const dt = previous ? Number(sample.time) - previous.time : 0;
        if (valid && previous && dt > 0 && dt <= 1) group.distance += (Number(sample.speed) + previous.speed) * .5 * dt / 3.6;
        else if (group.points.length) group.gaps++;
        group.points.push({time: Number(sample.time), distance: group.distance, gaps: group.gaps});
        group.previous = valid ? {time: Number(sample.time), speed: Number(sample.speed)} : null;
      }
      const laps = (info.lapRanges || []).map(lap => {
        const segment = lap.segment ?? lap.selection?.segment_id;
        const candidates = segment != null ? [groups.get(segment)].filter(Boolean) : [...groups.values()];
        for (const group of candidates) {
          const start = sampleAt(group.points, Number(lap.start)), end = sampleAt(group.points, Number(lap.end));
          // Retain observed distance when another part of the lap has a gap.
          // sampleAt still refuses interpolation across that gap itself.
          if (start && end && end.distance > start.distance) return {...lap, start: Number(lap.start), end: Number(lap.end), samples: group.points, startDistance: start.distance, totalDistance: end.distance - start.distance};
        }
        return {...lap, start: Number(lap.start), end: Number(lap.end), timeOnly: true};
      }).filter(lap => finite(lap.start) && finite(lap.end) && lap.end > lap.start);
      state.progressSources[side] = {info, trace, plot, laps};
    }
  }
  function progressFor(side, point = state.point) {
    const source = state.progressSources[side];
    if (!source || !state.track || !point || trackMismatch()) return null;
    let sample = point[side], finished = false;
    // A shorter recording has finished while its comparison continues.
    if (!sample && source.plot.length && point.position > source.plot.at(-1).position) {
      sample = source.plot.at(-1)[side]; finished = true;
    }
    if (!sample || sample.active === false || !finite(sample.time)) return null;
    const distance = finite(sample.raw_distance) ? Number(sample.raw_distance) : finite(sample.distance) ? Number(sample.distance) : null;
    let progress, timeOnly = false, unverified = false;
    if (source.info.lap) {
      const start = Number(source.trace.selection?.start_time), end = Number(source.trace.selection?.end_time);
      if (!(end > start)) return null;
      const total = Number(source.trace.raw_distance ?? source.trace.distance);
      progress = distance !== null && total > 0 ? distance / total : (Number(sample.time) - start) / (end - start);
      timeOnly = !(distance !== null && total > 0);
      if (Number(sample.time) <= start + .001) progress = 0;
      if (Number(sample.time) >= end - .001 || finished) progress = 1;
      unverified = source.info.reviewed && !source.info.boundariesVerified;
    } else {
      // Never turn an intro, out-lap or arbitrary clip into a fabricated full lap.
      const lap = source.laps.find(lap => sample.time >= lap.start - .001 && sample.time <= lap.end + .001 && (lap.segment == null || sample.source_segment_id == null || lap.segment === sample.source_segment_id));
      if (!lap) return null;
      const observed = !lap.timeOnly && sampleAt(lap.samples, Number(sample.time));
      timeOnly = !observed;
      progress = observed ? (observed.distance - lap.startDistance) / lap.totalDistance : (Number(sample.time) - lap.start) / (lap.end - lap.start);
      if (Number(sample.time) <= lap.start + .001) progress = 0;
      if (Number(sample.time) >= lap.end - .001) progress = 1;
      if (finished && progress < .999) return null;
      unverified = lap.reviewed && !lap.boundariesVerified;
    }
    progress = clamp(progress);
    const finish = Number(state.track.timing_lines?.find(line => line.kind === 'start_finish')?.progress || 0);
    return {progress, fraction: finish + progress, timeOnly, unverified, finished};
  }
  function seekProgress(fraction) {
    const finish = Number(state.track.timing_lines?.find(line => line.kind === 'start_finish')?.progress || 0);
    const wanted = wrap(fraction - finish), side = progressFor('current') ? 'current' : 'reference';
    let best;
    for (const point of state.result.points || []) {
      const progress = progressFor(side, point);
      if (!progress || progress.finished) continue;
      const error = Math.abs(progress.progress - wanted);
      const proximity = Math.abs(point.position - (state.point?.position || 0));
      if (!best || error < best.error - .0001 || (Math.abs(error - best.error) < .0001 && proximity < best.proximity)) best = {error, proximity, position: point.position};
    }
    if (best && best.error < .025) helpers.seek(best.position);
    else {state.mapMessage = 'This map location is outside the selected recorded range.'; render();}
  }
  function trackMismatch() {
    if (!state.track || !state.result) return false;
    const sessions = Object.values(state.result.sessions || {}).filter(Boolean);
    return ['current', 'reference'].some(side => {
      if (!state.result[side]) return false;
      const session = sessions.find(item => (item.id || item.session_id) === state.ids[side]);
      const context = state.result.context?.[side] || session?.context || session?.metadata?.telemetry_context;
      return context?.track_id && context.track_id !== state.track.id;
    });
  }
  function mapped() {
    if (!state.track || trackMismatch() || !state.result) return false;
    return sides.some(side => progressFor(side));
  }
  function render() {
    if (!helpers) return;
    const mismatch = trackMismatch();
    const ranges = ['current', 'reference'].filter(side => state.result?.[side] || state.result?.source_review?.[side]);
    for (const side of ['current', 'reference']) $(`track${side}Video`).hidden = !ranges.includes(side);
    document.querySelector('.track-video-pair').classList.toggle('single-recording', ranges.length === 1);
    $('trackLength').textContent = state.track ? `${Math.round(state.track.length_m).toLocaleString()} m / nominal lap` : '-- m';
    $('trackDetectionStatus').textContent = state.detectionMessage;
    $('trackDetectionStatus').hidden = !state.detectionMessage;
    renderCursor();
    let status = !state.result ? 'Select a recording and range for review.' : !state.track ? 'Choose a circuit, or select a complete lap for automatic detection.' : mapped() ? 'Play or scrub to follow each run around its lap.' : 'Select a lap, or set its start and end, to show automatic lap progress.';
    if (mapped() && ranges.some(side => state.result[side] && !state.result[side].distance_complete)) status += ' Speed gaps may shift the estimated location.';
    if (mapped() && state.result.alignment?.applied) status += ` Distance registered at ${state.result.alignment.anchors.length} paired positions.`;
    if (mapped() && ranges.some(side => state.result[side]?.distance_estimated)) status += ' Brief missing speed observations are estimated.';
    if (state.mapMessage) status = state.mapMessage;
    if (mismatch) status = 'Selected map differs from the recording circuit label. Automatic position is unavailable; select the matching circuit layout. Source frames remain available.';
    $('trackReviewStatus').textContent = status;
    const credit = $('trackCredit');
    credit.replaceChildren();
    if (state.track) {
      for (const [label, source] of [['Geometry', state.track.source], ['Labels', state.track.annotation_source]]) {
        if (!source) continue;
        const row = document.createElement('p'), link = document.createElement('a');
        link.href = source.file_url || source.url;
        link.textContent = source.name || `${label} source`;
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        row.append(`${label}: `, link, document.createTextNode(source.license ? ` / ${source.license}` : ''));
        if (source.attribution) row.append(document.createElement('br'), document.createTextNode(source.attribution));
        credit.append(row);
      }
    }
    const hasTiming = state.track?.timing_lines?.some(line => line.kind === 'start_finish');
    $('trackAnnotationNote').textContent = state.track ? (state.track.annotations?.note || state.track.annotation_note ||
      `${hasTiming ? 'Lap progress begins and ends at the circuit start / finish line.' : 'Timing-line positions are not available for this layout.'} ${state.track.annotation_positions_kind?.includes('approximate') ? 'Corner and timing positions are approximate projections of source lap markers.' : 'Corner and straight labels refer to this diagram.'}`) : 'Select a circuit to view its sources.';
    document.querySelector('.track-legend-finish').hidden = !hasTiming;
    document.querySelector('.track-legend-sector').hidden = !state.track?.timing_lines?.some(line => line.kind === 'sector');
    preparePace(); draw();
  }
  function preparePace() {
    const key = [state.result, state.track];
    if (state.paceKey?.every((value, index) => value === key[index])) return;
    state.paceKey = key;
    state.pace = null;
    hidePaceTip();
    const legend = $('trackPaceLegend'), status = $('trackPaceStatus'), canvas = $('trackMap');
    legend.hidden = true; status.hidden = true;
    for (const winner of Object.keys(paceColors)) canvas.dataset['pace' + winner[0].toUpperCase() + winner.slice(1)] = '0';
    if (!state.track || !state.result?.current || !state.result.reference || trackMismatch()) return;
    const passages = {};
    for (const side of sides) {
      passages[side] = (state.result.points || []).map(point => {
        const sample = point[side], mapped = sample && progressFor(side, point);
        if (!mapped || mapped.timeOnly || mapped.finished || sample.distance_segment_id === null) return null;
        const progress = mapped.progress;
        return {progress, time: Number(sample.time), segment: sample.distance_segment_id ?? sample.segment_id};
      });
    }
    const bins = window.ACCCTrackPace.build(passages.current, passages.reference);
    const counts = Object.fromEntries(Object.keys(paceColors).map(winner => [winner, bins.filter(bin => bin.winner === winner).length]));
    for (const [winner, count] of Object.entries(counts)) canvas.dataset['pace' + winner[0].toUpperCase() + winner.slice(1)] = count;
    canvas.dataset.paceBuilds = Number(canvas.dataset.paceBuilds || 0) + 1;
    legend.hidden = false; status.hidden = false;
    for (const side of sides) {
      const label = side === 'current' ? 'Your run' : 'Reference';
      const item = $(side === 'current' ? 'trackPaceCurrent' : 'trackPaceReference');
      item.textContent = `${label} faster`;
    }
    const available = bins.length - counts.unavailable;
    status.textContent = available ? 'Local time gained · hover the colored track for section times.' : 'Select a lap, or set its start and end, for each run to compare circuit sections.';
    const origin = Number(state.track.timing_lines?.find(line => line.kind === 'start_finish')?.progress || 0);
    const direction = 1;
    // Cache paths in circuit coordinates. Playback and resizing only paint them.
    const vertices = [];
    for (const point of state.track.points) for (let lap = -1; lap <= 2; lap++) {
      const progress = (point.distance_m / state.track.length_m + lap - origin) * direction;
      if (progress > 0 && progress < 1) vertices.push({progress, x: point.x, y: point.y});
    }
    vertices.sort((a, b) => a.progress - b.progress);
    const paths = Object.fromEntries(Object.keys(paceColors).map(winner => [winner, new Path2D()]));
    let cursor = 0;
    for (const bin of bins) {
      const path = paths[bin.winner], first = pathPoint(origin + direction * bin.start), last = pathPoint(origin + direction * bin.end);
      path.moveTo(first.x, first.y);
      while (cursor < vertices.length && vertices[cursor].progress <= bin.start) cursor++;
      while (cursor < vertices.length && vertices[cursor].progress < bin.end) {const point = vertices[cursor++]; path.lineTo(point.x, point.y);}
      path.lineTo(last.x, last.y);
    }
    state.pace = {bins, paths, origin, direction};
  }
  function hidePaceTip() {if ($('trackPaceTip')) $('trackPaceTip').hidden = true;}
  function hoverPace(event) {
    const nearest = nearestPosition(event), pace = state.pace, tip = $('trackPaceTip');
    if (!nearest || !pace) {hidePaceTip(); return;}
    const progress = wrap((nearest.fraction - pace.origin) * pace.direction);
    const bin = pace.bins[Math.min(pace.bins.length - 1, Math.floor(progress * pace.bins.length))];
    const label = bin.winner === 'unavailable' ? 'No comparison in this section' : bin.winner === 'equal' ? 'Even pace · within 0.010 s' : `${bin.winner === 'current' ? 'Your run' : 'Reference'} gains ${Math.abs(bin.gain).toFixed(3)} s here`;
    const details = bin.winner === 'unavailable' ? '' : `\nYour run ${bin.currentTime.toFixed(3)} s · Reference ${bin.referenceTime.toFixed(3)} s`;
    tip.textContent = `${label}\n${(bin.start * 100).toFixed(1)}–${(bin.end * 100).toFixed(1)}% of lap${details}`;
    tip.dataset.winner = bin.winner;
    tip.style.borderColor = paceColors[bin.winner];
    tip.hidden = false;
    const box = $('trackMap').getBoundingClientRect();
    tip.style.left = `${Math.max(5, Math.min(box.width - tip.offsetWidth - 5, event.clientX - box.left + 12))}px`;
    const y = event.clientY - box.top;
    tip.style.top = `${Math.max(5, Math.min(box.height - tip.offsetHeight - 5, y > tip.offsetHeight + 20 ? y - tip.offsetHeight - 12 : y + 15))}px`;
  }
  function pathPoint(fraction) {
    const points = state.track.points, distance = wrap(fraction) * state.track.length_m;
    let lo = 0, hi = points.length - 1;
    while (lo < hi) {const mid = Math.ceil((lo + hi) / 2); if (points[mid].distance_m <= distance) lo = mid; else hi = mid - 1;}
    const a = points[lo], b = points[Math.min(lo + 1, points.length - 1)];
    const f = b.distance_m > a.distance_m ? (distance - a.distance_m) / (b.distance_m - a.distance_m) : 0;
    return {x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f};
  }
  function draw() {
    const canvas = $('trackMap');
    if (!canvas) return;
    const box = canvas.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    const width = Math.round(box.width * ratio), height = Math.round(box.height * ratio);
    if (canvas.width !== width || canvas.height !== height) {canvas.width = width; canvas.height = height;}
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, box.width, box.height);
    const size = Math.min(box.width, box.height) - 40;
    const left = (box.width - size) / 2, top = (box.height - size) / 2;
    state.projection = {left, top, size};
    ctx.fillStyle = '#92a39a'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
    for (const side of sides) $(markerId(side)).hidden = true;
    if (!state.track) {
      $('trackAnnotations').replaceChildren();
      state.annotationTrack = null;
      ctx.fillText('Select a circuit to explore', box.width / 2, box.height / 2);
      return;
    }
    const pixel = point => ({x: left + point.x * size, y: top + point.y * size});
    ctx.strokeStyle = '#748b80'; ctx.lineWidth = 3; ctx.lineJoin = 'round';
    ctx.beginPath();
    state.track.points.forEach((point, index) => {const p = pixel(point); if (index) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y);});
    ctx.stroke();
    if (state.pace) {
      ctx.save(); ctx.translate(left, top); ctx.scale(size, size);
      ctx.lineWidth = 6 / size; ctx.lineCap = 'butt';
      for (const [winner, path] of Object.entries(state.pace.paths)) {ctx.strokeStyle = paceColors[winner]; ctx.stroke(path);}
      ctx.restore();
    }
    for (const line of state.track.timing_lines || []) {
      if (!finite(line.progress)) continue;
      const center = pixel(pathPoint(Number(line.progress)));
      const a = pixel(pathPoint(Number(line.progress) - .002)), b = pixel(pathPoint(Number(line.progress) + .002));
      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      ctx.save(); ctx.translate(center.x, center.y); ctx.rotate(angle);
      if (line.kind === 'start_finish') {
        ctx.fillStyle = '#f2f5ec'; ctx.fillRect(-4, -13, 8, 26);
        ctx.fillStyle = '#18231e';
        for (let row = 0; row < 6; row++) ctx.fillRect(row % 2 ? 0 : -4, -12 + row * 4, 4, 4);
      } else {
        ctx.strokeStyle = '#c5abed'; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.moveTo(0, -12); ctx.lineTo(0, 12); ctx.stroke();
      }
      ctx.restore();
    }
    drawAnnotations(pixel, box);
    ctx.strokeStyle = '#46614e'; ctx.lineWidth = .75; ctx.setLineDash([2, 3]);
    for (const leader of state.annotationLeaders || []) {
      if (Math.hypot(leader.x - leader.point.x, leader.y - leader.point.y) < 29) continue;
      ctx.beginPath(); ctx.moveTo(leader.point.x, leader.point.y); ctx.lineTo(leader.x, leader.y); ctx.stroke();
    }
    ctx.setLineDash([]);
    if (mapped()) {
      const cars = sides.map(side => ({side, progress: progressFor(side)})).filter(car => car.progress);
      for (const car of cars) {
        const {side, progress} = car, p = pixel(pathPoint(progress.fraction));
        const before = pixel(pathPoint(progress.fraction - .001)), after = pixel(pathPoint(progress.fraction + .001));
        const angle = Math.atan2(after.y - before.y, after.x - before.x);
        const marker = $(markerId(side));
        // Keep both cars individually visible when they occupy the same point.
        const overlap = cars.length === 2 && Math.hypot(p.x - pixel(pathPoint(cars[1 - cars.indexOf(car)].progress.fraction)).x, p.y - pixel(pathPoint(cars[1 - cars.indexOf(car)].progress.fraction)).y) < 23;
        const lane = overlap ? (side === 'current' ? -7 : 7) : 0;
        marker.style.left = `${p.x - Math.sin(angle) * lane}px`; marker.style.top = `${p.y + Math.cos(angle) * lane}px`;
        marker.querySelector('svg').style.transform = `rotate(${angle}rad)`;
        marker.dataset.progress = progress.progress;
        marker.dataset.x = pathPoint(progress.fraction).x; marker.dataset.y = pathPoint(progress.fraction).y;
        const caption = `${side === 'current' ? 'Your run' : 'Reference'}\n${(progress.progress * 100).toFixed(1)}% of lap${progress.timeOnly ? ' · estimated from time' : ''}${progress.unverified ? '\nUnverified lap boundaries' : ''}${progress.finished ? '\nLap finished' : ''}`;
        marker.setAttribute('aria-label', caption);
        const tip = marker.querySelector('.track-annotation-tip'); tip.textContent = caption;
        positionTip(tip, p.x, box.width); marker.hidden = false;
      }
    }
  }

  function drawAnnotations(pixel, box) {
    const key = `${box.width}:${box.height}`;
    if (state.annotationTrack === state.track && state.annotationSize === key) return;
    state.annotationTrack = state.track; state.annotationSize = key;
    const layer = $('trackAnnotations');
    layer.replaceChildren();
    layer.dataset.trackId = state.track.id;
    const occupied = [], entries = [];
    const items = [...(state.track.corners || []).map(item => ({...item, type: 'corner'})),
      ...(state.track.straights || []).map(item => ({...item, type: 'straight'})),
      ...(state.track.timing_lines || []).map(item => ({...item, type: 'timing'}))];
    for (const item of items) {
      if (!finite(item.progress)) continue;
      const point = pixel(pathPoint(Number(item.progress)));
      const origin = pixel({x: .5, y: .5});
      const length = Math.hypot(point.x - origin.x, point.y - origin.y) || 1;
      let x = point.x + (point.x - origin.x) / length * 20;
      let y = point.y + (point.y - origin.y) / length * 20;
      if (item.type === 'corner') {
        for (let attempt = 0; attempt < 5 && occupied.some(other => Math.hypot(other.x - x, other.y - y) < 24); attempt++) y += y < box.height / 2 ? -13 : 13;
        occupied.push({x, y});
      }
      x = Math.max(item.type === 'straight' ? 55 : 16, Math.min(box.width - (item.type === 'straight' ? 55 : 16), x));
      y = Math.max(16, Math.min(box.height - 16, y));
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `track-annotation track-${item.type}`;
      const numbered = item.number !== null && item.number !== undefined && item.number !== '';
      if (item.type === 'corner' && !numbered) button.classList.add('track-complex');
      button.dataset.kind = item.type === 'timing' ? item.kind : item.type;
      button.dataset.progress = item.progress;
      button.style.left = `${x}px`; button.style.top = `${y}px`;
      const turn = `Turn ${item.number}`;
      const label = item.type === 'corner' ? numbered ? `${turn}${item.name && item.name !== turn ? ' · ' + item.name : ''}` : item.name || 'Corner section' : item.label || item.name || (item.kind === 'start_finish' ? 'Start / finish' : `Sector ${item.sector || ''}`);
      button.append(document.createTextNode(item.type === 'corner' ? numbered ? String(item.number) : item.abbreviation || item.name || 'Section' : item.type === 'timing' ? item.kind === 'start_finish' ? 'S/F' : `S${item.sector || ''}` : item.name));
      const tip = document.createElement('span');
      tip.className = 'track-annotation-tip';
      tip.textContent = label;
      positionTip(tip, x, box.width);
      button.append(tip);
      button.setAttribute('aria-label', tip.textContent);
      button.onclick = event => {
        event.stopPropagation();
        if (trackMismatch()) return;
        if (mapped()) seekProgress(Number(item.progress));
      };
      layer.append(button);
      entries.push({button, tip, point, x, y, type: item.type, numbered});
    }
    // Place labels in nearby free space while retaining a leader to the source
    // marker. Timing lines get first choice; long straight names follow turns.
    const order = entry => entry.type === 'timing' ? 0 : entry.type === 'corner' && entry.numbered ? 1 : entry.type === 'straight' ? 2 : 3;
    const placed = [];
    for (const entry of entries.sort((a, b) => order(a) - order(b))) {
      const width = entry.button.offsetWidth, height = entry.button.offsetHeight;
      let best = null;
      for (const radius of [0, 14, 28, 42, 60, 80, 105]) {
        for (let index = 0; index < (radius ? 16 : 1); index++) {
          const angle = index * Math.PI / 8;
          const x = Math.max(width / 2 + 4, Math.min(box.width - width / 2 - 4, entry.x + Math.cos(angle) * radius));
          const y = Math.max(height / 2 + 4, Math.min(box.height - height / 2 - 4, entry.y + Math.sin(angle) * radius));
          let overlap = 0;
          for (const other of placed) {
            const w = (width + other.width) / 2 + 3 - Math.abs(x - other.x);
            const h = (height + other.height) / 2 + 3 - Math.abs(y - other.y);
            if (w > 0 && h > 0) overlap += w * h;
          }
          const cost = overlap * 1000 + (x - entry.x) ** 2 + (y - entry.y) ** 2;
          if (!best || cost < best.cost) best = {x, y, width, height, cost};
        }
        if (best.cost < radius * radius) break;
      }
      entry.button.style.left = `${best.x}px`; entry.button.style.top = `${best.y}px`;
      positionTip(entry.tip, best.x, box.width);
      placed.push({...best, point: entry.point});
    }
    state.annotationLeaders = placed;
  }
  function positionTip(tip, x, width) {
    tip.style.left = x < 115 ? '0' : x > width - 115 ? 'auto' : '50%';
    tip.style.right = x > width - 115 ? '0' : 'auto';
    tip.style.transform = x < 115 || x > width - 115 ? 'none' : 'translateX(-50%)';
  }
  function nearestPosition(event) {
    if (!state.track || !state.projection || trackMismatch()) return null;
    const box = $('trackMap').getBoundingClientRect(), p = state.projection;
    const target = {x: (event.clientX - box.left - p.left) / p.size, y: (event.clientY - box.top - p.top) / p.size};
    let best = null;
    for (let index = 1; index < state.track.points.length; index++) {
      const a = state.track.points[index - 1], b = state.track.points[index];
      const dx = b.x - a.x, dy = b.y - a.y;
      const f = Math.max(0, Math.min(1, ((target.x - a.x) * dx + (target.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
      const squared = (a.x + dx * f - target.x) ** 2 + (a.y + dy * f - target.y) ** 2;
      if (!best || squared < best.squared) best = {squared, fraction: (a.distance_m + (b.distance_m - a.distance_m) * f) / state.track.length_m};
    }
    return best && Math.sqrt(best.squared) * p.size <= 24 ? best : null;
  }
  function choosePosition(event) {
    const best = nearestPosition(event);
    if (!best) return;
    state.mapMessage = null;
    if (mapped()) seekProgress(best.fraction);
  }
  function seekDistance(distance) {
    const end = state.result?.extent?.[1] || 0;
    if (distance < 0 || distance > end) {
      state.mapMessage = 'This map location is outside the selected recorded range.'; render(); return;
    }
    state.mapMessage = null; helpers.seek(distance);
  }
  return {init, setComparison, setCursor, setFrame, setPlayback};
})();
