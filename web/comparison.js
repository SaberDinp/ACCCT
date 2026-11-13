/* Independent recordings share a coordinate axis, never an assumed racing line. */
'use strict';
window.ACCCComparison = (() => {
  const $ = id => document.getElementById(id);
  const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  } [c]));
  const number = (value, digits = 0) => finite(value) ? Number(value).toFixed(digits) : '--';
  const sourceTime = value => Number(Number(value).toFixed(3));
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const stamp = value => finite(value) ? `${Math.floor(value / 60)}:${(value % 60).toFixed(2).padStart(5, '0')}` : '--:--';
  const names = {
    current: 'Your run',
    reference: 'Reference'
  };
  const runStyles = {
    current: {color: '#6dccdf', dash: []},
    reference: {color: '#e0bf79', dash: [6, 4]}
  };
  const channelSpecs = [{
    key: 'speed',
    label: 'SPEED',
    unit: 'km/h',
    color: '#6dccdf'
  }, {
    key: 'brake',
    label: 'BRAKE',
    unit: '%',
    color: '#ff6d61'
  }, {
    key: 'throttle',
    label: 'THROTTLE',
    unit: '%',
    color: '#66dba3'
  }, {
    key: 'gear',
    label: 'GEAR',
    unit: '',
    color: '#c5cfd1'
  }, {
    key: 'steering',
    label: 'STEERING',
    unit: 'HUD %',
    color: '#e0bf79'
  }, {
    key: 'delta_time_s',
    label: 'TIME DELTA',
    unit: 's',
    color: '#d5e991'
  }];
  const state = {
    sessions: [],
    runs: {
      current: {},
      reference: {}
    },
    axis: 'distance',
    alignmentMode: 'raw',
    landmarks: [],
    result: null,
    extent: [0, 1],
    zoom: null,
    cursor: null,
    drag: null,
    jobs: [],
    activeJob: null,
    lastRefresh: 0,
    request: 0,
    exportRevision: 0,
    exportResultRevision: null,
    libraryEpoch: 0,
    deletedSessions: new Set(),
    importing: false,
    playback: {playing: false, frame: null, clock: [], lastPaint: 0, lastPreview: 0}
  };
  let helpers, ready = false,
    compareTimer = null,
    previewTimer = null;

  const selectedSides = () => ['current', 'reference'].filter(side => state.runs[side].id && !state.runs[side].loading);
  const isPaired = () => selectedSides().length === 2;
  const visibleChannels = () => channelSpecs.filter(spec => spec.key !== 'delta_time_s' || isPaired());
  const notifySelectedRecording = () => helpers.onCurrentLoaded?.(state.runs.current.data || state.runs.reference.data || null);

  function sourceMarkup(side) {
    return `<div class="comparison-run"><div class="run-label"><span class="run-line ${side === 'reference' ? 'reference' : ''}"></span>${names[side].toUpperCase()}<span class="tag" id="${side}RecordingStatus">NO RECORDING</span></div><details class="run-video-options" open><summary>Video options</summary><div class="run-video-options-content"><button class="button subtle compact" id="${side}ImportRecording"><i data-lucide="video"></i>Import recording</button><select id="${side}Session" aria-label="${names[side]} saved recording"><option value="">Select a recording</option></select><a class="button subtle compact disabled" id="${side}ExportCsv" aria-label="Export ${names[side].toLowerCase()} CSV" aria-disabled="true"><i data-lucide="download"></i>Export CSV</a><button class="icon-button run-delete" id="${side}DeleteSession" title="Delete recording" aria-label="Delete ${names[side].toLowerCase()} recording" disabled><i data-lucide="trash-2"></i></button></div></details><div class="run-boundary-actions">${['Start', 'End'].map(edge => `<button class="button subtle lap-boundary-button inspect-boundary" id="${side}${edge}Boundary" data-side="${side}" data-edge="${edge}" disabled><span>${edge} of lap</span><small id="${side}${edge}BoundaryTime">Not selected</small></button>`).join('')}</div><div class="run-detail"><span id="${side}Detail">No recording selected</span></div><input type="hidden" id="${side}Range"><input type="hidden" id="${side}Start"><input type="hidden" id="${side}End"><div class="run-thumbnail" hidden><img id="${side}Frame" alt="${names[side]} source frame" hidden><span id="${side}FrameEmpty">NO FRAME</span><span id="${side}FrameTime" hidden></span></div></div>`;
  }

  function alignmentMarkup() {
    return `<p class="lap-boundary-hint" id="lapBoundaryHint" hidden>Lock the start of each lap to confirm time alignment.</p><div class="comparison-legend" aria-label="Lap legend"><div><span class="run-line current"></span><span><strong id="comparisonLegendCurrent">Your run</strong><small>YOUR RUN / SOLID</small></span></div><div><span class="run-line reference"></span><span><strong id="comparisonLegendReference">Reference</strong><small>REFERENCE / DASHED</small></span></div><span class="delta-sign-note">Delta + slower / - faster</span></div>`;
  }

  function init(context) {
    if (ready) return;
    helpers = context;
    $('referenceWorkspace').innerHTML = `<div class="reference-header"><h2>Reference comparison</h2><div class="reference-header-actions"><button class="icon-button" id="refreshComparisons" title="Refresh recordings" aria-label="Refresh recordings"><i data-lucide="refresh-cw"></i></button></div></div><div class="import-status-strip" id="activeImport" hidden><i data-lucide="scan-line"></i><div class="import-status-copy"><strong id="activeImportName">Reference import</strong><small id="activeImportDetail"></small><div class="import-progress"><span id="activeImportProgress"></span></div></div><span class="import-percent" id="activeImportPercent"></span><button class="icon-button" id="cancelActiveImport" title="Cancel import" aria-label="Cancel import"><i data-lucide="x"></i></button></div><div class="run-pair">${sourceMarkup('current')}${sourceMarkup('reference')}</div><div class="compare-toolbar"><div class="compare-toolbar-left"><div class="axis-segments" role="group" aria-label="Comparison axis"><button id="axisDistance" class="active" aria-pressed="true">Distance</button><button id="axisElapsed" aria-pressed="false">Time</button></div></div><div class="compare-toolbar-right"><span class="compare-unit-note" id="compareAxisNote">Estimated distance / metres</span><button class="icon-button" id="compareZoomOut" title="Zoom out" aria-label="Zoom out"><i data-lucide="zoom-out"></i></button><button class="icon-button" id="compareZoomIn" title="Zoom in" aria-label="Zoom in"><i data-lucide="zoom-in"></i></button><button class="icon-button" id="compareZoomReset" title="Reset zoom" aria-label="Reset zoom"><i data-lucide="maximize-2"></i></button><button class="button primary compact" id="runComparison"><i data-lucide="chart-no-axes-combined"></i>Compare</button></div></div><div class="comparison-empty-status" id="comparisonStatus" role="status">Select two recordings to compare.</div><div class="comparison-body"><canvas id="synchronizedChart" class="synchronized-chart" aria-label="Synchronized speed, brake, throttle, gear, steering and time delta traces" tabindex="0"></canvas><aside class="comparison-inspector"><div class="cursor-heading"><span>CURSOR</span><strong id="comparisonCursor">-- m</strong></div><table class="cursor-table"><thead><tr><th>Channel</th><th>Your</th><th>Ref.</th><th>Diff.</th></tr></thead><tbody id="comparisonValues"></tbody></table><div class="section-bar"><h2>Channel coverage</h2></div><div class="compare-channel-availability" id="comparisonAvailability"></div><div class="comparison-warning-list" id="comparisonWarnings"></div></aside></div><div class="compare-summary-line"><span id="comparisonCurrentSummary">YOUR RUN <strong>--</strong></span><span id="comparisonDeltaSummary">DELTA <strong>Awaiting alignment</strong></span><span id="comparisonReferenceSummary">REFERENCE <strong>--</strong></span></div>`;
    $('referenceWorkspace').querySelector('.comparison-empty-status').insertAdjacentHTML('beforebegin', alignmentMarkup());
    $('refreshComparisons').insertAdjacentHTML('beforebegin', '<button class="icon-button" id="swapComparisonRuns" title="Swap your run and reference" aria-label="Swap your run and reference" disabled><i data-lucide="arrow-left-right"></i></button>');
    state.importKind = 'file';
    const dialog = document.createElement('dialog');
    dialog.id = 'referenceImportDialog';
    dialog.innerHTML = `<div class="dialog-header"><div><span class="eyebrow">INDEPENDENT VIDEO ANALYSIS</span><h2>Import recording</h2></div><button class="icon-button" id="closeReferenceImport" title="Close" aria-label="Close recording import"><i data-lucide="x"></i></button></div><label class="field-label">Use this recording as<select id="recordingImportRole"><option value="current">Your run</option><option value="reference">Reference lap</option></select></label><div class="dialog-tabs" role="tablist"><button class="active" data-import-kind="file">Local recording</button><button data-import-kind="youtube">Video link</button></div><div class="import-kind-pane" id="import-url"><label class="field-label">Recorded video URL<input id="referenceUrl" type="url" placeholder="YouTube, Twitch VOD / clip, or video-file URL" autocomplete="off" aria-describedby="referenceUrlHint"></label><p class="reference-url-hint" id="referenceUrlHint">Import a completed recording: YouTube video, Twitch VOD or clip, or a direct video file. Live streams are not supported.</p></div><div class="import-kind-pane active" id="import-file"><label class="field-label">Recording<select id="referenceLocalFile"><option value="">Select a local recording</option></select></label><button class="button subtle compact" id="referenceUpload"><i data-lucide="upload"></i>Browse files</button><input id="referenceUploadInput" type="file" accept="video/*,.mkv" hidden></div><label class="field-label reference-name-input">Recording name<input id="referenceName" type="text" placeholder="My lap" maxlength="100"></label><button class="button primary full-width" id="startReferenceImport"><i data-lucide="download"></i>Import and analyze</button><div class="dialog-error" id="referenceImportError" role="alert"></div><div class="import-history" id="referenceImportHistory"></div>`;
    document.body.appendChild(dialog);
    const frameDialog = document.createElement('dialog');
    frameDialog.id = 'alignmentDialog';
    frameDialog.innerHTML = '<div class="dialog-header"><div><span class="eyebrow" id="alignmentSide">YOUR RUN</span><h2 id="alignmentTitle">Selecting start of lap</h2></div><button class="icon-button" id="closeAlignment" title="Close" aria-label="Close lap selection"><i data-lucide="x"></i></button></div><p class="lap-boundary-instruction">Choose the same start/finish line in both recordings.</p><div class="alignment-image"><video id="alignmentVideo" controls muted playsinline preload="metadata" hidden></video><img id="alignmentImage" alt="Source frame for lap boundary selection"></div><div class="alignment-controls"><button class="icon-button" id="alignmentFrameBack" title="Back one frame" aria-label="Back one frame"><i data-lucide="chevrons-left"></i></button><button class="icon-button" id="alignmentBack" title="Back one second" aria-label="Back one second"><i data-lucide="chevron-left"></i></button><label class="range-time">Time <input id="alignmentTime" type="number" min="0" step="0.001" aria-label="Lap boundary source time">s</label><button class="icon-button" id="alignmentForward" title="Forward one second" aria-label="Forward one second"><i data-lucide="chevron-right"></i></button><button class="icon-button" id="alignmentFrameForward" title="Forward one frame" aria-label="Forward one frame"><i data-lucide="chevrons-right"></i></button><span id="alignmentFrameStamp" class="subtle-text"></span><button class="button primary" id="alignmentLock" disabled><i data-lucide="lock-keyhole"></i>Lock</button></div><p class="dialog-error" id="alignmentError" role="alert"></p>';
    document.body.appendChild(frameDialog);
    try {
      // Keep existing recording selections when upgrading from the old name.
      const key = 'accc.comparison.v1', desktop = window.ACCCDesktopPreferences;
      let value;
      try {value = desktop?.getItem(key);} catch { /* Browser fallback below. */ }
      value ??= localStorage.getItem(key) ?? localStorage.getItem('apex.comparison.v1');
      state.saved = JSON.parse(value ?? '{}');
      try {if (value !== null) desktop?.setItem(key, value);} catch { /* Keep the restored choices. */ }
    } catch {
      state.saved = {};
    }
    state.axis = state.saved.axis === 'elapsed' ? 'elapsed' : 'distance';
    state.alignmentMode = 'raw';
    state.landmarks = [];
    for (const axis of ['distance', 'elapsed']) {
      const button = $(`axis${axis === 'distance' ? 'Distance' : 'Elapsed'}`);
      button.classList.toggle('active', axis === state.axis);
      button.setAttribute('aria-pressed', String(axis === state.axis));
    }
    bind();
    updateRegistration();
    ready = true;
    window.ACCCTrackReview?.init({api: helpers.api, icons: helpers.icons,
      trackId: () => {
        const tracks = selectedSides().map(side => recordingContext(side).track_id).filter(Boolean);
        return tracks.length && tracks.every(id => id === tracks[0]) ? tracks[0] : null;
      }, rangeInfo, pointAt, sourceSamples: side => state.runs[side]?.data?.samples || [],
      playback: {toggle: togglePlayback, pause: pausePlayback, state: playbackState}, seek: position => {
      if (!state.result) return;
      pausePlayback();
      state.cursor = clamp(position, state.extent[0], state.extent[1]);
      draw(); updateCursor();
    }, inspect: (side, time) => {
      if (!state.runs[side]?.id) return;
      openFrame(side, time);
    }});
    helpers.icons();
    renderMetadata();
    updateCursor();
    $('comparisonStatus').textContent = 'Select a recording.';
    $('runComparison').disabled = true;
    for (const button of document.querySelectorAll('.inspect-boundary')) button.disabled = true;
    notifySelectedRecording();
    draw();
    refreshJobs();
    setInterval(refreshJobs, 1800);
  }

  async function refresh(force = false) {
    if (!ready || (!force && Date.now() - state.lastRefresh < 3000)) return;
    if (state.refreshing) {
      if (force) state.refreshQueued = true;
      return;
    }
    state.refreshing = true;
    const epoch = state.libraryEpoch;
    state.lastRefresh = Date.now();
    try {
      const response = await helpers.api('/api/sessions');
      if (epoch !== state.libraryEpoch) return;
      state.sessions = (Array.isArray(response) ? response : response.sessions || []).filter(session => !state.deletedSessions.has(session.id || session.session_id));
      const available = new Set(state.sessions.map(session => session.id || session.session_id));
      const removed = ['current', 'reference'].filter(side => state.runs[side].id && !available.has(state.runs[side].id));
      const staleSaved = !state.restored ? ['current', 'reference'].filter(side => state.saved[side]?.id && !available.has(state.saved[side].id)) : [];
      if (removed.length) {
        const ids = [...new Set(removed.map(side => state.runs[side].id))];
        for (const id of ids) await removeSession(id);
        return;
      }
      if (staleSaved.length === 2) {
        persistPreferences({});
        state.alignmentMode = 'raw';
        resetAlignment(true);
      } else if (staleSaved.length) {
        const preferences = {...state.saved, align_start: false, align_end: false, landmarks: []};
        for (const side of staleSaved) {
          state.deletedSessions.add(state.saved[side].id);
          preferences[side] = {id: ''};
        }
        persistPreferences(preferences);
        resetAlignment(true);
      }
      renderSessionOptions();
      if (state.restored) {
        const updated = ['current', 'reference'].filter(side => {
          const run = state.runs[side];
          const session = state.sessions.find(item => item.id === run.id);
          return session && String(session.metadata?.comparison_defaults?.revision || '') !== (run.defaultsRevision || '');
        });
        if (updated.length) {
          state.restoring = true;
          try {
            for (const side of updated) {
              if (epoch !== state.libraryEpoch) break;
              await loadRun(side, state.runs[side].id, true);
            }
          } finally {
            state.restoring = false;
            if (epoch === state.libraryEpoch) savePreferences();
          }
        }
      }
      if (epoch !== state.libraryEpoch) return;
      if (!state.restored) {
        state.restored = true;
        state.restoring = true;
        for (const side of ['current','reference']) $(`${side}Session`).disabled = true;
        try {
          for (const side of ['current', 'reference']) {
            if (epoch !== state.libraryEpoch) break;
            const saved = state.saved[side];
            const isReference = session => (session.metadata?.role ?? session.source?.role) === 'reference';
            const liveId = helpers.currentSessionId();
            const id = saved?.id === '' ? '' : saved?.id && available.has(saved.id) ? saved.id : side === 'current' ? state.sessions.find(session => session.metadata?.role === 'current')?.id || (available.has(liveId) ? liveId : null) || state.sessions.find(session => !isReference(session))?.id : state.sessions.find(isReference)?.id;
            await loadRun(side, id || '', true);
          }
        } finally {
          state.restoring = false;
          for (const side of ['current','reference']) $(`${side}Session`).disabled = false;
          if (epoch === state.libraryEpoch) savePreferences();
        }
      }
    } catch (error) {
      if (epoch === state.libraryEpoch) $('comparisonStatus').textContent = error.message;
    } finally {
      state.refreshing = false;
      updateSessionActions();
      if (state.refreshQueued) {
        state.refreshQueued = false;
        refresh(true);
      }
    }
  }

  function renderSessionOptions() {
    for (const side of ['current', 'reference']) {
      const select = $(`${side}Session`), value = select.value;
      const signature = state.sessions.map(session => `${session.id || session.session_id}:${session.metadata?.comparison_defaults?.revision || ''}`).join('|');
      if (select.dataset.signature === signature) continue;
      select.innerHTML = '<option value="">Select a recording</option>' + state.sessions.map(session => `<option value="${esc(session.id || session.session_id)}">${esc(session.name || session.source?.name || session.source_name || 'Recording')} / ${esc(String(session.created_at || '').slice(0, 10))}</option>`).join('');
      select.value = value;
      select.dataset.signature = signature;
    }
  }

  function updateSessionActions() {
    const loading = Object.values(state.runs).some(run => run.loading);
    $('swapComparisonRuns').disabled = state.restoring || loading || selectedSides().length === 0;
    for (const side of ['current', 'reference']) {
      const run = state.runs[side], available = Boolean(run.id && !run.loading);
      $(`${side}DeleteSession`).disabled = !available || !helpers.deleteSession;
      const link = $(`${side}ExportCsv`);
      link.classList.toggle('disabled', !available);
      link.setAttribute('aria-disabled', String(!available));
      if (available) link.href = `/api/export/${encodeURIComponent(run.id)}.csv`;
      else link.removeAttribute('href');
      for (const edge of ['Start', 'End']) $(`${side}${edge}Boundary`).disabled = !available;
    }
  }

  function swapRuns() {
    if ($('swapComparisonRuns').disabled) return;
    const sides = ['current', 'reference'];
    const snapshots = {};
    for (const side of sides) {
      const run = state.runs[side];
      snapshots[side] = {run, range: $(`${side}Range`).value,
        start: $(`${side}Start`).value, end: $(`${side}End`).value,
        segment: state.runs[side].segment, recordingStatus: $(`${side}RecordingStatus`).textContent,
        detail: $(`${side}Detail`).textContent, detailTitle: $(`${side}Detail`).title};
      clearTimeout(run.frameTimer);
      run.frameAbort?.abort();
      if (run.frameUrl) URL.revokeObjectURL(run.frameUrl);
    }
    state.libraryEpoch++;
    state.restored = true;
    state.restoring = true;
    $('alignmentDialog').close();
    $('alignmentImage').removeAttribute('src');
    state.viewer = null;
    for (const side of sides) {
      const incoming = snapshots[side === 'current' ? 'reference' : 'current'];
      const run = state.runs[side] = {...incoming.run, token: (snapshots[side].run.token || 0) + 1,
        frameBusy: false, frameKey: null, frameUrl: null, frameDecodedTime: null, frameRequested: 0,
        frameTimer: null, frameAbort: null, crossingJob: null, crossings: [], crossingAnchor: null};
      $(`${side}Session`).value = run.id || '';
      $(`${side}Range`).value = incoming.range;
      state.runs[side].segment = incoming.segment;
      $(`${side}Start`).value = incoming.start;
      $(`${side}End`).value = incoming.end;
      for (const field of ['Range', 'Start', 'End']) $(`${side}${field}`).disabled = !run.data?.samples?.length;
      $(`${side}RecordingStatus`).textContent = incoming.recordingStatus;
      $(`${side}Detail`).textContent = incoming.detail;
      $(`${side}Detail`).title = incoming.detailTitle;
      $(`${side}Frame`).hidden = true;
      $(`${side}Frame`).removeAttribute('src');
      $(`${side}FrameEmpty`).hidden = false;
      $(`${side}FrameTime`).hidden = true;
      $(`${side}FrameTime`).textContent = '';
      for (const button of document.querySelectorAll(`.inspect-boundary[data-side="${side}"]`)) button.disabled = !run.id;
    }
    state.landmarks = state.landmarks.map(({label, current_time, reference_time}) => ({label, current_time: reference_time, reference_time: current_time}));
    clearResult();
    notifySelectedRecording();
    state.restoring = false;
    markDirty();
    for (const side of sides) if (state.runs[side].id) updateFrame(side, Number($(`${side}Start`).value));
  }

  async function removeSession(id) {
    if (!ready || !id) return;

    const pendingRestore = !state.restored || state.restoring;
    const affected = ['current', 'reference'].filter(side => state.runs[side].id === id || $(`${side}Session`).value === id);
    const savedAffected = ['current', 'reference'].filter(side => state.saved[side]?.id === id || affected.includes(side));
    const preferences = {...state.saved};
    if (savedAffected.length) {
      Object.assign(preferences, {align_start: false, align_end: false, landmarks: []});
      for (const side of savedAffected) preferences[side] = {id: ''};
    }
    state.deletedSessions.add(id);
    state.libraryEpoch++;
    state.sessions = state.sessions.filter(session => (session.id || session.session_id) !== id);
    state.restoring = true;
    if (savedAffected.length) resetAlignment(true);
    for (const side of affected) loadRun(side, '', true);
    state.restoring = false;
    state.restored = !pendingRestore;
    // Before initial restoration the saved ranges, not the empty controls, are authoritative.
    if (pendingRestore) persistPreferences(preferences);
    else savePreferences();
    if (state.jobs.some(job => job.id === state.pendingImport && job.session_id === id)) state.pendingImport = null;
    renderSessionOptions();
    if (affected.length) markDirty();
    updateSessionActions();
    for (const button of document.querySelectorAll('.import-use')) if (button.dataset.session === id) button.remove();
    await refresh(true);
  }

  function makeRanges(data) {
    const samples = data.samples || [],
      ranges = [];
    for (const [index, lap] of (data.session?.metadata?.reviewed_laps || []).entries()) {
      if (!lap?.id || !finite(lap.start_time) || !finite(lap.end_time) || Number(lap.end_time) <= Number(lap.start_time)) continue;
      const start = Number(lap.start_time), end = Number(lap.end_time);
      const verified = lap.boundaries_verified === true;
      ranges.push({id: String(lap.id), label: `${verified ? 'Reviewed' : 'Saved'} lap ${lap.lap_number || index + 1} / ${stamp(end - start)} / ${verified ? 'verified boundaries' : 'unverified boundaries'}${lap.valid === false ? ' / invalid' : ''}`,
        selection: {start_time: start, end_time: end, ...(lap.segment_id != null ? {segment_id: lap.segment_id} : {})},
        start, end, duration: end - start, lap: true, valid: lap.valid, reviewed: true,
        boundariesVerified: verified, startMarker: lap.start_marker, endMarker: lap.end_marker,
        landmarks: Array.isArray(lap.landmarks) ? lap.landmarks : [],
        timingUncertainty: lap.timing_uncertainty_s, notes: lap.notes,
        segment: lap.segment_id});
    }
    const savedLaps = data.laps || data.session?.laps || [];
    const laps = Array.isArray(data.refined_laps) ? [...data.refined_laps.map((lap, index) => ({...lap, id: lap.lap_id ?? index + 1, source: 'timer_refined'})), ...savedLaps.filter(lap => lap.source !== 'timer')] : savedLaps;
    for (const lap of laps) {
      if (!lap.complete) continue;
      const selection = Number.isInteger(lap.start_sample) && Number.isInteger(lap.end_sample) ? {
        start_index: lap.start_sample,
        end_index: lap.end_sample
      } : {
        start_time: lap.start_time,
        end_time: lap.end_time,
        ...(lap.segment_id != null ? {segment_id: lap.segment_id} : {})
      };
      ranges.push({
        id: `lap-${lap.id}`,
        label: `Lap ${lap.id} / ${stamp(lap.duration)}${lap.source === 'timer_refined' ? ' / corrected timer' : ''}${lap.valid === false ? ' / invalid' : lap.valid == null ? ' / unverified' : ''}`,
        selection,
        start: lap.start_time,
        end: lap.end_time,
        duration: lap.duration,
        lap: true,
        valid: lap.valid
      });
    }
    const groups = new Map();
    samples.forEach((sample, index) => {
      if (sample.segment_id == null || sample.active === false) return;
      const group = groups.get(sample.segment_id);
      if (group) group.end = index;
      else groups.set(sample.segment_id, {
        start: index,
        end: index
      });
    });
    for (const [id, range] of groups) {
      if (range.end <= range.start) continue;
      const start = samples[range.start].time,
        end = samples[range.end].time;
      ranges.push({
        id: `segment-${id}`,
        label: `Segment ${id} / ${stamp(end-start)}`,
        selection: {
          start_index: range.start,
          end_index: range.end
        },
        start,
        end,
        duration: end - start,
        segment: id
      });
    }
    if (samples.length) ranges.push({
      id: 'full',
      label: 'Full recording',
      selection: {
        start_index: 0,
        end_index: samples.length - 1
      },
      start: samples[0].time,
      end: samples.at(-1).time,
      duration: samples.at(-1).time - samples[0].time
    });
    return ranges;
  }

  async function loadRun(side, id, restore = false) {
    if (state.deletedSessions.has(id)) id = '';
    if (!restore) resetAlignment(true);
    const previous = state.runs[side], token = (previous.token || 0) + 1;
    clearTimeout(previous.frameTimer);
    previous.frameAbort?.abort();
    if (previous.frameUrl) URL.revokeObjectURL(previous.frameUrl);
    if (state.viewer?.side === side) {
      $('alignmentDialog').close();
      $('alignmentImage').removeAttribute('src');
      state.viewer = null;
    }
    const run = state.runs[side] = {token, segment: '', loading: Boolean(id)};
    updateSessionActions();
    $(`${side}Session`).value = id;
    for (const field of ['Range', 'Start', 'End']) $(`${side}${field}`).disabled = true;
    for (const edge of ['Start', 'End']) $(`${side}${edge}`).value = '';
    $(`${side}Detail`).textContent = 'No recording selected';
    $(`${side}RecordingStatus`).textContent = 'NO RECORDING';
    $(`${side}Frame`).hidden = true;
    $(`${side}Frame`).removeAttribute('src');
    $(`${side}FrameEmpty`).hidden = false;
    $(`${side}FrameTime`).hidden = true;
    $(`${side}FrameTime`).textContent = '';
    for (const button of document.querySelectorAll(`.inspect-boundary[data-side="${side}"]`)) button.disabled = !id;
    notifySelectedRecording();
    clearResult();
    if (!id) {
      markDirty();
      return;
    }
    $(`${side}Detail`).textContent = 'Loading recording...';
    run.loading = true;
    for (const field of ['Range','Start','End']) $(`${side}${field}`).disabled = true;
    $(`${side}Frame`).hidden = true;
    $(`${side}FrameEmpty`).hidden = false;
    $(`${side}RecordingStatus`).textContent = 'LOADING';
    $('comparisonStatus').textContent = `Loading ${names[side].toLowerCase()} recording...`;
    state.request++;
    try {
      const data = await helpers.api(`/api/sessions/${encodeURIComponent(id)}`);
      if (state.runs[side].token !== token || state.deletedSessions.has(id)) return;
      try {
        const refined = await helpers.api(`/api/sessions/${encodeURIComponent(id)}/lap-candidates`);
        if (Array.isArray(refined.candidates)) data.refined_laps = refined.candidates;
      } catch {
        /* Older servers still expose the saved lap boundaries. */
      }
      if (state.runs[side].token !== token || state.deletedSessions.has(id)) return;
      state.runs[side] = {
        id,
        data,
        segment: '',
        ranges: makeRanges(data),
        defaultsRevision: String(data.session?.metadata?.comparison_defaults?.revision || ''),
        token
      };
      notifySelectedRecording();
      const current = state.runs[side];
      $(`${side}Range`).disabled = !data.samples?.length;
      const configured = data.session?.metadata?.comparison_defaults;
      const preferred = current.ranges.find(range => range.id === configured?.range_id) || current.ranges.find(range => range.lap && range.valid !== false) || current.ranges.filter(range => !range.lap && range.id !== 'full').sort((a, b) => b.duration - a.duration)[0] || current.ranges[0];
      $(`${side}Range`).value = preferred?.id || 'custom';
      const previousPreference = restore && state.saved[side]?.id === id ? state.saved[side] : null;
      const revisionChanged = previousPreference && current.defaultsRevision !== String(previousPreference.defaults_revision || '');
      const saved = revisionChanged ? null : previousPreference;
      if (revisionChanged) {
        state.alignmentMode = 'raw';
        resetAlignment(true);
      }
      if (saved && (saved.range === 'custom' || current.ranges.some(range => range.id === saved.range))) $(`${side}Range`).value = saved.range;
      $(`${side}RecordingStatus`).textContent = 'READY';
      chooseRange(side);
      if (saved?.range === 'custom') {
        $(`${side}Start`).value = saved.start;
        $(`${side}End`).value = saved.end;
        current.segment = saved.segment !== undefined && saved.segment !== null &&
          data.samples.some(sample => String(sample.segment_id) === String(saved.segment)) ? String(saved.segment) : '';
        current.boundaryLocks = {};
        for (const edge of ['Start', 'End']) {
          const value = saved.boundary_locks?.[edge.toLowerCase()];
          if (finite(value) && Number(value) === Number($(`${side}${edge}`).value)) current.boundaryLocks[edge.toLowerCase()] = Number(value);
        }
        updateFrame(side, Number(saved.start));
      }
      applyReviewedAlignment(!restore);
      markDirty();
    } catch (error) {
      if (state.runs[side].token !== token) return;
      run.loading = false;
      $(`${side}Detail`).textContent = error.message;
      markDirty();
    }
  }

  function chooseRange(side) {
    const run = state.runs[side],
      value = $(`${side}Range`).value;
    const range = run.ranges?.find(item => item.id === value);
    for (const edge of ['Start', 'End']) $(`${side}${edge}`).disabled = !run.data?.samples?.length;
    if (range) {
      $(`${side}Start`).value = Number(range.start).toFixed(3);
      $(`${side}End`).value = Number(range.end).toFixed(3);
      state.runs[side].segment = range.segment ?? '';
    }
    const description = value === 'custom' ? 'Lap boundaries selected' : 'Choose the start and end of a lap';
    $(`${side}Detail`).textContent = run.data ? `${run.data.samples.length.toLocaleString()} samples / ${description}` : 'No recording selected';
    $(`${side}Detail`).title = range?.notes || '';
    updateFrame(side, Number($(`${side}Start`).value));
  }

  function applyReviewedAlignment() {
    updateRegistration();
  }

  function resetAlignment() {
    state.alignmentMode = 'raw';
    state.landmarks = [];
    updateRegistration();
  }

  function boundaryLocked(side, edge) {
    const locked = state.runs[side]?.boundaryLocks?.[edge.toLowerCase()];
    return finite(locked) && finite($(`${side}${edge}`).value) && Number(locked) === Number($(`${side}${edge}`).value);
  }

  function alignmentState() {
    const sides = ['current', 'reference'];
    const contexts = sides.map(recordingContext);
    const mismatch = contexts.every(context => context.track_id) && contexts[0].track_id !== contexts[1].track_id;
    const ranges = sides.map(side => state.runs[side].ranges?.find(range => range.id === $(`${side}Range`).value));
    const reviewed = edge => ranges.every(range => range?.reviewed && range.boundariesVerified && range[edge + 'Marker']) && ranges[0][edge + 'Marker'] === ranges[1][edge + 'Marker'];
    return {
      align_start: isPaired() && !mismatch && (sides.every(side => boundaryLocked(side, 'Start')) || reviewed('start')),
      align_end: isPaired() && !mismatch && (sides.every(side => boundaryLocked(side, 'End')) || reviewed('end')),
      confirm_same_track: false, alignment_mode: 'raw', landmarks: []
    };
  }

  function updateRegistration() {
    const alignment = alignmentState();
    $('lapBoundaryHint').hidden = !isPaired() || alignment.align_start;
    for (const side of ['current', 'reference']) {
      for (const edge of ['Start', 'End']) {
        const locked = boundaryLocked(side, edge);
        const value = $(`${side}${edge}`).value;
        $(`${side}${edge}BoundaryTime`).textContent = !state.runs[side].id || !finite(value) ? 'Not selected' : `${stamp(Number(value))}${locked ? ' · Locked' : ''}`;
        $(`${side}${edge}Boundary`).classList.toggle('is-locked', locked);
      }
    }
  }

  function openFrame(side, time, edge = 'Start') {
    const run = state.runs[side];
    if (!run?.id || run.loading) return;
    pausePlayback();
    edge = edge === 'End' ? 'End' : 'Start';
    const bounds = (run.data?.samples || []).reduce((bounds, sample) => {
      const value = Number(sample.time);
      if (Number.isFinite(value)) {bounds.min = Math.min(bounds.min, value); bounds.max = Math.max(bounds.max, value);}
      return bounds;
    }, {min: Infinity, max: -Infinity});
    const min = Number.isFinite(bounds.min) ? Math.max(0, bounds.min) : 0;
    const max = Number.isFinite(bounds.max) ? Math.max(min, bounds.max) : min;
    time = clamp(finite(time) ? sourceTime(time) : min, min, max);
    const viewer = state.viewer = {side, edge, sessionId: run.id, token: run.token, min, max, pendingTime: time};
    $('alignmentTitle').textContent = `Selecting ${edge.toLowerCase()} of lap`;
    $('alignmentSide').textContent = names[side].toUpperCase();
    $('alignmentTime').value = time.toFixed(3);
    $('alignmentTime').min = min;
    $('alignmentTime').max = max;
    $('alignmentError').textContent = '';
    $('alignmentFrameStamp').textContent = 'Loading frame…';
    const image = $('alignmentImage'), video = $('alignmentVideo');
    image.hidden = false;
    if (run.frameUrl) image.src = run.frameUrl;
    else image.removeAttribute('src');
    video.pause();
    video.hidden = true;
    const stillCurrent = () => state.viewer === viewer && state.runs[side].id === viewer.sessionId && $('alignmentDialog').open;
    const syncVideo = () => {
      if (!stillCurrent() || video.seeking || video.readyState < 2) return;
      if (finite(viewer.pendingTime)) {
        const target = clamp(viewer.pendingTime, min, Math.min(max, Math.max(min, video.duration - .001)));
        if (Math.abs(video.currentTime - target) > .005) {
          setFrameReady(false);
          video.currentTime = target;
          return;
        }
        viewer.pendingTime = null;
      }
      if (video.currentTime < min || video.currentTime > max) {
        video.pause();
        video.currentTime = clamp(video.currentTime, min, max);
        return;
      }
      video.hidden = false;
      image.hidden = true;
      $('alignmentTime').value = sourceTime(video.currentTime).toFixed(3);
      $('alignmentFrameStamp').textContent = `Frame ${stamp(video.currentTime)}`;
      setFrameReady(true);
    };
    video.onloadedmetadata = () => {
      if (!stillCurrent()) return;
      const requested = finite(viewer.pendingTime) ? viewer.pendingTime : Number($('alignmentTime').value);
      video.currentTime = clamp(requested, min, Math.min(max, Math.max(min, video.duration - .001)));
    };
    video.onloadeddata = syncVideo;
    video.onseeking = () => {if (stillCurrent()) setFrameReady(false);};
    video.onseeked = syncVideo;
    video.ontimeupdate = syncVideo;
    video.onerror = () => {
      if (!stillCurrent()) return;
      video.hidden = true;
      image.hidden = false;
      updateFrame(side, Number($('alignmentTime').value));
    };
    video.src = `/api/sessions/${encodeURIComponent(run.id)}/video/media`;
    if (!$('alignmentDialog').open) $('alignmentDialog').showModal();
    setFrameReady(false);
    video.load();
    updateFrame(side, time);
  }

  function seekBoundaryFrame(time) {
    const viewer = state.viewer;
    if (!viewer || !finite(time)) return;
    const value = sourceTime(clamp(Number(time), viewer.min, viewer.max));
    viewer.pendingTime = value;
    $('alignmentTime').value = value.toFixed(3);
    $('alignmentError').textContent = '';
    $('alignmentFrameStamp').textContent = 'Loading frame…';
    setFrameReady(false);
    const video = $('alignmentVideo');
    video.pause();
    if (video.readyState >= 1) video.currentTime = Math.min(value, Math.max(0, video.duration - .001));
    updateFrame(viewer.side, value);
  }

  function lockBoundary() {
    const viewer = state.viewer;
    if (!viewer || $('alignmentLock').disabled) return;
    const {side, edge} = viewer, run = state.runs[side];
    if (!run.id || run.id !== viewer.sessionId || run.token !== viewer.token || run.loading) return;
    const video = $('alignmentVideo');
    video.pause();
    if (!video.hidden && (video.seeking || video.readyState < 2)) return;
    const value = sourceTime(!video.hidden ? video.currentTime : Number($('alignmentTime').value));
    if (!Number.isFinite(value) || value < viewer.min || value > viewer.max) {
      $('alignmentError').textContent = 'Choose a time inside this recording.';
      return;
    }
    const opposite = edge === 'Start' ? 'End' : 'Start';
    const other = Number($(`${side}${opposite}`).value);
    if (edge === 'Start' ? value >= other : value <= other) {
      $('alignmentError').textContent = edge === 'Start'
        ? 'Choose a start before the end of the lap, or adjust End of lap first.'
        : 'Choose an end after the start of the lap, or adjust Start of lap first.';
      return;
    }
    $(`${side}${edge}`).value = sourceTime(value).toFixed(3);
    $(`${side}Range`).value = 'custom';
    const start = Number($(`${side}Start`).value), end = Number($(`${side}End`).value);
    const candidates = (run.ranges || []).filter(range => range.id.startsWith('segment-') &&
      range.start <= start + .001 && range.end >= end - .001);
    const retained = candidates.find(range => String(range.segment) === String(run.segment));
    run.segment = retained?.segment ?? (candidates.length === 1 ? candidates[0].segment : '');
    run.boundaryLocks ||= {};
    run.boundaryLocks[edge.toLowerCase()] = sourceTime(value);
    $(`${side}Detail`).textContent = 'Lap boundaries saved automatically';
    markDirty();
    $('alignmentDialog').close();
    updateFrame(side, value);
  }

  function setFrameReady(ready) {
    $('alignmentLock').disabled = !ready;
  }

  function showInspectorFrame(side, time, url, decodedTime = null) {
    if (state.viewer?.side !== side || !$('alignmentDialog').open || Math.abs(Number($('alignmentTime').value) - Number(time)) > .005) return;
    $('alignmentImage').src = url;
    const video = $('alignmentVideo');
    if (!video.hidden) return;
    const displayed = finite(decodedTime) ? Number(decodedTime) : Number(time);
    $('alignmentFrameStamp').textContent = `Frame ${Math.floor(displayed / 60)}:${(displayed % 60).toFixed(3).padStart(6, '0')}`;
    setFrameReady(true);
  }

  function sessionName(side) {
    const run = state.runs[side];
    return run.data?.session?.name || run.data?.session?.source?.name || state.sessions.find(session => session.id === run.id)?.name || names[side];
  }

  function recordingContext(side) {
    const session = state.runs[side].data?.session;
    return session?.context || session?.metadata?.telemetry_context || {};
  }

  function updateLegend() {
    $('comparisonLegendCurrent').textContent = sessionName('current');
    $('comparisonLegendReference').textContent = sessionName('reference');
    for (const side of ['current', 'reference']) {
      const legend = $(`comparisonLegend${side === 'current' ? 'Current' : 'Reference'}`).parentElement.querySelector('small');
      legend.textContent = side === 'current' ? 'YOUR RUN / SOLID' : 'REFERENCE / DASHED';
      $(`comparisonLegend${side === 'current' ? 'Current' : 'Reference'}`).parentElement.parentElement.hidden = !state.runs[side].id;
      $(`comparison${side === 'current' ? 'Current' : 'Reference'}Summary`).hidden = !state.runs[side].id;
      const thumbnail = $(`${side}Frame`).parentElement;
      thumbnail.tabIndex = state.runs[side].id ? 0 : -1;
      thumbnail.setAttribute('aria-disabled', String(!state.runs[side].id));
    }
    document.querySelector('.delta-sign-note').hidden = !isPaired();
    $('comparisonDeltaSummary').hidden = !isPaired();
    const sides = selectedSides();
    document.querySelector('.cursor-table thead tr').innerHTML = '<th>Channel</th>' + sides.map(side => `<th>${side === 'current' ? 'Your' : 'Ref.'}</th>`).join('') + (isPaired() ? '<th>Diff.</th>' : '');
    const command = isPaired() ? 'Compare' : 'Plot';
    if ($('runComparison').textContent.trim() !== command) {
      $('runComparison').innerHTML = `<i data-lucide="chart-no-axes-combined"></i>${command}`;
      helpers.icons();
    }
    $('synchronizedChart').setAttribute('aria-label', isPaired() ? 'Synchronized speed, brake, throttle, gear, steering and time delta traces' : 'Speed, brake, throttle, gear and steering traces');
  }

  function canExportComparison() {
    return isPaired() && state.result?.current && state.result?.reference &&
      state.result.points?.length > 0 && !state.result.video_only &&
      state.exportResultRevision === state.exportRevision &&
      !$('comparisonStatus').classList.contains('comparison-dirty') &&
      $('runComparison').getAttribute('aria-busy') !== 'true' &&
      ['current', 'reference'].every(side => state.result.sessions?.[side]?.id === state.runs[side].id) &&
      typeof window.ACCCComparisonExport?.create === 'function';
  }

  function updateExportAction() {
    $('exportComparison').disabled = !canExportComparison();
  }

  function invalidateExport() {
    state.exportRevision++;
    updateExportAction();
  }

  function exportComparison() {
    if (!canExportComparison()) return;
    try {
      const exported = window.ACCCComparisonExport.create(state.result);
      const url = URL.createObjectURL(new Blob([exported.text], {type: 'application/json'}));
      const link = document.createElement('a');
      link.href = url;
      link.download = exported.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      helpers.notify(`Exported ${exported.filename} (${Math.round(exported.bytes / 1024)} KB).`);
    } catch (error) {
      helpers.notify(error.message, true);
    }
  }

  function clearResult() {
    pausePlayback();
    invalidateExport();
    state.request++;
    clearTimeout(compareTimer);
    clearTimeout(previewTimer);
    state.result = null;
    state.playback.clock = [];
    notifyPlayback();
    state.extent = [0, 1];
    state.zoom = state.cursor = state.drag = null;
    window.ACCCTrackReview?.setComparison(null, {current: state.runs.current.id, reference: state.runs.reference.id});
    renderMetadata();
    draw();
    updateCursor();
  }

  function selectedRange(side) {
    const run = state.runs[side],
      value = $(`${side}Range`).value;
    if (!run.id) throw new Error(`Select ${names[side].toLowerCase()} recording.`);
    if (run.loading) throw new Error(`Wait for ${names[side].toLowerCase()} to finish loading.`);
    if (value !== 'custom') {
      const range = run.ranges.find(item => item.id === value);
      if (!range) throw new Error(`Select ${names[side].toLowerCase()} lap.`);
      return range.selection;
    }
    const start = Number($(`${side}Start`).value),
      end = Number($(`${side}End`).value);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) throw new Error(`${names[side]} end time must follow its start time.`);
    const result = {
      start_time: start,
      end_time: end
    };
    if (state.runs[side].segment !== '') result.segment_id = Number(state.runs[side].segment);
    return result;
  }

  function rangeInfo(side) {
    const run = state.runs[side];
    if (!run?.id || run.loading) return null;
    const id = $(`${side}Range`).value;
    const range = run.ranges?.find(item => item.id === id);
    const resolved = state.result?.[side];
    const start = Number($(`${side}Start`).value), end = Number($(`${side}End`).value);
    const describe = item => ({id: item.id, start: Number(item.start), end: Number(item.end),
      duration: Number(item.duration), segment: item.segment ?? item.selection?.segment_id ?? null,
      selection: {...item.selection}, lap: item.lap === true, reviewed: item.reviewed === true,
      boundariesVerified: item.boundariesVerified === true, valid: item.valid,
      startMarker: item.startMarker || null, endMarker: item.endMarker || null,
      landmarks: item.landmarks || []});
    const lapRanges = (run.ranges || []).filter(item => item.lap).map(describe);
    const lockedLap = boundaryLocked(side, 'Start') && boundaryLocked(side, 'End');
    return {...(range ? describe(range) : {}), id,
      segment: range?.segment ?? range?.selection?.segment_id ?? (finite(run.segment) ? Number(run.segment) : null),
      kind: range?.lap || lockedLap ? 'lap' : id === 'full' ? 'full' : id === 'custom' ? 'custom' : 'segment',
      lap: range?.lap === true || lockedLap, reviewed: range?.reviewed === true,
      boundariesVerified: range?.boundariesVerified === true,
      start: finite(start) ? start : null, end: finite(end) ? end : null,
      duration: finite(resolved?.duration) ? Number(resolved.duration) : finite(start) && finite(end) ? end - start : null,
      selection: resolved?.selection || (range ? {...range.selection} : {start_time: start, end_time: end,
        ...(state.runs[side].segment ? {segment_id: Number(state.runs[side].segment)} : {})}),
      lapRanges, reviewedLaps: lapRanges.filter(item => item.reviewed)};
  }

  function playbackState() {
    return {playing: state.playback.playing, available: !!state.result &&
      !$('comparisonStatus').classList.contains('comparison-dirty') && state.playback.clock.length > 1};
  }

  function notifyPlayback() {window.ACCCTrackReview?.setPlayback?.(playbackState());}

  function pausePlayback() {
    const playback = state.playback;
    const wasPlaying = playback.playing;
    playback.playing = false;
    if (playback.frame !== null) cancelAnimationFrame(playback.frame);
    playback.frame = null;
    notifyPlayback();
    if (wasPlaying && state.result) updateCursor();
  }

  function preparePlayback() {
    const clock = [];
    // Video has a continuous source clock even where OCR observations are absent.
    if (state.result?.axis === 'elapsed' && state.result.source_review) {
      const [start, end] = state.result.extent;
      state.playback.clock = end > start ? [{elapsed: 0, position: start}, {elapsed: end - start, position: end}] : [];
      notifyPlayback();
      return;
    }
    for (const point of state.result?.points || []) {
      if (!finite(point.position)) continue;
      const side = finite(point.current?.elapsed) ? 'current' : 'reference', sample = point[side];
      if (!finite(sample?.elapsed)) continue;
      const previous = clock.at(-1);
      let step = 0;
      if (previous) {
        // When the current range finishes first, the reference tail continues
        // at its own source-time pace rather than disappearing at the finish.
        const sharedSide = finite(previous.point[side]?.elapsed) ? side :
          ['current', 'reference'].find(key => finite(previous.point[key]?.elapsed) && finite(point[key]?.elapsed));
        if (!sharedSide) continue;
        step = Number(point[sharedSide].elapsed) - Number(previous.point[sharedSide].elapsed);
        // Keep the review clock monotonic across malformed timestamp resets.
        if (step <= 0) continue;
      }
      clock.push({elapsed: (previous?.elapsed || 0) + step, position: Number(point.position), point});
    }
    state.playback.clock = clock;
    notifyPlayback();
  }

  function positionAtElapsed(elapsed) {
    const clock = state.playback.clock;
    let lo = 0, hi = clock.length - 1;
    while (lo < hi) {
      const middle = Math.floor((lo + hi) / 2);
      if (clock[middle].elapsed < elapsed) lo = middle + 1;
      else hi = middle;
    }
    const b = clock[lo], a = clock[Math.max(0, lo - 1)];
    const fraction = b.elapsed > a.elapsed ? clamp((elapsed - a.elapsed) / (b.elapsed - a.elapsed), 0, 1) : 0;
    return a.position + (b.position - a.position) * fraction;
  }

  function elapsedAtPosition(position) {
    const clock = state.playback.clock;
    let lo = 0, hi = clock.length - 1;
    while (lo < hi) {
      const middle = Math.floor((lo + hi) / 2);
      if (clock[middle].position < position) lo = middle + 1;
      else hi = middle;
    }
    const b = clock[lo], a = clock[Math.max(0, lo - 1)];
    const fraction = b.position > a.position ? clamp((position - a.position) / (b.position - a.position), 0, 1) : 0;
    return a.elapsed + (b.elapsed - a.elapsed) * fraction;
  }

  function togglePlayback() {
    if (state.playback.playing) {pausePlayback(); return;}
    if (!playbackState().available || document.hidden || !$('view-analysis')?.classList.contains('active')) return;
    const playback = state.playback, clock = playback.clock;
    let elapsed = elapsedAtPosition(state.cursor ?? state.extent[0]);
    if (!Number.isFinite(elapsed) || elapsed >= clock.at(-1).elapsed - .01) {
      elapsed = clock[0].elapsed;
      state.cursor = clock[0].position;
    }
    playback.started = performance.now(); playback.elapsed = elapsed;
    playback.lastPaint = 0; playback.lastPreview = 0; playback.playing = true;
    notifyPlayback();
    draw(); updateCursor();
    playback.frame = requestAnimationFrame(advancePlayback);
  }

  function advancePlayback(now) {
    const playback = state.playback;
    if (!playback.playing) return;
    if (!state.result || document.hidden || !$('view-analysis')?.classList.contains('active')) {pausePlayback(); return;}
    const end = playback.clock.at(-1).elapsed;
    const elapsed = Math.min(end, playback.elapsed + (now - playback.started) / 1000);
    if (now - playback.lastPaint >= 1000 / 30 || elapsed >= end) {
      playback.lastPaint = now;
      state.cursor = positionAtElapsed(elapsed);
      draw(); updateCursor();
    }
    if (elapsed >= end) {pausePlayback(); return;}
    playback.frame = requestAnimationFrame(advancePlayback);
  }

  function markDirty(schedule = true) {
    pausePlayback();
    invalidateExport();
    state.request++;
    $('runComparison').disabled = selectedSides().length === 0;
    $('runComparison').setAttribute('aria-busy', 'false');
    updateRegistration();
    updateLegend();
    updateSessionActions();
    savePreferences();
    $('comparisonStatus').textContent = isPaired() ? 'Selections changed. Updating comparison...' : selectedSides().length ? 'Updating recording...' : 'Select a recording.';
    $('comparisonStatus').classList.add('comparison-dirty');
    notifyPlayback();
    clearTimeout(compareTimer);
    if (schedule && selectedSides().length) compareTimer = setTimeout(compare, 300);
  }

  function savePreferences() {
    if (state.restoring || !state.restored) return;
    const preferences = {
      axis: state.axis,
      ...alignmentState()
    };
    for (const side of ['current', 'reference']) preferences[side] = {
      id: state.runs[side].id || '',
      range: $(`${side}Range`).value,
      start: $(`${side}Start`).value,
      end: $(`${side}End`).value,
      segment: state.runs[side].segment,
      boundary_locks: Object.fromEntries(['Start', 'End'].map(edge => [edge.toLowerCase(), boundaryLocked(side, edge) ? Number($(`${side}${edge}`).value) : null])),
      defaults_revision: state.runs[side].defaultsRevision || ''
    };
    persistPreferences(preferences);
  }

  function persistPreferences(preferences) {
    const value = JSON.stringify(preferences);
    try { window.ACCCDesktopPreferences?.setItem('accc.comparison.v1', value); } catch { /* Browser fallback below. */ }
    try {
      localStorage.setItem('accc.comparison.v1', value);
    } catch {
      /* Browser storage can be disabled. */
    }
    state.saved = preferences;
  }

  function setAxis(axis) {
    state.axis = axis;
    for (const value of ['distance', 'elapsed']) {
      const button = $(`axis${value === 'distance' ? 'Distance' : 'Elapsed'}`);
      button.classList.toggle('active', value === axis);
      button.setAttribute('aria-pressed', String(value === axis));
    }
    updateRegistration();
  }

  function sourceReviewRanges(sides) {
    return Object.fromEntries(sides.flatMap(side => {
      const info = rangeInfo(side);
      if (!finite(info?.start) || !finite(info?.end) || info.end <= info.start) return [];
      // A time range cannot silently traverse a source seek or rewind.
      const selection = selectedRange(side), samples = state.runs[side].data.samples;
      const indexed = Number.isInteger(selection.start_index);
      const eligible = (indexed ? samples.slice(selection.start_index, selection.end_index + 1) : samples)
        .filter(sample => finite(sample.time) && (selection.segment_id == null || sample.segment_id === selection.segment_id));
      if (eligible.length < 2) return [];
      const selected = indexed ? eligible : eligible.filter(sample => sample.time >= info.start && sample.time <= info.end);
      if (selected.some((sample, index) => index && sample.time <= selected[index - 1].time)) return [];
      // Match the API's actual bounds, including nonzero source origins and
      // custom ranges that extend beyond the available recording. A segment
      // filter limits telemetry, not the requested source-video boundaries.
      let sourceStart = Infinity, sourceEnd = -Infinity;
      for (const sample of samples) if (finite(sample.time)) {
        sourceStart = Math.min(sourceStart, Number(sample.time));
        sourceEnd = Math.max(sourceEnd, Number(sample.time));
      }
      const start = indexed ? Number(eligible[0].time) : Math.max(Number(info.start), sourceStart);
      const end = indexed ? Number(eligible.at(-1).time) : Math.min(Number(info.end), sourceEnd);
      return end > start ? [[side, {start, end, duration: end - start}]] : [];
    }));
  }

  function sourceCursor(position, point) {
    return Object.fromEntries(selectedSides().map(side => {
      const range = state.result?.source_review?.[side];
      const time = state.result?.axis === 'elapsed' && range ? range.start + clamp(position, 0, range.duration) : point?.[side]?.time;
      return [side, finite(time) ? {time, ended: !!range && position >= range.duration && state.result?.axis === 'elapsed'} : null];
    }));
  }

  const unavailableTelemetry = error => /no usable distance|at least two usable HUD observations/i.test(error.message);

  async function timeReviewWithoutHud(sides, ranges) {
    // Retain the other recording's observations when only one side has no HUD.
    const traces = await Promise.all(sides.map(async side => {
      try {
        return [side, await helpers.api('/api/trace', {method: 'POST', body: JSON.stringify({
          session_id: state.runs[side].id, range: selectedRange(side), axis: 'elapsed'
        })})];
      } catch (error) {
        if (!unavailableTelemetry(error)) throw error;
        return [side, null];
      }
    }));
    const result = {axis: 'elapsed', extent: [0, Math.max(...Object.values(ranges).map(range => range.duration))],
      current: null, reference: null, points: [], channels: {}, warnings: [], source_review: ranges};
    const positions = new Set(result.extent);
    for (const [side, trace] of traces) {
      result[side] = trace?.current || null;
      for (const point of trace?.points || []) positions.add(point.position);
      for (const [key, value] of Object.entries(trace?.channels || {})) {
        result.channels[key] ||= {current: false, reference: false, comparable: false};
        result.channels[key][side] = value.current;
      }
      result.warnings.push(...(trace?.warnings || []));
      if (!trace) result.warnings.push(`${names[side]}: no usable HUD telemetry was detected in this range. Video playback remains available; channel readings are unavailable.`);
    }
    result.points = [...positions].sort((a, b) => a - b).map(position => {
      const point = {position, current: null, reference: null, delta_time_s: null};
      for (const [side, trace] of traces) {
        if (trace && position >= trace.extent[0] && position <= trace.extent[1]) point[side] = interpolatePoint(trace.points, position)?.current || null;
      }
      return point;
    });
    result.video_only = traces.every(([, trace]) => !trace);
    return result;
  }

  async function compare() {
    pausePlayback();
    invalidateExport();
    const exportRevision = state.exportRevision;
    state.playback.clock = [];
    notifyPlayback();
    clearTimeout(compareTimer);
    const token = ++state.request;
    $('runComparison').disabled = true;
    $('runComparison').setAttribute('aria-busy', 'true');
    try {
      const sides = selectedSides(), paired = sides.length === 2;
      if (!sides.length) throw new Error('Select a recording.');
      const body = paired ? {
        current_session_id: state.runs.current.id,
        reference_session_id: state.runs.reference.id,
        current_range: selectedRange('current'),
        reference_range: selectedRange('reference'),
        axis: state.axis,
        ...alignmentState()
      } : {session_id: state.runs[sides[0]].id, range: selectedRange(sides[0]), axis: state.axis};
      $('comparisonStatus').textContent = paired ? 'Aligning recorded samples...' : 'Loading recorded samples...';
      const ranges = sourceReviewRanges(sides);
      const canReviewTime = sides.every(side => ranges[side]);
      const request = () => helpers.api(paired ? '/api/compare' : '/api/trace', {method: 'POST', body: JSON.stringify(body)});
      let result, timeFallback = false, boundaryFallback = false, splitReview = false;
      try {result = await request();} catch (error) {
        if (!canReviewTime || !unavailableTelemetry(error)) throw error;
      }
      if (token !== state.request) return;
      const missingDistance = body.axis === 'distance' && (!result ||
        (paired ? sides : ['current']).some(side => !(Number(result[side]?.distance) > 0)));
      const missingBoundaryDistance = body.axis === 'distance' && result &&
        (paired ? sides : ['current']).some(side => result[side]?.distance_boundaries_complete === false);
      if (canReviewTime && (missingDistance || missingBoundaryDistance)) {
        timeFallback = true;
        boundaryFallback = !missingDistance && missingBoundaryDistance;
        body.axis = 'elapsed';
        if (paired) {body.alignment_mode = 'raw'; body.landmarks = [];}
        try {result = await request();} catch (error) {
          if (!unavailableTelemetry(error)) throw error;
          result = null;
        }
      }
      if (!result) {
        result = await timeReviewWithoutHud(sides, ranges);
        splitReview = true;
      }
      if (token !== state.request) return;
      if (!paired && sides[0] === 'reference' && !splitReview) {
        result.reference = result.current;
        result.current = null;
        for (const point of result.points || []) {
          point.reference = point.current;
          point.current = null;
        }
        for (const channel of Object.values(result.channels || {})) {
          channel.reference = channel.current;
          channel.current = false;
        }
      }
      result.source_review = ranges;
      if (timeFallback) {
        setAxis('elapsed');
        savePreferences();
        result.warnings ||= [];
        result.warnings.unshift(boundaryFallback
          ? 'Showing Time to preserve the selected IN and OUT points. Speed data is missing or ambiguous at a boundary, so Distance playback would skip part of the selection.'
          : 'Distance review is unavailable because a selected recording has no usable speed or movement. Showing Time for source video playback; track positions are not aligned.');
      }
      state.result = result;
      state.exportResultRevision = exportRevision;
      window.ACCCTrackReview?.setComparison(result, {current: state.runs.current.id, reference: state.runs.reference.id});
      const max = Number(result.extent?.[1] ?? result.points?.at(-1)?.position ?? 1);
      state.extent = [Number(result.extent?.[0] || 0), Math.max(max, .001)];
      state.zoom = null;
      state.cursor = Number(result.points?.[0]?.position || 0);
      $('comparisonStatus').textContent = result.video_only ? 'Video review is available. No usable HUD telemetry was detected in this range.' : result.points?.length ? '' : 'No usable telemetry in the selected range.';
      $('comparisonStatus').classList.remove('comparison-dirty');
      preparePlayback();
      renderMetadata();
      draw();
      updateCursor();
    } catch (error) {
      if (token === state.request) {
        state.result = null;
        state.playback.clock = [];
        notifyPlayback();
        window.ACCCTrackReview?.setComparison(null);
        $('comparisonStatus').textContent = error.message;
        $('comparisonStatus').classList.remove('comparison-dirty');
        renderMetadata();
        draw();
        updateCursor();
      }
    } finally {
      if (token === state.request) {
        $('runComparison').disabled = selectedSides().length === 0;
        $('runComparison').setAttribute('aria-busy', 'false');
        updateExportAction();
      }
    }
  }

  function renderMetadata() {
    const result = state.result || {};
    updateExportAction();
    updateLegend();
    const warnings = (result.warnings || []).filter(warning => !warning.startsWith('Estimated delta compares matching fractions'))
      .map(warning => warning.replace('Estimated delta assumes the selected ranges begin', 'The selected ranges are assumed to begin')
        .replace('delta is estimated and raw speed remains missing', 'raw speed remains missing'));
    $('comparisonWarnings').innerHTML = warnings.map(warning => `<p>${esc(warning)}</p>`).join('');
    $('comparisonAvailability').innerHTML = !state.result ? '' : channelSpecs.filter(spec => spec.key !== 'delta_time_s').map(spec => {
      const availability = result.channels?.[spec.key];
      const a = availability?.current,
        b = availability?.reference;
      const value = !isPaired() ? a || b ? 'Available' : 'Unavailable' : a && b ? availability.comparable === false ? 'Unit mismatch' : 'Both runs' : a ? 'Your run only' : b ? 'Reference only' : 'Unavailable';
      const available = isPaired() ? a && b && availability.comparable !== false : a || b;
      return `<div><span>${spec.label.charAt(0)+spec.label.slice(1).toLowerCase()}</span><span class="availability-state ${available ? '' : 'unavailable'}">${value}</span></div>`;
    }).join('');
    for (const side of ['current', 'reference']) {
      const metrics = result[side]?.metrics || {};
      const duration = result[side]?.duration ?? result.source_review?.[side]?.duration ?? metrics.duration ?? metrics.elapsed_seconds ?? metrics.duration_s;
      const distance = metrics.distance_m ?? metrics.distance;
      const partialDistance = result[side]?.distance_complete === false;
      const summary = $(`comparison${side === 'current' ? 'Current' : 'Reference'}Summary`);
      summary.innerHTML = `${names[side].toUpperCase()} <strong>${stamp(duration)}</strong> / ${number(distance)} m${partialDistance ? ' (partial distance)' : ''}`;
      summary.title = partialDistance ? 'Distance excludes movement where speed data is missing.' : '';
    }
    const deltaPoints = (result.points || []).filter(point => finite(point.delta_time_s));
    const lastDelta = deltaPoints.at(-1);
    const deltaBasis = result.delta_basis === 'lap_progress' ? 'matched lap progress' :
      result.delta_basis === 'registered_distance' ? 'registered distance' : 'matched distance';
    const deltaNote = document.querySelector('.delta-sign-note');
    deltaNote.textContent = `${result.delta_estimated ? 'Estimated delta' : 'Delta'} at ${deltaBasis} / + slower, − faster`;
    deltaNote.title = `Your elapsed time minus the reference elapsed time at ${result.delta_basis === 'lap_progress' ? "the same fraction of each selected range's integrated distance" : 'the same distance'}. Missing observations leave breaks in the delta trace.`;
    $('comparisonDeltaSummary').innerHTML = isPaired() ? lastDelta ? `DELTA <strong>${lastDelta.delta_time_s > 0 ? '+' : ''}${number(lastDelta.delta_time_s, 3)} s</strong>${lastDelta.position < result.points.at(-1).position ? ' / last comparable point' : ''}` : '<strong>No overlapping distance data</strong>' : '';
    $('comparisonDeltaSummary').title = lastDelta ? `At ${number(lastDelta.position, state.axis === 'elapsed' ? 2 : 0)} ${state.axis === 'elapsed' ? 's' : 'm'} on the chart; ${deltaBasis}.` : '';
    $('compareAxisNote').textContent = state.axis === 'distance' ? result.alignment?.mode && result.alignment.mode !== 'raw' ? 'Registered distance / estimated metres' : 'Speed distance / estimated metres' : 'Elapsed time / seconds';

  }

  function pointAt(position) {
    return interpolatePoint(state.result?.points || [], position);
  }

  function interpolatePoint(points, position) {
    if (!points.length) return null;
    let lo = 0,
      hi = points.length - 1;
    while (lo < hi) {
      const middle = Math.floor((lo + hi) / 2);
      if (points[middle].position < position) lo = middle + 1;
      else hi = middle;
    }
    const b = points[lo], a = points[Math.max(0, lo - 1)];
    if (position <= a.position) return a;
    if (position >= b.position || a.position === b.position) return b;
    const fraction = (position - a.position) / (b.position - a.position);
    const blend = (first, last) => finite(first) && finite(last) ? Number(first) + (Number(last) - Number(first)) * fraction : null;
    const sampleAt = side => {
      const first = a[side], last = b[side];
      if (!first || !last || first.segment_id !== last.segment_id) return null;
      const sample = {...first};
      for (const key of ['time','elapsed','distance','raw_distance','speed','brake','throttle','steering','steering_confidence','rpm']) {
        sample[key] = blend(first[key], last[key]);
      }
      return sample;
    };
    const continuousDelta = deltaSegment(a) === deltaSegment(b);
    return {position, current: sampleAt('current'), reference: sampleAt('reference'),
      delta_time_s: continuousDelta ? blend(a.delta_time_s, b.delta_time_s) : null,
      delta_segment_id: continuousDelta ? a.delta_segment_id : null,
      delta_current_time: continuousDelta ? blend(a.delta_current_time, b.delta_current_time) : null,
      delta_reference_time: continuousDelta ? blend(a.delta_reference_time, b.delta_reference_time) : null};
  }

  function deltaSegment(point) {
    return point.delta_segment_id ?? JSON.stringify(['current', 'reference'].map(side =>
      [point[side]?.segment_id ?? null, point[side]?.distance_segment_id ?? null]));
  }

  function gearNumber(value) {
    if (value === 'N') return 0;
    if (value === 'R') return -1;
    return finite(value) ? Number(value) : null;
  }

  function rangeFor(spec) {
    if (spec.key === 'gear') return [-1, 8];
    if (spec.key === 'steering') return [-100, 100];
    if (spec.key === 'delta_time_s') {
      const values = (state.result?.points || []).filter(point => finite(point.delta_time_s)).map(point => Math.abs(point.delta_time_s));
      const limit = Math.max(1, Math.ceil(Math.max(...values, 0) * 2) / 2);
      return [-limit, limit];
    }
    if (spec.key === 'speed') {
      const maximum = Math.max(0, ...(state.result?.points || []).map(point => Math.max(point.current?.speed || 0, point.reference?.speed || 0)));
      return [0, Math.max(100, Math.ceil(maximum / 50) * 50)];
    }
    return [0, 100];
  }

  function draw() {
    if (!ready && !$('synchronizedChart')) return;
    const canvas = $('synchronizedChart'),
      rect = canvas.getBoundingClientRect();
    if (!rect.width) return;
    const width = rect.width,
      height = rect.height,
      ratio = Math.min(devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const left = 47,
      right = 13,
      bottom = 24,
      top = 10,
      plotWidth = width - left - right;
    const bounds = state.zoom || state.extent;
    const channels = visibleChannels();
    const lane = (height - top - bottom) / channels.length;
    const x = position => left + (position - bounds[0]) / (bounds[1] - bounds[0]) * plotWidth;
    state.chart = {
      width,
      height,
      left,
      right,
      top,
      bottom,
      plotWidth,
      bounds,
      x
    };
    ctx.font = '8px -apple-system, sans-serif';
    for (let index = 0; index < channels.length; index++) {
      const spec = channels[index],
        y0 = top + index * lane,
        inset = 20,
        plotHeight = lane - inset - 9,
        limits = rangeFor(spec);
      const y = value => y0 + inset + (1 - (value - limits[0]) / (limits[1] - limits[0])) * plotHeight;
      ctx.fillStyle = spec.color;
      ctx.textAlign = 'left';
      ctx.fillText(`${spec.label}${spec.unit?' / '+spec.unit:''}`, left, y0 + 10);
      const ticks = spec.key === 'gear' ? [0, 4, 8] : spec.key === 'steering' ? [-100, 0, 100] : spec.key === 'delta_time_s' ? [limits[0], 0, limits[1]] : [0, limits[1] / 2, limits[1]];
      for (const tick of ticks) {
        ctx.strokeStyle = tick === 0 && spec.key === 'steering' ? '#536044' : '#28372d';
        ctx.lineWidth = .7;
        ctx.beginPath();
        ctx.moveTo(left, y(tick));
        ctx.lineTo(width - right, y(tick));
        ctx.stroke();
        ctx.fillStyle = '#85978b';
        ctx.textAlign = 'right';
        ctx.fillText(number(tick, spec.key === 'delta_time_s' ? 1 : 0), left - 9, y(tick) + 3);
      }
      for (let tick = 0; tick <= 8; tick++) {
        const position = bounds[0] + (bounds[1] - bounds[0]) * tick / 8;
        ctx.strokeStyle = '#1d2b22';
        ctx.beginPath();
        ctx.moveTo(x(position), y0 + inset);
        ctx.lineTo(x(position), y0 + lane - 9);
        ctx.stroke();
        if (index === channels.length - 1 && (width > 600 || tick % 2 === 0)) {
          ctx.fillStyle = '#8a9a90';
          ctx.textAlign = tick === 0 ? 'left' : tick === 8 ? 'right' : 'center';
          ctx.fillText(`${number(position,state.axis==='elapsed'?1:0)}${state.axis==='elapsed'?'s':'m'}`, x(position), height - 6);
        }
      }
      ctx.save();
      ctx.beginPath();
      ctx.rect(left, y0 + inset, plotWidth, plotHeight);
      ctx.clip();
      const sides = spec.key === 'delta_time_s' ? ['delta'] : ['reference', 'current'].filter(side => state.runs[side].id);
      for (const side of sides) {
        const steeringChannels = state.result?.channels?.steering;
        if (spec.key === 'steering' && steeringChannels?.current && steeringChannels?.reference && steeringChannels.comparable === false) continue;
        ctx.strokeStyle = side === 'delta' ? spec.color : runStyles[side].color;
        ctx.globalAlpha = 1;
        ctx.lineWidth = side === 'reference' ? 1.7 : 1.6;
        ctx.setLineDash(side === 'delta' ? [] : runStyles[side].dash);
        ctx.beginPath();
        let previous = null;
        const isolated = [];
        for (const point of state.result?.points || []) {
          if (point.position < bounds[0] || point.position > bounds[1]) continue;
          const sample = side === 'delta' ? point : point[side];
          let value = sample?.[spec.key];
          if (spec.key === 'gear') value = gearNumber(value);
          if (!finite(value)) {
            if (side === 'delta' && previous?.count === 1) isolated.push(previous);
            previous = null;
            continue;
          }
          const px = x(point.position),
            py = y(value);
          const segment = side === 'delta' ? deltaSegment(point) : sample.segment_id;
          const interrupted = previous && segment !== previous.segment;
          if (interrupted && side === 'delta' && previous.count === 1) isolated.push(previous);
          if (!previous || interrupted) ctx.moveTo(px, py);
          else if (spec.key === 'gear') {
            ctx.lineTo(px, previous.y);
            ctx.lineTo(px, py);
          } else ctx.lineTo(px, py);
          previous = {
            x: px,
            y: py,
            segment,
            count: !previous || interrupted ? 1 : previous.count + 1
          };
        }
        ctx.stroke();
        if (side === 'delta') {
          if (previous?.count === 1) isolated.push(previous);
          ctx.fillStyle = spec.color;
          for (const point of isolated) {
            ctx.beginPath(); ctx.arc(point.x, point.y, 2.5, 0, Math.PI * 2); ctx.fill();
          }
        }
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      }
      ctx.restore();
      if (spec.key === 'delta_time_s' && !state.result?.points?.some(point => finite(point.delta_time_s))) {
        ctx.fillStyle = '#6d7d70';
        ctx.font = '9px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(state.result ? 'No overlapping distance data' : 'No comparison loaded', left + plotWidth / 2, y0 + lane / 2 + 9);
      }
    }
    if (state.cursor !== null && state.result) {
      const cx = x(state.cursor);
      ctx.strokeStyle = '#d6e5aa';
      ctx.lineWidth = .8;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(cx, top);
      ctx.lineTo(cx, height - bottom);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (state.drag && Math.abs(state.drag.end - state.drag.start) > 4) {
      ctx.fillStyle = '#d8ee8822';
      ctx.fillRect(Math.min(state.drag.start, state.drag.end), top, Math.abs(state.drag.end - state.drag.start), height - top - bottom);
    }
  }

  function updateCursor() {
    const position = state.cursor ?? 0, point = pointAt(position), sources = sourceCursor(position, point);
    window.ACCCTrackReview?.setCursor(point, {position, sources});
    $('comparisonCursor').textContent = `${number(state.result ? position : null,state.axis==='elapsed'?2:0)} ${state.axis==='elapsed'?'s':'m'}`;
    const specs = [{
      key: 'time',
      label: 'Source',
      unit: 's',
      color: '#9ba89f'
    }, ...channelSpecs.filter(spec => spec.key !== 'delta_time_s')];
    const sides = selectedSides();
    $('comparisonValues').innerHTML = !sides.length ? '' : specs.map(spec => {
      const a = point?.current?.[spec.key],
        b = point?.reference?.[spec.key],
        digits = spec.key === 'time' ? 2 : spec.key === 'steering' ? 1 : 0;
      let difference = finite(a) && finite(b) ? Number(a) - Number(b) : null;
      if (spec.key === 'gear' || spec.key === 'time') difference = null;
      const render = value => spec.key === 'gear' ? (value ?? '--') : spec.key === 'time' ? stamp(value) : number(value, digits);
      return `<tr><td><span class="cursor-channel-dot" style="background:${spec.color}"></span>${spec.label.charAt(0)+spec.label.slice(1).toLowerCase()}</td>${sides.map(side => `<td>${esc(render(spec.key === 'time' ? sources[side]?.time : point?.[side]?.[spec.key]))}</td>`).join('')}${isPaired() ? `<td>${finite(difference)?(difference>0?'+':'')+number(difference,digits):'--'}</td>` : ''}</tr>`;
    }).join('') + (isPaired() ? `<tr><td>Delta / s</td><td colspan="3">${finite(point?.delta_time_s)?(point.delta_time_s>0?'+':'')+number(point.delta_time_s,3):'--'}</td></tr>` : '') + `<tr><td>Steer fit</td>${sides.map(side => `<td>${number(finite(point?.[side]?.steering_confidence) ? point[side].steering_confidence * 100 : null)}%</td>`).join('')}${isPaired() ? '<td></td>' : ''}</tr>`;
    clearTimeout(previewTimer);
    const request = state.request;
    const preview = () => {
      if (request !== state.request) return;
      for (const side of sides)
        if (finite(sources[side]?.time) && !(state.viewer?.side === side && $('alignmentDialog').open) && ![$(`${side}Start`), $(`${side}End`)].includes(document.activeElement)) updateFrame(side, sources[side].time);
    };
    if (state.playback.playing) {
      const now = performance.now();
      if (now - state.playback.lastPreview >= 1000) {
        state.playback.lastPreview = now;
        preview();
      }
    } else previewTimer = setTimeout(preview, 250);
  }

  async function updateFrame(side, time) {
    const run = state.runs[side];
    if (!run.id || !finite(time)) return;
    const width = state.viewer?.side === side && $('alignmentDialog').open ? 1920 : 1280;
    const key = `${run.id}:${Number(time).toFixed(6)}:${width}`;
    if (run.frameKey === key) {
      window.ACCCTrackReview?.setFrame(side, run.frameUrl, time, run.id);
      showInspectorFrame(side, time, run.frameUrl, run.frameDecodedTime);
      return;
    }
    if (run.frameBusy || Date.now() - (run.frameRequested || 0) < 1000) {
      clearTimeout(run.frameTimer);
      run.frameTimer = setTimeout(() => {
        if (state.runs[side] === run) updateFrame(side, time);
      }, Math.max(80, 1000 - (Date.now() - (run.frameRequested || 0))));
      return;
    }
    run.frameRequested = Date.now();
    run.frameBusy = true;
    const controller = new AbortController();
    run.frameAbort = controller;
    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(run.id)}/frame.jpg?time=${Number(time)}&width=${width}`, {signal: controller.signal});
      if (!response.ok) throw new Error('No frame');
      const decoded = response.headers.get('X-Source-Time');
      const blob = await response.blob();
      if (state.runs[side] !== run) return;
      const image = $(`${side}Frame`),
        old = run.frameUrl;
      run.frameUrl = URL.createObjectURL(blob);
      run.frameDecodedTime = finite(decoded) ? Number(decoded) : null;
      image.src = run.frameUrl;
      window.ACCCTrackReview?.setFrame(side, run.frameUrl, time, run.id);
      image.hidden = false;
      $(`${side}FrameEmpty`).hidden = true;
      $(`${side}FrameTime`).hidden = false;
      $(`${side}FrameTime`).textContent = stamp(time);
      showInspectorFrame(side, time, run.frameUrl, run.frameDecodedTime);
      if (old) URL.revokeObjectURL(old);
      run.frameKey = key;
    } catch (error) {
      if (state.runs[side] !== run || error.name === 'AbortError') return;
      window.ACCCTrackReview?.setFrame(side, null, time, run.id);
      $(`${side}Frame`).hidden = true;
      $(`${side}FrameEmpty`).hidden = false;
      $(`${side}FrameTime`).hidden = true;
    } finally {
      run.frameBusy = false;
      if (run.frameAbort === controller) run.frameAbort = null;
    }
  }

  function zoom(factor) {
    const bounds = state.zoom || state.extent,
      center = clamp(state.cursor ?? (bounds[0] + bounds[1]) / 2, bounds[0], bounds[1]);
    const span = clamp((bounds[1] - bounds[0]) * factor, (state.extent[1] - state.extent[0]) / 100, state.extent[1] - state.extent[0]);
    const start = clamp(center - span / 2, state.extent[0], state.extent[1] - span);
    state.zoom = [start, start + span];
    draw();
  }

  async function openImport(role = 'current') {
    const request = state.importSourceRequest = (state.importSourceRequest || 0) + 1;
    $('recordingImportRole').value = role === 'reference' ? 'reference' : 'current';
    $('referenceName').placeholder = role === 'reference' ? 'Reference lap' : 'My lap';
    $('referenceImportDialog').showModal();
    $('referenceImportError').textContent = '';
    $('referenceLocalFile').replaceChildren(new Option('Loading recordings…', ''));
    for (const id of ['referenceLocalFile', 'referenceUpload', 'startReferenceImport']) $(id).disabled = true;
    try {
      const result = await helpers.api('/api/sources');
      if (request !== state.importSourceRequest) return;
      const files = Array.isArray(result) ? result : result.sources || [];
      $('referenceLocalFile').innerHTML = '<option value="">Select a recording</option>' + files.map(file => `<option value="${esc(file.path)}">${esc(file.name || file.path)}</option>`).join('');
    } catch (error) {
      if (request !== state.importSourceRequest) return;
      $('referenceLocalFile').replaceChildren(new Option('Browse files to add a recording', ''));
      $('referenceImportError').textContent = error.message;
    } finally {
      if (request === state.importSourceRequest) {
        for (const id of ['referenceLocalFile', 'referenceUpload']) $(id).disabled = false;
        $('startReferenceImport').disabled = Boolean(state.importing);
      }
    }
    refreshJobs();
  }

  async function refreshJobs() {
    if (!ready || state.jobsLoading) return;
    state.jobsLoading = true;
    try {
      const response = await helpers.api('/api/imports');
      state.jobs = Array.isArray(response) ? response : response.jobs || response.imports || [];
      const active = state.jobs.find(job => !['completed', 'cancelled', 'error'].includes(job.state));
      state.activeJob = active || null;
      $('activeImport').hidden = !active;
      if (active) {
        $('activeImportName').textContent = active.name || active.url || 'Recording import';
        $('activeImportDetail').textContent = `${active.state === 'calibrating' ? 'Analyzing video' : active.state}${active.processed_frames?' / '+Number(active.processed_frames).toLocaleString()+' frames':''}`;
        $('activeImportProgress').style.width = `${finite(active.progress)?clamp(active.progress,0,100):5}%`;
        $('activeImportPercent').textContent = finite(active.progress) ? `${number(active.progress)}%` : '';
      }
      const available = new Set(state.sessions.map(session => session.id || session.session_id));
      $('referenceImportHistory').innerHTML = state.jobs.slice(0, 6).map(job => `<div class="import-job"><div><strong>${esc(job.name||job.url||job.path||'Recording')}</strong><small>${esc(job.error || (job.state === 'calibrating' ? 'Analyzing video' : job.state))}</small></div>${job.state==='completed'&&job.session_id&&available.has(job.session_id)&&!state.deletedSessions.has(job.session_id)?`<button class="button subtle compact import-use" data-session="${esc(job.session_id)}" data-role="${job.role === 'current' ? 'current' : 'reference'}">Use ${job.role === 'current' ? 'your run' : 'reference'}</button>`:!['completed','cancelled','error'].includes(job.state)?`<button class="icon-button import-cancel" data-id="${esc(job.id)}" title="Cancel import" aria-label="Cancel import"><i data-lucide="x"></i></button>`:''}</div>`).join('');
      helpers.icons();
      const completed = state.jobs.find(job => job.id === state.pendingImport && job.state === 'completed' && job.session_id);
      if (completed) {
        state.pendingImport = null;
        if (!state.deletedSessions.has(completed.session_id)) {
          await refresh(true);
          if (state.sessions.some(session => (session.id || session.session_id) === completed.session_id)) await loadRun(completed.role === 'current' ? 'current' : 'reference', completed.session_id);
        }
      }
    } catch (error) {
      if ($('referenceImportDialog').open) $('referenceImportError').textContent = error.message;
    } finally {
      state.jobsLoading = false;
    }
  }

  async function cancelImport(id) {
    try {
      await helpers.api(`/api/imports/${encodeURIComponent(id)}/cancel`, {
        method: 'POST',
        body: '{}'
      });
      await refreshJobs();
    } catch (error) {
      helpers.notify(error.message, true);
    }
  }

  function bind() {
    document.addEventListener('visibilitychange', () => {if (document.hidden) pausePlayback();});
    window.addEventListener('pagehide', pausePlayback);
    document.addEventListener('click', event => {
      const view = event.target.closest('[data-view]')?.dataset.view;
      if (view && view !== 'analysis') pausePlayback();
    }, true);
    $('swapComparisonRuns').onclick = swapRuns;
    for (const side of ['current', 'reference']) {
      $(`${side}DeleteSession`).onclick = async () => {
        const id = state.runs[side].id;
        if (!id || !helpers.deleteSession) return;
        try {
          await helpers.deleteSession(id);
        } catch (error) {
          helpers.notify(error.message, true);
        }
      };
      $(`${side}ImportRecording`).onclick = () => openImport(side);
      $(`${side}Session`).onchange = () => loadRun(side, $(`${side}Session`).value);
      $(`${side}Range`).onchange = () => {
        resetAlignment(false);
        chooseRange(side);
        applyReviewedAlignment(true);
        markDirty();
      };
      for (const edge of ['Start', 'End']) $(`${side}${edge}`).onchange = () => {
        if (state.runs[side].boundaryLocks) delete state.runs[side].boundaryLocks[edge.toLowerCase()];
        resetAlignment(false, edge);
        $(`${side}Range`).value = 'custom';
        markDirty();
      };
      for (const edge of ['Start', 'End']) {
        $(`${side}${edge}`).onfocus = () => {pausePlayback(); updateFrame(side, Number($(`${side}${edge}`).value));};
        $(`${side}${edge}`).oninput = () => {pausePlayback(); invalidateExport(); updateFrame(side, Number($(`${side}${edge}`).value));};
      }
      const thumbnail = $(`${side}Frame`).parentElement;
      thumbnail.setAttribute('role', 'button');
      thumbnail.setAttribute('aria-label', `Inspect ${names[side].toLowerCase()} source frame`);
      thumbnail.tabIndex = 0;
      thumbnail.onclick = () => {
        if (!state.runs[side].id) return;
        const time = Number($(`${side}Start`).value);
        openFrame(side, time);
      };
      thumbnail.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          thumbnail.click();
        }
      };
    }
    for (const button of document.querySelectorAll('.inspect-boundary')) button.onclick = () => openFrame(button.dataset.side, Number($(`${button.dataset.side}${button.dataset.edge}`).value), button.dataset.edge);
    $('closeAlignment').onclick = () => $('alignmentDialog').close();
    $('alignmentLock').onclick = lockBoundary;
    $('alignmentDialog').addEventListener('close', () => {
      const video = $('alignmentVideo');
      video.pause();
      video.removeAttribute('src');
      video.load();
      state.viewer = null;
      setFrameReady(false);
    });
    $('alignmentTime').oninput = () => seekBoundaryFrame($('alignmentTime').value);
    for (const [id, direction] of [
        ['alignmentBack', -1],
        ['alignmentForward', 1],
        ['alignmentFrameBack', -1 / 30],
        ['alignmentFrameForward', 1 / 30]
      ]) $(id).onclick = () => {
      seekBoundaryFrame(Number($('alignmentTime').value) + direction);
    };
    $('refreshComparisons').onclick = () => refresh(true);
    $('runComparison').onclick = compare;
    $('exportComparison').onclick = exportComparison;
    for (const axis of ['distance', 'elapsed']) $(`axis${axis==='distance'?'Distance':'Elapsed'}`).onclick = () => {
      setAxis(axis);
      markDirty();
    };
    $('compareZoomIn').onclick = () => zoom(.5);
    $('compareZoomOut').onclick = () => zoom(2);
    $('compareZoomReset').onclick = () => {
      state.zoom = null;
      draw();
    };
    const canvas = $('synchronizedChart');
    const pointer = e => clamp(e.clientX - canvas.getBoundingClientRect().left, state.chart.left, state.chart.width - state.chart.right);
    const position = x => state.chart.bounds[0] + (x - state.chart.left) / state.chart.plotWidth * (state.chart.bounds[1] - state.chart.bounds[0]);
    canvas.onpointerdown = e => {
      if (!state.result) return;
      pausePlayback();
      const x = pointer(e);
      state.drag = {
        start: x,
        end: x
      };
      canvas.setPointerCapture(e.pointerId);
      state.cursor = position(x);
      updateCursor();
      draw();
    };
    canvas.onpointermove = e => {
      if (!state.result) return;
      if (state.playback.playing && !state.drag) return;
      const x = pointer(e);
      if (state.drag) state.drag.end = x;
      state.cursor = position(x);
      updateCursor();
      draw();
    };
    canvas.onpointerup = () => {
      if (state.drag && Math.abs(state.drag.end - state.drag.start) > 10) state.zoom = [position(Math.min(state.drag.start, state.drag.end)), position(Math.max(state.drag.start, state.drag.end))];
      state.drag = null;
      draw();
    };
    canvas.onpointercancel = () => {
      state.drag = null;
      draw();
    };
    canvas.ondblclick = () => {
      state.zoom = null;
      draw();
    };
    canvas.onkeydown = e => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'Escape'].includes(e.key)) return;
      e.preventDefault();
      pausePlayback();
      if (e.key === 'Escape') {
        state.zoom = null;
        draw();
        return;
      }
      const bounds = state.zoom || state.extent;
      state.cursor = e.key === 'Home' ? bounds[0] : clamp((state.cursor || bounds[0]) + (e.key === 'ArrowRight' ? 1 : -1) * (bounds[1] - bounds[0]) / 100, bounds[0], bounds[1]);
      updateCursor();
      draw();
    };
    $('recordingImportRole').onchange = () => {$('referenceName').placeholder = $('recordingImportRole').value === 'current' ? 'My lap' : 'Reference lap';};
    $('closeReferenceImport').onclick = () => $('referenceImportDialog').close();
    for (const button of document.querySelectorAll('[data-import-kind]')) button.onclick = () => {
      state.importKind = button.dataset.importKind;
      for (const item of document.querySelectorAll('[data-import-kind]')) item.classList.toggle('active', item === button);
      $('import-url').classList.toggle('active', state.importKind !== 'file');
      $('import-file').classList.toggle('active', state.importKind === 'file');
    };
    $('referenceUpload').onclick = () => $('referenceUploadInput').click();
    $('referenceUploadInput').onchange = async () => {
      const file = $('referenceUploadInput').files[0];
      if (!file) return;
      $('referenceUpload').disabled = true;
      $('referenceImportError').textContent = '';
      try {
        const body = new FormData();
        body.append('file', file);
        const result = await helpers.api('/api/upload', {
          method: 'POST',
          body
        });
        $('referenceLocalFile').add(new Option(result.name || file.name, result.path));
        $('referenceLocalFile').value = result.path;
      } catch (error) {
        $('referenceImportError').textContent = error.message;
      } finally {
        $('referenceUpload').disabled = false;
        $('referenceUploadInput').value = '';
      }
    };
    $('startReferenceImport').onclick = async () => {
      if (state.importing) return;
      state.importing = true;
      $('startReferenceImport').disabled = true;
      $('referenceImportError').textContent = '';
      try {
        const kind = state.importKind || 'file';
        const body = {
          kind,
          role: $('recordingImportRole').value,
          auto_calibrate: true
        };
        if ($('referenceName').value.trim()) body.name = $('referenceName').value.trim();
        if (kind === 'file') {
          body.path = $('referenceLocalFile').value;
          if (!body.path) throw new Error('Select a local video.');
        } else {
          body.url = $('referenceUrl').value.trim();
          const url = new URL(body.url);
          if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Use an HTTP or HTTPS video URL.');
          const host = url.hostname.toLowerCase();
          body.kind = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'].includes(host) ? 'youtube' :
            ['twitch.tv', 'www.twitch.tv', 'm.twitch.tv', 'go.twitch.tv', 'clips.twitch.tv', 'player.twitch.tv'].includes(host) ? 'twitch' : 'url';
        }
        const job = await helpers.api('/api/imports', {
          method: 'POST',
          body: JSON.stringify(body)
        });
        state.pendingImport = job.id || job.job?.id;
        $('referenceImportDialog').close();
        await refreshJobs();
      } catch (error) {
        $('referenceImportError').textContent = error.message;
      } finally {
        state.importing = false;
        $('startReferenceImport').disabled = false;
      }
    };
    $('cancelActiveImport').onclick = () => {
      if (state.activeJob) cancelImport(state.activeJob.id);
    };
    $('referenceImportHistory').onclick = async e => {
      const use = e.target.closest('.import-use'),
        cancel = e.target.closest('.import-cancel');
      if (use) {
        $('referenceImportDialog').close();
        await refresh(true);
        await loadRun(use.dataset.role === 'current' ? 'current' : 'reference', use.dataset.session);
      }
      if (cancel) cancelImport(cancel.dataset.id);
    };
    window.addEventListener('resize', draw);
  }
  return {
    init,
    refresh,
    removeSession,
    openImport,
    openRecording: async id => {await refresh(true); await loadRun('current', id);},
    setActive: active => {if (!active) pausePlayback();},
    resize: draw
  };
})();
