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
    catalog: null,
    contextEditor: null,
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
    return `<div class="comparison-run"><div class="run-label"><span class="run-line ${side === 'reference' ? 'reference' : ''}"></span>${names[side].toUpperCase()}<span class="tag" id="${side}Calibration">NO RECORDING</span></div><div class="run-session-row"><div class="run-fields"><select id="${side}Session" aria-label="${names[side]} session"><option value="">Select a recording</option></select><div class="run-range-row"><select id="${side}Range" aria-label="${names[side]} lap or segment" disabled><option value="">Select a lap or segment</option></select><label class="range-time">IN <input id="${side}Start" type="number" step="0.001" min="0" aria-label="${names[side]} start time" disabled></label><label class="range-time">OUT <input id="${side}End" type="number" step="0.001" min="0" aria-label="${names[side]} end time" disabled></label></div></div><div class="run-thumbnail"><img id="${side}Frame" alt="${names[side]} source frame" hidden><span class="thumb-empty" id="${side}FrameEmpty">NO FRAME</span><span class="thumb-time" id="${side}FrameTime" hidden></span></div></div><div class="run-detail"><span id="${side}Detail">No recording selected</span><select class="run-custom-segment" id="${side}Segment" aria-label="${names[side]} custom segment" hidden></select></div></div>`;
  }

  function alignmentMarkup() {
    return `<div class="lap-registration"><div class="registration-heading"><label class="registration-mode">Distance registration<select id="comparisonAlignmentMode" aria-label="Distance registration"><option value="raw">Raw speed distance</option><option value="lap_endpoints">Same-lap endpoints</option><option value="landmarks">Paired track landmarks</option></select></label><div class="registration-confirmations" id="registrationConfirmations"><label class="align-toggle"><input id="confirmFinishAlignment" type="checkbox">OUT points are the same finish crossing</label></div><span id="registrationStatus" class="registration-status"></span></div><div id="landmarkEditor" hidden><div class="landmark-heading"><span>TRACK LANDMARK</span><span>Your source / s</span><span>Reference source / s</span><span></span></div><div id="landmarkRows"></div><div class="landmark-actions"><button class="button subtle compact" id="addComparisonLandmark"><i data-lucide="plus"></i>Add landmark</button><span id="landmarkError" role="status"></span></div></div></div><div class="comparison-legend" aria-label="Lap legend"><div><span class="run-line current"></span><span><strong id="comparisonLegendCurrent">Your run</strong><small>YOUR RUN / SOLID</small></span></div><div><span class="run-line reference"></span><span><strong id="comparisonLegendReference">Reference</strong><small>REFERENCE / DASHED</small></span></div><span class="delta-sign-note">Delta + slower / - faster</span></div>`;
  }

  function init(context) {
    if (ready) return;
    helpers = context;
    $('referenceWorkspace').innerHTML = `<div class="reference-header"><h2>Reference comparison</h2><div class="reference-header-actions"><button class="icon-button" id="refreshComparisons" title="Refresh recordings" aria-label="Refresh recordings"><i data-lucide="refresh-cw"></i></button><button class="button subtle compact" id="openReferenceImport"><i data-lucide="video"></i>Import reference</button></div></div><div class="import-status-strip" id="activeImport" hidden><i data-lucide="scan-line"></i><div class="import-status-copy"><strong id="activeImportName">Reference import</strong><small id="activeImportDetail"></small><div class="import-progress"><span id="activeImportProgress"></span></div></div><span class="import-percent" id="activeImportPercent"></span><button class="icon-button" id="cancelActiveImport" title="Cancel import" aria-label="Cancel import"><i data-lucide="x"></i></button></div><div class="run-pair">${sourceMarkup('current')}${sourceMarkup('reference')}</div><div class="compare-toolbar"><div class="compare-toolbar-left"><div class="axis-segments" role="group" aria-label="Comparison axis"><button id="axisDistance" class="active" aria-pressed="true">Distance</button><button id="axisElapsed" aria-pressed="false">Time</button></div><label class="align-toggle"><input id="confirmAlignment" type="checkbox">Same track and start marker</label></div><div class="compare-toolbar-right"><span class="compare-unit-note" id="compareAxisNote">Estimated distance / metres</span><button class="icon-button" id="compareZoomOut" title="Zoom out" aria-label="Zoom out"><i data-lucide="zoom-out"></i></button><button class="icon-button" id="compareZoomIn" title="Zoom in" aria-label="Zoom in"><i data-lucide="zoom-in"></i></button><button class="icon-button" id="compareZoomReset" title="Reset zoom" aria-label="Reset zoom"><i data-lucide="maximize-2"></i></button><button class="button primary compact" id="runComparison"><i data-lucide="chart-no-axes-combined"></i>Compare</button></div></div><div class="comparison-empty-status" id="comparisonStatus" role="status">Select two recordings to compare.</div><div class="comparison-body"><canvas id="synchronizedChart" class="synchronized-chart" aria-label="Synchronized speed, brake, throttle, gear, steering and time delta traces" tabindex="0"></canvas><aside class="comparison-inspector"><div class="cursor-heading"><span>CURSOR</span><strong id="comparisonCursor">-- m</strong></div><table class="cursor-table"><thead><tr><th>Channel</th><th>Your</th><th>Ref.</th><th>Diff.</th></tr></thead><tbody id="comparisonValues"></tbody></table><div class="section-bar"><h2>Channel coverage</h2></div><div class="compare-channel-availability" id="comparisonAvailability"></div><div class="comparison-warning-list" id="comparisonWarnings"></div></aside></div><div class="compare-summary-line"><span id="comparisonCurrentSummary">YOUR RUN <strong>--</strong></span><span id="comparisonDeltaSummary">DELTA <strong>Awaiting alignment</strong></span><span id="comparisonReferenceSummary">REFERENCE <strong>--</strong></span></div>`;
    $('referenceWorkspace').querySelector('.comparison-empty-status').insertAdjacentHTML('beforebegin', alignmentMarkup());
    $('refreshComparisons').insertAdjacentHTML('beforebegin', '<button class="icon-button" id="swapComparisonRuns" title="Swap your run and reference" aria-label="Swap your run and reference" disabled><i data-lucide="arrow-left-right"></i></button>');
    const startConfirmation = $('confirmAlignment').parentElement;
    startConfirmation.lastChild.textContent = 'Same circuit layout; IN points are the same start crossing';
    $('registrationConfirmations').prepend(startConfirmation);
    document.querySelector('.compare-toolbar-left').insertAdjacentHTML('beforeend', '<label class="align-toggle" id="sameTrackConfirmation"><input id="confirmSameTrack" type="checkbox">Same circuit layout</label>');
    document.querySelector('.compare-summary-line').insertAdjacentHTML('afterend', '<section class="observed-segments" id="observedSegments" hidden><div class="section-bar"><h2>Observed segment differences</h2></div><p id="observedSegmentsNote"></p><div class="segment-table-scroll"><table><thead><tr><th>Physical markers</th><th>Your run / s</th><th>Reference / s</th><th>Delta / s</th></tr></thead><tbody id="observedSegmentsRows"></tbody></table></div></section>');
    for (const side of ['current', 'reference']) {
      $(`${side}Detail`).parentElement.insertAdjacentHTML('afterend', `<div class="run-context"><span id="${side}Context">Car and circuit not labelled</span><button class="icon-button" id="${side}EditContext" title="Edit car, circuit and conditions" aria-label="Edit ${names[side].toLowerCase()} car, circuit and conditions" disabled><i data-lucide="sliders-horizontal"></i></button></div>`);
      $(`${side}Calibration`).insertAdjacentHTML('afterend', `<button class="icon-button run-delete" id="${side}DeleteSession" title="Delete recording" aria-label="Delete ${names[side].toLowerCase()} recording" disabled><i data-lucide="trash-2"></i></button>`);
      $(`${side}Detail`).parentElement.insertAdjacentHTML('afterend', `<div class="run-boundary-actions"><button class="icon-button inspect-boundary" data-side="${side}" data-edge="Start" title="Inspect start crossing" aria-label="Inspect ${names[side].toLowerCase()} start crossing"><i data-lucide="log-in"></i></button><button class="icon-button inspect-boundary" data-side="${side}" data-edge="End" title="Inspect finish crossing" aria-label="Inspect ${names[side].toLowerCase()} finish crossing"><i data-lucide="log-out"></i></button><span class="boundary-tool-slot" id="${side}BoundaryTools"></span></div>`);
      $(`${side}BoundaryTools`).innerHTML = `<button class="button subtle compact" id="${side}FindCrossing"><i data-lucide="scan-line"></i>Find next crossing</button><span id="${side}CrossingStatus" role="status"></span><div class="boundary-candidates" id="${side}CrossingCandidates" hidden><select id="${side}CrossingChoice" aria-label="${names[side]} suggested finish crossing"></select><button class="icon-button" id="${side}InspectCrossing" title="Inspect suggested finish crossing" aria-label="Inspect ${names[side].toLowerCase()} suggested finish crossing"><i data-lucide="scan"></i></button><button class="button subtle compact" id="${side}ApplyCrossing">Use OUT</button></div>`;
      $(`${side}FindCrossing`).disabled = true;
      $(`${side}BoundaryTools`).insertAdjacentHTML('beforebegin', `<button class="icon-button" id="${side}SaveLapRange" title="Save lap boundaries as an unverified range" aria-label="Save ${names[side].toLowerCase()} lap boundaries" disabled><i data-lucide="save"></i></button>`);
      $(`${side}FindCrossing`).insertAdjacentHTML('afterend', `<button class="icon-button" id="${side}CancelCrossing" title="Cancel crossing search" aria-label="Cancel ${names[side].toLowerCase()} crossing search" hidden><i data-lucide="x"></i></button>`);
      const tools = document.createElement('details');
      tools.className = 'run-review-tools';
      tools.innerHTML = '<summary>Lap boundaries and recording labels</summary>';
      const card = $(`${side}Session`).closest('.comparison-run');
      for (const selector of ['.run-detail', '.run-boundary-actions', '.run-context']) tools.appendChild(card.querySelector(selector));
      card.appendChild(tools);
    }
    state.importKind = 'file';
    const dialog = document.createElement('dialog');
    dialog.id = 'referenceImportDialog';
    dialog.innerHTML = `<div class="dialog-header"><div><span class="eyebrow">INDEPENDENT VIDEO ANALYSIS</span><h2>Import recording</h2></div><button class="icon-button" id="closeReferenceImport" title="Close" aria-label="Close recording import"><i data-lucide="x"></i></button></div><label class="field-label">Use this recording as<select id="recordingImportRole"><option value="current">Your run</option><option value="reference">Reference lap</option></select></label><div class="dialog-tabs" role="tablist"><button class="active" data-import-kind="file">Local recording</button><button data-import-kind="youtube">Video link</button></div><div class="import-kind-pane" id="import-url"><label class="field-label">Recorded video URL<input id="referenceUrl" type="url" placeholder="YouTube, Twitch VOD / clip, or video-file URL" autocomplete="off" aria-describedby="referenceUrlHint"></label><p class="reference-url-hint" id="referenceUrlHint">Import a completed recording: YouTube video, Twitch VOD or clip, or a direct video file. Live streams are not supported.</p></div><div class="import-kind-pane active" id="import-file"><label class="field-label">Recording<select id="referenceLocalFile"><option value="">Select a local recording</option></select></label><button class="button subtle compact" id="referenceUpload"><i data-lucide="upload"></i>Browse files</button><input id="referenceUploadInput" type="file" accept="video/*,.mkv" hidden></div><label class="field-label reference-name-input">Recording name<input id="referenceName" type="text" placeholder="My lap" maxlength="100"></label><label class="toggle-row"><span>Automatic HUD calibration</span><input id="referenceAutoCalibration" type="checkbox" checked></label><button class="button primary full-width" id="startReferenceImport"><i data-lucide="download"></i>Import and analyze</button><div class="dialog-error" id="referenceImportError" role="alert"></div><div class="import-history" id="referenceImportHistory"></div>`;
    document.body.appendChild(dialog);
    const contextDialog = document.createElement('dialog');
    contextDialog.id = 'recordingContextDialog';
    contextDialog.innerHTML = '<div class="dialog-header"><div><span class="eyebrow">RECORDING LABELS / USER SUPPLIED</span><h2>Car and circuit</h2></div><button class="icon-button" id="closeRecordingContext" title="Close" aria-label="Close recording labels"><i data-lucide="x"></i></button></div><p id="recordingContextName" class="context-recording-name"></p><label class="field-label">GT3 car<select id="recordingContextCar"><option value="">Unknown car</option></select></label><label class="field-label">Circuit layout<select id="recordingContextTrack"><option value="">Unknown circuit</option></select></label><div class="context-optional-fields"><label class="field-label">Platform<select id="recordingContextPlatform"><option value="">Unknown</option><option value="pc">PC</option><option value="ps5">PS5</option><option value="xbox_series">Xbox Series</option></select></label><label class="field-label">Game version<input id="recordingContextVersion" maxlength="160" placeholder="Unknown"></label></div><label class="field-label">Conditions<input id="recordingContextConditions" maxlength="160" placeholder="Weather, track temperature, fuel and tyres"></label><label class="field-label">Balance of Performance<input id="recordingContextBop" maxlength="160" placeholder="Unknown"></label><div class="dialog-error" id="recordingContextError" role="alert"></div><button class="button primary full-width" id="saveRecordingContext"><i data-lucide="save"></i>Save labels</button>';
    document.body.appendChild(contextDialog);
    for (const input of contextDialog.querySelectorAll('input')) input.type = 'text';
    const frameDialog = document.createElement('dialog');
    frameDialog.id = 'alignmentDialog';
    frameDialog.innerHTML = '<div class="dialog-header"><div><span class="eyebrow" id="alignmentSide">SOURCE FRAME</span><h2>Range alignment</h2></div><button class="icon-button" id="closeAlignment" title="Close" aria-label="Close alignment frame"><i data-lucide="x"></i></button></div><div class="alignment-image"><img id="alignmentImage" alt="Full source frame for range alignment"></div><div class="alignment-controls"><button class="icon-button" id="alignmentBack" title="Previous second" aria-label="Previous second"><i data-lucide="chevron-left"></i></button><label class="range-time">SOURCE <input id="alignmentTime" type="number" min="0" step="0.033" aria-label="Alignment source timestamp">s</label><button class="icon-button" id="alignmentForward" title="Next second" aria-label="Next second"><i data-lucide="chevron-right"></i></button><span id="alignmentFrameStamp" class="subtle-text"></span><button class="button subtle compact" id="alignmentSetStart">Set IN</button><button class="button primary compact" id="alignmentSetEnd">Set OUT</button></div>';
    document.body.appendChild(frameDialog);
    $('alignmentBack').insertAdjacentHTML('beforebegin', '<button class="icon-button" id="alignmentFrameBack" title="Back 1/30 second" aria-label="Back 1/30 second"><i data-lucide="chevrons-left"></i></button>');
    $('alignmentForward').insertAdjacentHTML('afterend', '<button class="icon-button" id="alignmentFrameForward" title="Forward 1/30 second" aria-label="Forward 1/30 second"><i data-lucide="chevrons-right"></i></button>');
    $('alignmentSetEnd').insertAdjacentHTML('afterend', '<button class="button primary compact" id="alignmentSetLandmark" hidden><i data-lucide="map-pin"></i>Use landmark</button>');
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
    state.alignmentMode = ['raw', 'lap_endpoints', 'landmarks'].includes(state.saved.alignment_mode) ? state.saved.alignment_mode : 'raw';
    state.landmarks = Array.isArray(state.saved.landmarks) ? state.saved.landmarks.slice(0, 32) : [];
    $('comparisonAlignmentMode').value = state.alignmentMode;
    $('confirmAlignment').checked = state.saved.align_start === true;
    $('confirmSameTrack').checked = state.saved.confirm_same_track === true;
    $('confirmFinishAlignment').checked = state.saved.align_end === true;
    for (const axis of ['distance', 'elapsed']) {
      const button = $(`axis${axis === 'distance' ? 'Distance' : 'Elapsed'}`);
      button.classList.toggle('active', axis === state.axis);
      button.setAttribute('aria-pressed', String(axis === state.axis));
    }
    bind();
    renderLandmarks();
    updateRegistration();
    ready = true;
    window.ACCCTrackReview?.init({api: helpers.api, icons: helpers.icons,
      carName: side => {
        const context = recordingContext(side);
        return context.car_name || state.catalog?.cars?.find(car => car.id === context.car_id)?.name || 'Car not labelled';
      }, trackId: () => {
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
    helpers.api('/api/catalog').then(catalog => {state.catalog = catalog; renderMetadata(); updateCursor();}).catch(() => {});
    helpers.icons();
    renderMetadata();
    updateCursor();
    $('comparisonStatus').textContent = 'Select a recording.';
    $('runComparison').disabled = true;
    for (const button of document.querySelectorAll('.inspect-boundary')) button.disabled = true;
    for (const side of ['current', 'reference']) $(`${side}Segment`).disabled = true;
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
        $('comparisonAlignmentMode').value = 'raw';
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
    for (const side of ['current', 'reference']) $(`${side}DeleteSession`).disabled = !state.runs[side].id || state.runs[side].loading || !helpers.deleteSession;
  }

  function swapRuns() {
    if ($('swapComparisonRuns').disabled) return;
    const sides = ['current', 'reference'];
    const snapshots = {};
    for (const side of sides) {
      const run = state.runs[side];
      snapshots[side] = {run, rangeOptions: $(`${side}Range`).innerHTML, range: $(`${side}Range`).value,
        start: $(`${side}Start`).value, end: $(`${side}End`).value,
        segmentOptions: $(`${side}Segment`).innerHTML, segment: $(`${side}Segment`).value,
        segmentHidden: $(`${side}Segment`).hidden, calibration: $(`${side}Calibration`).textContent,
        detail: $(`${side}Detail`).textContent, detailTitle: $(`${side}Detail`).title};
      clearTimeout(run.frameTimer);
      run.frameAbort?.abort();
      if (run.frameUrl) URL.revokeObjectURL(run.frameUrl);
      if (run.crossingJob) cancelCrossing(side);
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
      $(`${side}Range`).innerHTML = incoming.rangeOptions;
      $(`${side}Range`).value = incoming.range;
      $(`${side}Segment`).innerHTML = incoming.segmentOptions;
      $(`${side}Segment`).value = incoming.segment;
      $(`${side}Segment`).hidden = incoming.segmentHidden;
      $(`${side}Start`).value = incoming.start;
      $(`${side}End`).value = incoming.end;
      for (const field of ['Range', 'Start', 'End', 'Segment']) $(`${side}${field}`).disabled = !run.data?.samples?.length;
      $(`${side}Calibration`).textContent = incoming.calibration;
      $(`${side}Detail`).textContent = incoming.detail;
      $(`${side}Detail`).title = incoming.detailTitle;
      $(`${side}Frame`).hidden = true;
      $(`${side}Frame`).removeAttribute('src');
      $(`${side}FrameEmpty`).hidden = false;
      $(`${side}FrameTime`).hidden = true;
      $(`${side}FrameTime`).textContent = '';
      $(`${side}CrossingCandidates`).hidden = true;
      $(`${side}CrossingStatus`).textContent = '';
      $(`${side}CancelCrossing`).hidden = true;
      $(`${side}FindCrossing`).disabled = !run.id;
      for (const button of document.querySelectorAll(`.inspect-boundary[data-side="${side}"]`)) button.disabled = !run.id;
    }
    state.landmarks = state.landmarks.map(({label, current_time, reference_time}) => ({label, current_time: reference_time, reference_time: current_time}));
    renderLandmarks();
    clearResult();
    notifySelectedRecording();
    state.restoring = false;
    markDirty();
    for (const side of sides) if (state.runs[side].id) updateFrame(side, Number($(`${side}Start`).value));
  }

  async function removeSession(id) {
    if (!ready || !id) return;
    if (state.contextEditor?.id === id) {
      state.contextEditor = null;
      $('recordingContextDialog').close();
    }
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
    if (state.runs[side].crossingJob) cancelCrossing(side);
    $(`${side}CrossingCandidates`).hidden = true;
    $(`${side}CrossingStatus`).textContent = '';
    $(`${side}FindCrossing`).disabled = !id;
    $(`${side}CancelCrossing`).hidden = true;
    const previous = state.runs[side], token = (previous.token || 0) + 1;
    clearTimeout(previous.frameTimer);
    previous.frameAbort?.abort();
    if (previous.frameUrl) URL.revokeObjectURL(previous.frameUrl);
    if (state.viewer?.side === side) {
      $('alignmentDialog').close();
      $('alignmentImage').removeAttribute('src');
      state.viewer = null;
    }
    const run = state.runs[side] = {token, loading: Boolean(id)};
    updateSessionActions();
    $(`${side}Session`).value = id;
    $(`${side}Range`).innerHTML = '<option value="">Select a lap or segment</option>';
    $(`${side}Segment`).innerHTML = '';
    $(`${side}Segment`).hidden = true;
    for (const field of ['Range', 'Start', 'End', 'Segment']) $(`${side}${field}`).disabled = true;
    for (const edge of ['Start', 'End']) $(`${side}${edge}`).value = '';
    $(`${side}Detail`).textContent = 'No recording selected';
    $(`${side}Calibration`).textContent = 'NO RECORDING';
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
    for (const field of ['Range','Start','End','Segment']) $(`${side}${field}`).disabled = true;
    $(`${side}Frame`).hidden = true;
    $(`${side}FrameEmpty`).hidden = false;
    $(`${side}Calibration`).textContent = 'LOADING';
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
        ranges: makeRanges(data),
        defaultsRevision: String(data.session?.metadata?.comparison_defaults?.revision || ''),
        token
      };
      notifySelectedRecording();
      const current = state.runs[side];
      $(`${side}Range`).innerHTML = current.ranges.map(range => `<option value="${esc(range.id)}">${esc(range.label)}</option>`).join('') + '<option value="custom">Custom time range</option>';
      $(`${side}Range`).disabled = !data.samples?.length;
      $(`${side}Segment`).disabled = !data.samples?.length;
      const configured = data.session?.metadata?.comparison_defaults;
      const preferred = current.ranges.find(range => range.id === configured?.range_id) || current.ranges.find(range => range.lap && range.valid !== false) || current.ranges.filter(range => !range.lap && range.id !== 'full').sort((a, b) => b.duration - a.duration)[0] || current.ranges[0];
      $(`${side}Range`).value = preferred?.id || 'custom';
      const previousPreference = restore && state.saved[side]?.id === id ? state.saved[side] : null;
      const revisionChanged = previousPreference && current.defaultsRevision !== String(previousPreference.defaults_revision || '');
      const saved = revisionChanged ? null : previousPreference;
      if (revisionChanged) {
        state.alignmentMode = 'raw';
        $('comparisonAlignmentMode').value = 'raw';
        resetAlignment(true);
      }
      if (saved && (saved.range === 'custom' || current.ranges.some(range => range.id === saved.range))) $(`${side}Range`).value = saved.range;
      const segments = [...new Set(data.samples.map(sample => sample.segment_id).filter(value => value != null))];
      $(`${side}Segment`).innerHTML = '<option value="">All segments</option>' + segments.map(segment => `<option value="${segment}">Segment ${segment}</option>`).join('');
      const calibration = data.session?.metadata?.calibration || data.session?.metadata?.profile?.auto_calibrate;
      const lastCalibration = [...data.samples].reverse().find(sample => sample.hud_visible && sample.calibration)?.calibration;
      $(`${side}Calibration`).textContent = lastCalibration?.status ? `HUD ${String(lastCalibration.status).toUpperCase()}` : calibration ? 'AUTO HUD' : 'SAVED HUD';
      chooseRange(side);
      if (saved?.range === 'custom') {
        $(`${side}Start`).value = saved.start;
        $(`${side}End`).value = saved.end;
        $(`${side}Segment`).value = saved.segment ?? '';
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
      $(`${side}Segment`).value = range.segment ?? '';
    }
    $(`${side}Segment`).hidden = value !== 'custom';
    const description = range?.reviewed ? `${range.boundariesVerified ? 'Reviewed boundaries' : 'Saved boundaries / unverified'}${finite(range.timingUncertainty) ? ` / +/-${number(range.timingUncertainty, 3)} s` : ''}` : range?.lap ? 'Lap selection' : value === 'custom' ? 'Custom source timestamps' : value === 'full' ? 'Entire recording' : 'Continuous segment selection';
    $(`${side}Detail`).textContent = run.data ? `${run.data.samples.length.toLocaleString()} samples / ${description}` : 'No recording selected';
    $(`${side}Detail`).title = range?.notes || '';
    updateFrame(side, Number($(`${side}Start`).value));
  }

  function applyReviewedAlignment(force = false) {
    const ranges = ['current', 'reference'].map(side => state.runs[side].ranges?.find(range => range.id === $(`${side}Range`).value));
    const [current, reference] = ranges;
    if (!ranges.every(range => range?.reviewed && range.boundariesVerified) || !current.startMarker || !current.endMarker || current.startMarker !== reference.startMarker || current.endMarker !== reference.endMarker) return;
    const restored = ['current', 'reference'].every(side => {
      const run = state.runs[side], saved = state.saved[side];
      return saved?.id === run.id && saved.range === $(`${side}Range`).value && String(saved.defaults_revision || '') === run.defaultsRevision;
    });
    if (!force && restored) return;
    const referenceMarkers = new Map(reference.landmarks.map(marker => [marker.marker_id, marker]));
    const landmarks = current.landmarks.filter(marker => marker.marker_id && finite(marker.time) && marker.time > current.start && marker.time < current.end).map(marker => {
      const paired = referenceMarkers.get(marker.marker_id);
      return paired && finite(paired.time) && paired.time > reference.start && paired.time < reference.end ? {label: marker.label || paired.label || marker.marker_id, current_time: sourceTime(marker.time), reference_time: sourceTime(paired.time)} : null;
    }).filter(Boolean).sort((a, b) => a.current_time - b.current_time);
    const ordered = landmarks.every((landmark, index) => index === 0 || landmark.current_time > landmarks[index - 1].current_time && landmark.reference_time > landmarks[index - 1].reference_time);
    state.landmarks = ordered ? landmarks.slice(0, 32) : [];
    state.alignmentMode = state.landmarks.length ? 'landmarks' : 'lap_endpoints';
    state.axis = 'distance';
    $('comparisonAlignmentMode').value = state.alignmentMode;
    $('confirmAlignment').checked = true;
    $('confirmFinishAlignment').checked = true;
    for (const axis of ['distance', 'elapsed']) {
      const button = $(`axis${axis === 'distance' ? 'Distance' : 'Elapsed'}`);
      button.classList.toggle('active', axis === state.axis);
      button.setAttribute('aria-pressed', String(axis === state.axis));
    }
    renderLandmarks();
    updateRegistration();
  }

  function resetAlignment(clearLandmarks = false, edge = null) {
    if (edge !== 'End') $('confirmAlignment').checked = false;
    if (edge !== 'Start') $('confirmFinishAlignment').checked = false;
    if (clearLandmarks) {
      $('confirmSameTrack').checked = false;
      state.landmarks = [];
      renderLandmarks();
    }
    updateRegistration();
  }

  function updateRegistration() {
    const registered = state.alignmentMode !== 'raw';
    const timeAxis = state.axis !== 'distance';
    document.querySelector('.lap-registration').hidden = !isPaired();
    $('comparisonAlignmentMode').disabled = timeAxis || !isPaired();
    const contexts = ['current', 'reference'].map(recordingContext);
    $('sameTrackConfirmation').hidden = !isPaired() || contexts.every(context => context.track_id);
    $('confirmSameTrack').disabled = !isPaired();
    $('confirmAlignment').disabled = timeAxis || !isPaired();
    $('confirmFinishAlignment').disabled = timeAxis || !isPaired();
    $('landmarkEditor').hidden = timeAxis || state.alignmentMode !== 'landmarks';
    $('confirmFinishAlignment').parentElement.hidden = timeAxis || !registered;
    const missingStart = !$('confirmAlignment').checked;
    const missingEnd = registered && !$('confirmFinishAlignment').checked;
    $('registrationStatus').textContent = state.axis !== 'distance' ? 'Time axis / delta at lap progress' : missingStart || missingEnd ? `Unconfirmed ${[missingStart ? 'start' : '', missingEnd ? 'finish' : ''].filter(Boolean).join(' + ')}` : registered ? 'Estimated track-position registration' : 'Speed-integrated distance';
    $('registrationStatus').classList.toggle('unconfirmed', missingStart || missingEnd);
  }

  function renderLandmarks() {
    for (const landmark of state.landmarks) {
      for (const side of ['current', 'reference']) {
        if (finite(landmark[`${side}_time`])) landmark[`${side}_time`] = sourceTime(landmark[`${side}_time`]);
      }
    }
    $('landmarkRows').innerHTML = state.landmarks.map((landmark, index) => `<div class="landmark-row" data-landmark="${index}"><input data-field="label" type="text" aria-label="Landmark ${index + 1} name" maxlength="60" value="${esc(landmark.label || '')}" placeholder="Track landmark ${index + 1}">${['current', 'reference'].map(side => `<div class="landmark-time"><input data-field="${side}_time" type="number" min="0" step="0.001" aria-label="Landmark ${index + 1} ${names[side].toLowerCase()} source time" value="${finite(landmark[`${side}_time`]) ? Number(landmark[`${side}_time`]) : ''}"><button class="icon-button landmark-frame" data-side="${side}" title="Pick ${names[side].toLowerCase()} landmark frame" aria-label="Pick landmark ${index + 1} ${names[side].toLowerCase()} frame"><i data-lucide="scan"></i></button>${side === 'reference' ? `<button class="icon-button find-landmark-match" title="Find matching scene near the reference timestamp" aria-label="Find landmark ${index + 1} matching reference scene" ${landmark.matching ? 'disabled' : ''}><i data-lucide="search"></i></button>` : ''}</div>`).join('')}<button class="icon-button remove-landmark" title="Remove landmark" aria-label="Remove landmark ${index + 1}"><i data-lucide="x"></i></button>${landmark.matchStatus || landmark.matches?.length ? `<div class="landmark-matches"><span role="status">${esc(landmark.matchStatus || '')}</span>${landmark.matches?.length ? `<select class="landmark-match-choice" aria-label="Landmark ${index + 1} matching scene candidate">${landmark.matches.map((candidate, candidateIndex) => `<option value="${candidateIndex}">${stamp(candidate.target_time)} / ${candidate.inliers} image matches / unconfirmed</option>`).join('')}</select><button class="icon-button inspect-landmark-match" title="Review suggested reference scene" aria-label="Review landmark ${index + 1} suggested reference scene"><i data-lucide="scan"></i></button>` : ''}</div>` : ''}</div>`).join('');
    $('addComparisonLandmark').disabled = state.landmarks.length >= 32;
    helpers.icons();
  }

  function selectedLandmarks() {
    return state.landmarks.map((landmark, index) => {
      if (!finite(landmark.current_time) || !finite(landmark.reference_time)) throw new Error(`Landmark ${index + 1} needs both source timestamps.`);
      return {current_time: sourceTime(landmark.current_time), reference_time: sourceTime(landmark.reference_time), label: landmark.label || `Landmark ${index + 1}`};
    });
  }

  async function findLandmarkMatch(index) {
    const landmark = state.landmarks[index];
    if (!landmark || landmark.matching) return;
    if (!finite(landmark.current_time) || !state.runs.current.id || !state.runs.reference.id) {
      landmark.matchStatus = 'Select both recordings and a source landmark first.';
      renderLandmarks();
      return;
    }
    const start = Number($('referenceStart').value), end = Number($('referenceEnd').value);
    const currentStart = Number($('currentStart').value), currentEnd = Number($('currentEnd').value);
    const guess = finite(landmark.reference_time) ? Number(landmark.reference_time) : start + (Number(landmark.current_time) - currentStart) / (currentEnd - currentStart) * (end - start);
    const body = {anchor_session_id: state.runs.current.id, target_session_id: state.runs.reference.id,
      anchor_time: Number(landmark.current_time), search_start: Math.max(start, guess - 10), search_end: Math.min(end, guess + 10)};
    landmark.matching = true;
    landmark.matches = [];
    landmark.matchStatus = 'Matching track scene...';
    renderLandmarks();
    const unchanged = () => state.landmarks.includes(landmark) && state.runs.current.id === body.anchor_session_id && state.runs.reference.id === body.target_session_id && Number(landmark.current_time) === body.anchor_time;
    let job = null;
    try {
      job = await helpers.api('/api/landmark-detection', {method: 'POST', body: JSON.stringify(body)});
      const deadline = Date.now() + 10 * 60 * 1000;
      while (!['completed', 'failed', 'error', 'cancelled'].includes(job.state)) {
        if (!unchanged()) return;
        if (Date.now() > deadline) throw new Error('Scene search is taking too long. Retry later.');
        await new Promise(resolve => setTimeout(resolve, 900));
        if (!unchanged()) return;
        job = await helpers.api(`/api/lap-detection/${encodeURIComponent(job.id)}`);
      }
      if (!unchanged()) return;
      if (job.state !== 'completed') throw new Error(job.error || 'Scene search failed.');
      landmark.matches = job.result?.candidates || [];
      landmark.matchStatus = landmark.matches.length ? 'Scene suggestions / physical marker unconfirmed' : job.result?.warnings?.[0] || 'No reliable matching scene found.';
    } catch (error) {
      if (unchanged()) landmark.matchStatus = error.message;
    } finally {
      await cancelUnfinishedDetection(job);
      if (!unchanged()) landmark.matchStatus = 'Landmark changed. Search cancelled.';
      landmark.matching = false;
      if (state.landmarks.includes(landmark)) renderLandmarks();
    }
  }

  async function cancelUnfinishedDetection(job) {
    if (!job?.id || ['completed', 'failed', 'error', 'cancelled'].includes(job.state)) return;
    try {
      await helpers.api(`/api/lap-detection/${encodeURIComponent(job.id)}/cancel`, {method: 'POST', body: '{}'});
    } catch {
      /* A lost connection must not leave the local search controls locked. */
    }
  }

  function openFrame(side, time, landmarkIndex = null) {
    if (!state.runs[side]?.id) return;
    pausePlayback();
    state.viewer = {side, landmarkIndex};
    $('alignmentSide').textContent = `${names[side].toUpperCase()} / ${landmarkIndex === null ? 'SOURCE FRAME' : state.landmarks[landmarkIndex]?.label || 'TRACK LANDMARK'}`;
    time = sourceTime(time);
    $('alignmentTime').value = time;
    $('alignmentImage').src = $(`${side}Frame`).src;
    $('alignmentFrameStamp').textContent = 'Loading frame...';
    $('alignmentSetLandmark').hidden = landmarkIndex === null;
    $('alignmentSetStart').hidden = landmarkIndex !== null;
    $('alignmentSetEnd').hidden = landmarkIndex !== null;
    $('alignmentDialog').showModal();
    setFrameReady(false);
    updateFrame(side, time);
  }

  function setFrameReady(ready) {
    for (const id of ['alignmentSetStart', 'alignmentSetEnd', 'alignmentSetLandmark']) $(id).disabled = !ready;
  }

  function showInspectorFrame(side, time, url, decodedTime = null) {
    if (state.viewer?.side !== side || !$('alignmentDialog').open || Math.abs(Number($('alignmentTime').value) - Number(time)) > .005) return;
    $('alignmentImage').src = url;
    const displayed = finite(decodedTime) ? Number(decodedTime) : Number(time);
    $('alignmentFrameStamp').textContent = `Frame ${Math.floor(displayed / 60)}:${(displayed % 60).toFixed(3).padStart(6, '0')}`;
    setFrameReady(true);
  }

  function sessionName(side) {
    const run = state.runs[side];
    return run.data?.session?.name || run.data?.session?.source?.name || state.sessions.find(session => session.id === run.id)?.name || names[side];
  }

  async function findCrossing(side) {
    const run = state.runs[side];
    const anchor = Number($(`${side}Start`).value);
    if (!run.id || run.loading || !Number.isFinite(anchor)) return;
    $(`${side}FindCrossing`).disabled = true;
    $(`${side}CrossingCandidates`).hidden = true;
    $(`${side}CrossingStatus`).textContent = 'Scanning track scene...';
    const unchanged = () => state.runs[side] === run && Number($(`${side}Start`).value) === anchor;
    let job = null;
    try {
      job = await helpers.api('/api/lap-detection', {method: 'POST', body: JSON.stringify({session_id: run.id, anchor_time: anchor})});
      run.crossingJob = job.id;
      if (state.runs[side] === run) $(`${side}CancelCrossing`).hidden = false;
      const deadline = Date.now() + 10 * 60 * 1000;
      while (!['completed', 'failed', 'error', 'cancelled'].includes(job.state)) {
        if (!unchanged()) return;
        if (Date.now() > deadline) throw new Error('Crossing search is taking too long. Retry later.');
        await new Promise(resolve => setTimeout(resolve, 900));
        if (!unchanged()) return;
        job = await helpers.api(`/api/lap-detection/${encodeURIComponent(job.id)}`);
      }
      if (!unchanged()) return;
      if (job.state !== 'completed') throw new Error(job.error || (job.state === 'cancelled' ? 'Crossing search cancelled.' : 'Crossing search failed.'));
      run.crossings = job.result?.candidates || [];
      run.crossingAnchor = anchor;
      $(`${side}CrossingChoice`).innerHTML = run.crossings.map((candidate, index) => `<option value="${index}">${stamp(candidate.end_time)} / ${stamp(candidate.duration)} lap / unconfirmed</option>`).join('');
      $(`${side}CrossingCandidates`).hidden = !run.crossings.length;
      $(`${side}CrossingStatus`).textContent = run.crossings.length ? `${run.crossings.length} scene match${run.crossings.length === 1 ? '' : 'es'} / unconfirmed` : job.result?.warnings?.[0] || 'No reliable returning scene found.';
    } catch (error) {
      if (unchanged()) $(`${side}CrossingStatus`).textContent = error.message;
    } finally {
      await cancelUnfinishedDetection(job);
      run.crossingJob = null;
      if (state.runs[side] === run) {
        if (!unchanged()) $(`${side}CrossingStatus`).textContent = 'Start changed. Search cancelled.';
        $(`${side}FindCrossing`).disabled = false;
        $(`${side}CancelCrossing`).hidden = true;
      }
    }
  }

  async function cancelCrossing(side) {
    const run = state.runs[side];
    if (!run.crossingJob) return;
    try {
      await helpers.api(`/api/lap-detection/${encodeURIComponent(run.crossingJob)}/cancel`, {method: 'POST', body: '{}'});
    } catch (error) {
      if (state.runs[side] === run) $(`${side}CrossingStatus`).textContent = error.message;
    }
  }

  function proposedCrossing(side) {
    const run = state.runs[side];
    if (run.crossingAnchor !== Number($(`${side}Start`).value)) {
      $(`${side}CrossingStatus`).textContent = 'Start changed. Search again.';
      return null;
    }
    return run.crossings?.[Number($(`${side}CrossingChoice`).value)] || null;
  }

  function recordingContext(side) {
    const session = state.runs[side].data?.session;
    return session?.context || session?.metadata?.telemetry_context || {};
  }

  function contextLabel(side) {
    const context = recordingContext(side);
    const car = context.car_name || state.catalog?.cars?.find(item => item.id === context.car_id)?.name || context.car_id || 'Unknown car';
    const track = context.track_name || state.catalog?.tracks?.find(item => item.id === context.track_id)?.name || context.track_id || 'Unknown circuit';
    return `${car} / ${track}`;
  }

  async function openContext(side) {
    const run = state.runs[side];
    if (!run.id || run.loading) return;
    const editor = state.contextEditor = {id: run.id, side};
    $('recordingContextName').textContent = sessionName(side);
    $('recordingContextError').textContent = '';
    $('saveRecordingContext').disabled = true;
    $('recordingContextDialog').showModal();
    try {
      state.catalog ||= await helpers.api('/api/catalog');
      if (state.contextEditor !== editor) return;
      $('recordingContextCar').innerHTML = '<option value="">Unknown car</option>' + (state.catalog.cars || []).map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');
      $('recordingContextTrack').innerHTML = '<option value="">Unknown circuit</option>' + (state.catalog.tracks || []).map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');
      const context = recordingContext(side);
      for (const [field, key] of Object.entries({Car: 'car_id', Track: 'track_id', Platform: 'platform', Version: 'game_version', Conditions: 'conditions', Bop: 'bop'})) $('recordingContext' + field).value = context[key] || '';
      $('saveRecordingContext').disabled = false;
    } catch (error) {
      if (state.contextEditor === editor) $('recordingContextError').textContent = error.message;
    }
  }

  async function saveContext() {
    const editor = state.contextEditor;
    if (!editor || state.deletedSessions.has(editor.id)) return;
    $('saveRecordingContext').disabled = true;
    $('recordingContextError').textContent = '';
    try {
      const body = {};
      for (const [field, key] of Object.entries({Car: 'car_id', Track: 'track_id', Platform: 'platform', Version: 'game_version', Conditions: 'conditions', Bop: 'bop'})) body[key] = $('recordingContext' + field).value.trim() || null;
      const response = await helpers.api(`/api/sessions/${encodeURIComponent(editor.id)}/context`, {method: 'PATCH', body: JSON.stringify(body)});
      if (state.deletedSessions.has(editor.id)) return;
      const context = response.context || body;
      let trackChanged = false;
      for (const side of ['current', 'reference']) {
        const run = state.runs[side];
        if (run.id !== editor.id || !run.data?.session) continue;
        trackChanged ||= recordingContext(side).track_id !== context.track_id;
        run.data.session.context = context;
        run.data.session.metadata = {...run.data.session.metadata, telemetry_context: body};
      }
      const session = state.sessions.find(item => item.id === editor.id);
      if (session) session.metadata = {...session.metadata, telemetry_context: body};
      if (trackChanged) resetAlignment(true);
      if (state.contextEditor === editor) $('recordingContextDialog').close();
      clearResult();
      markDirty();
      notifySelectedRecording();
    } catch (error) {
      if (state.contextEditor === editor) $('recordingContextError').textContent = error.message;
    } finally {
      if (state.contextEditor === editor) $('saveRecordingContext').disabled = false;
    }
  }

  function updateLegend() {
    $('comparisonLegendCurrent').textContent = sessionName('current');
    $('comparisonLegendReference').textContent = sessionName('reference');
    for (const side of ['current', 'reference']) {
      $(`${side}Context`).textContent = state.runs[side].id ? contextLabel(side) : 'Car and circuit not labelled';
      $(`${side}EditContext`).disabled = !state.runs[side].id || state.runs[side].loading;
      const legend = $(`comparisonLegend${side === 'current' ? 'Current' : 'Reference'}`).parentElement.querySelector('small');
      legend.textContent = `${side === 'current' ? 'YOUR RUN / SOLID' : 'REFERENCE / DASHED'} / ${contextLabel(side)}`;
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

  async function saveLapRange(side) {
    const run = state.runs[side];
    if (!run.id || $(`${side}Range`).value !== 'custom') return;
    const button = $(`${side}SaveLapRange`);
    button.disabled = true;
    try {
      const range = selectedRange(side);
      const lap = {id: `saved-${Date.now().toString(36)}`, lap_number: 1,
        start_time: range.start_time, end_time: range.end_time, valid: null, boundaries_verified: false,
        ...(range.segment_id != null ? {segment_id: range.segment_id} : {})};
      const metadata = await helpers.api(`/api/sessions/${encodeURIComponent(run.id)}/reviewed-laps`, {
        method: 'PATCH', body: JSON.stringify({lap, make_default: true})
      });
      if (state.runs[side] !== run) return;
      run.data.session.metadata = {...run.data.session.metadata, ...metadata};
      run.defaultsRevision = String(metadata.comparison_defaults?.revision || '');
      run.ranges = makeRanges(run.data);
      const session = state.sessions.find(item => item.id === run.id);
      if (session) session.metadata = {...session.metadata, ...metadata};
      $(`${side}Range`).innerHTML = run.ranges.map(item => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('') + '<option value="custom">Custom time range</option>';
      $(`${side}Range`).value = lap.id;
      chooseRange(side);
      markDirty();
      helpers.notify('Lap boundaries saved. Physical markers remain unverified.');
    } catch (error) {
      helpers.notify(error.message, true);
    } finally {
      if (state.runs[side] === run) button.disabled = $(`${side}Range`).value !== 'custom';
    }
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
      if (!range) throw new Error(`Select ${names[side].toLowerCase()} lap or segment.`);
      return range.selection;
    }
    const start = Number($(`${side}Start`).value),
      end = Number($(`${side}End`).value);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) throw new Error(`${names[side]} end time must follow its start time.`);
    const result = {
      start_time: start,
      end_time: end
    };
    if ($(`${side}Segment`).value !== '') result.segment_id = Number($(`${side}Segment`).value);
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
    return {...(range ? describe(range) : {}), id,
      kind: range?.lap ? 'lap' : id === 'full' ? 'full' : id === 'custom' ? 'custom' : 'segment',
      lap: range?.lap === true, reviewed: range?.reviewed === true,
      boundariesVerified: range?.boundariesVerified === true,
      start: finite(start) ? start : null, end: finite(end) ? end : null,
      duration: finite(resolved?.duration) ? Number(resolved.duration) : finite(start) && finite(end) ? end - start : null,
      selection: resolved?.selection || (range ? {...range.selection} : {start_time: start, end_time: end,
        ...($(`${side}Segment`).value ? {segment_id: Number($(`${side}Segment`).value)} : {})}),
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
    for (const side of ['current', 'reference']) $(`${side}SaveLapRange`).disabled = !state.runs[side].id || $(`${side}Range`).value !== 'custom';
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
      align_start: $('confirmAlignment').checked,
      confirm_same_track: $('confirmSameTrack').checked,
      align_end: $('confirmFinishAlignment').checked,
      alignment_mode: state.alignmentMode,
      landmarks: state.landmarks.map(({label, current_time, reference_time}) => ({label, current_time, reference_time}))
    };
    for (const side of ['current', 'reference']) preferences[side] = {
      id: state.runs[side].id || '',
      range: $(`${side}Range`).value,
      start: $(`${side}Start`).value,
      end: $(`${side}End`).value,
      segment: $(`${side}Segment`).value,
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
        align_start: $('confirmAlignment').checked,
        confirm_same_track: $('confirmSameTrack').checked,
        align_end: $('confirmFinishAlignment').checked,
        alignment_mode: state.axis === 'distance' ? state.alignmentMode : 'raw',
        landmarks: state.axis === 'distance' && state.alignmentMode === 'landmarks' ? selectedLandmarks() : []
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
    const segments = result.observed_segments;
    $('observedSegments').hidden = !state.result || !isPaired();
    $('observedSegmentsNote').textContent = segments?.reason || 'Segment differences need confirmed paired track landmarks.';
    $('observedSegmentsRows').innerHTML = (segments?.segments || []).map(segment => `<tr><td>${esc(segment.start_label)} / ${esc(segment.end_label)}</td><td>${number(segment.current_seconds, 3)}</td><td>${number(segment.reference_seconds, 3)}</td><td class="${segment.delta_seconds > 0 ? 'segment-slower' : 'segment-faster'}">${segment.delta_seconds > 0 ? '+' : ''}${number(segment.delta_seconds, 3)}</td></tr>`).join('');
    $('observedSegmentsRows').closest('.segment-table-scroll').hidden = !segments?.available;
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
    const registrationFallback = result.alignment?.requested_mode && !result.alignment.applied && result.alignment.fallback_reason;
    $('registrationStatus').title = registrationFallback || '';
    if (registrationFallback) {
      $('registrationStatus').textContent = 'Raw distance / registration unavailable';
      $('registrationStatus').classList.add('unconfirmed');
    } else if (result.alignment?.applied) {
      $('registrationStatus').textContent = `${result.alignment.mode === 'landmarks' ? 'Landmarks' : 'Endpoints'} / ${result.alignment.anchors?.length || 2} anchors / estimated`;
      $('registrationStatus').classList.remove('unconfirmed');
    }
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
        $('activeImportDetail').textContent = `${active.state}${active.processed_frames?' / '+Number(active.processed_frames).toLocaleString()+' frames':''}${active.calibration?.status?' / HUD '+active.calibration.status:''}`;
        $('activeImportProgress').style.width = `${finite(active.progress)?clamp(active.progress,0,100):5}%`;
        $('activeImportPercent').textContent = finite(active.progress) ? `${number(active.progress)}%` : '';
      }
      const available = new Set(state.sessions.map(session => session.id || session.session_id));
      $('referenceImportHistory').innerHTML = state.jobs.slice(0, 6).map(job => `<div class="import-job"><div><strong>${esc(job.name||job.url||job.path||'Recording')}</strong><small>${esc(job.error||job.state)}${job.calibration?.status?' / HUD '+esc(job.calibration.status):''}</small></div>${job.state==='completed'&&job.session_id&&available.has(job.session_id)&&!state.deletedSessions.has(job.session_id)?`<button class="button subtle compact import-use" data-session="${esc(job.session_id)}" data-role="${job.role === 'current' ? 'current' : 'reference'}">Use ${job.role === 'current' ? 'your run' : 'reference'}</button>`:!['completed','cancelled','error'].includes(job.state)?`<button class="icon-button import-cancel" data-id="${esc(job.id)}" title="Cancel import" aria-label="Cancel import"><i data-lucide="x"></i></button>`:''}</div>`).join('');
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
    $('closeRecordingContext').onclick = () => $('recordingContextDialog').close();
    $('recordingContextDialog').addEventListener('close', () => { state.contextEditor = null; });
    $('saveRecordingContext').onclick = saveContext;
    $('confirmSameTrack').onchange = () => markDirty();
    for (const side of ['current', 'reference']) {
      $(`${side}EditContext`).onclick = () => openContext(side);
      $(`${side}DeleteSession`).onclick = async () => {
        const id = state.runs[side].id;
        if (!id || !helpers.deleteSession) return;
        try {
          await helpers.deleteSession(id);
        } catch (error) {
          helpers.notify(error.message, true);
        }
      };
      $(`${side}FindCrossing`).onclick = () => findCrossing(side);
      $(`${side}SaveLapRange`).onclick = () => saveLapRange(side);
      $(`${side}CancelCrossing`).onclick = () => cancelCrossing(side);
      $(`${side}InspectCrossing`).onclick = () => {
        const crossing = proposedCrossing(side);
        if (crossing) openFrame(side, crossing.end_time);
      };
      $(`${side}ApplyCrossing`).onclick = () => {
        const crossing = proposedCrossing(side);
        if (!crossing) return;
        $(`${side}End`).value = Number(crossing.end_time).toFixed(3);
        $(`${side}Range`).value = 'custom';
        $(`${side}Segment`).hidden = false;
        resetAlignment(false, 'End');
        markDirty();
        updateFrame(side, crossing.end_time);
        $(`${side}CrossingStatus`).textContent = 'OUT updated / finish crossing unconfirmed';
      };
      $(`${side}Session`).onchange = () => loadRun(side, $(`${side}Session`).value);
      $(`${side}Range`).onchange = () => {
        resetAlignment(false);
        chooseRange(side);
        applyReviewedAlignment(true);
        markDirty();
      };
      for (const edge of ['Start', 'End']) $(`${side}${edge}`).onchange = () => {
        resetAlignment(false, edge);
        $(`${side}Range`).value = 'custom';
        $(`${side}Segment`).hidden = false;
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
      $(`${side}Segment`).onchange = () => { resetAlignment(false); markDirty(); };
    }
    for (const button of document.querySelectorAll('.inspect-boundary')) button.onclick = () => openFrame(button.dataset.side, Number($(`${button.dataset.side}${button.dataset.edge}`).value));
    $('closeAlignment').onclick = () => $('alignmentDialog').close();
    $('alignmentTime').oninput = () => {
      setFrameReady(false);
      $('alignmentFrameStamp').textContent = 'Loading frame...';
      updateFrame(state.viewer.side, Number($('alignmentTime').value));
    };
    for (const [id, direction] of [
        ['alignmentBack', -1],
        ['alignmentForward', 1],
        ['alignmentFrameBack', -1 / 30],
        ['alignmentFrameForward', 1 / 30]
      ]) $(id).onclick = () => {
      $('alignmentTime').value = Math.max(0, Number($('alignmentTime').value) + direction).toFixed(3);
      setFrameReady(false);
      $('alignmentFrameStamp').textContent = 'Loading frame...';
      updateFrame(state.viewer.side, Number($('alignmentTime').value));
    };
    for (const [id, edge] of [
        ['alignmentSetStart', 'Start'],
        ['alignmentSetEnd', 'End']
      ]) $(id).onclick = () => {
      const side = state.viewer.side;
      resetAlignment(false, edge);
      $(`${side}${edge}`).value = $('alignmentTime').value;
      $(`${side}Range`).value = 'custom';
      $(`${side}Segment`).hidden = false;
      markDirty();
      helpers.notify(`${names[side]} ${edge === 'Start' ? 'IN' : 'OUT'} set to ${stamp(Number($('alignmentTime').value))}.`);
    };
    $('alignmentSetLandmark').onclick = () => {
      const {side, landmarkIndex} = state.viewer;
      const time = Number($('alignmentTime').value);
      const landmark = state.landmarks[landmarkIndex];
      if (!landmark || !Number.isFinite(time) || time < 0) return;
      landmark[`${side}_time`] = time;
      renderLandmarks();
      markDirty();
      $('alignmentDialog').close();
    };
    $('comparisonAlignmentMode').onchange = () => {
      state.alignmentMode = $('comparisonAlignmentMode').value;
      markDirty();
    };
    $('confirmFinishAlignment').onchange = () => markDirty();
    $('addComparisonLandmark').onclick = () => {
      state.landmarks.push({label: '', current_time: null, reference_time: null});
      renderLandmarks();
      markDirty(false);
      $('landmarkRows').lastElementChild.querySelector('input').focus();
    };
    $('landmarkRows').onchange = event => {
      const row = event.target.closest('[data-landmark]');
      if (!row || !event.target.dataset.field) return;
      state.landmarks[Number(row.dataset.landmark)][event.target.dataset.field] = event.target.value;
      $('landmarkError').textContent = '';
      markDirty();
    };
    $('landmarkRows').oninput = event => {
      const row = event.target.closest('[data-landmark]');
      if (row && event.target.dataset.field) {
        state.landmarks[Number(row.dataset.landmark)][event.target.dataset.field] = event.target.value;
        invalidateExport();
      }
    };
    $('landmarkRows').onclick = event => {
      const row = event.target.closest('[data-landmark]');
      if (!row) return;
      const index = Number(row.dataset.landmark);
      if (event.target.closest('.find-landmark-match')) {
        findLandmarkMatch(index);
        return;
      }
      if (event.target.closest('.inspect-landmark-match')) {
        const candidate = state.landmarks[index].matches?.[Number(row.querySelector('.landmark-match-choice').value)];
        if (candidate) openFrame('reference', candidate.target_time, index);
        return;
      }
      if (event.target.closest('.remove-landmark')) {
        state.landmarks.splice(index, 1);
        renderLandmarks();
        markDirty();
        return;
      }
      const frameButton = event.target.closest('.landmark-frame');
      if (frameButton) {
        const side = frameButton.dataset.side;
        const time = state.landmarks[index][`${side}_time`];
        openFrame(side, finite(time) ? Number(time) : pointAt(state.cursor)?.[side]?.time ?? Number($(`${side}Start`).value), index);
      }
    };
    $('refreshComparisons').onclick = () => refresh(true);
    $('runComparison').onclick = compare;
    $('exportComparison').onclick = exportComparison;
    $('confirmAlignment').onchange = () => markDirty();
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
    $('openReferenceImport').onclick = () => openImport('reference');
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
          auto_calibrate: $('referenceAutoCalibration').checked
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
