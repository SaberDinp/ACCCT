/* ACCC Telemetry: recorded-lap analysis, recording library and HUD calibration. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const colors = {brake: '#ff685e', throttle: '#65d8a1', speed: '#69cce0'};
  const state = {view: 'analysis', analysisReview: null, profile: null, profileDirty: false,
    calibrationImage: null, drag: null};
  const icons = () => window.lucide?.createIcons();
  const deletedSessions = new Set();
  let deletionToken = 0, deletionPreview = null, deletionPending = false;
  let calibrationToken = 0;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
  const seconds = value => {
    if (!finite(value)) return '--:--';
    const t = Math.max(0, Number(value));
    return `${Math.floor(t / 60).toString().padStart(2, '0')}:${(t % 60).toFixed(1).padStart(4, '0')}`;
  };
  const notify = (message, error = false) => {
    $('noticeText').textContent = message;
    $('notice').classList.remove('hidden');
    $('notice').style.background = error ? '#34211d' : '';
  };
  async function api(path, opts = {}) {
    const response = await fetch(path, {
      ...opts,
      headers: {
        ...(opts.body && !(opts.body instanceof Blob) && !(opts.body instanceof FormData) ? {
          'Content-Type': 'application/json'
        } : {}),
        ...opts.headers
      }
    });
    if (!response.ok) {
      let message;
      try {
        const body = await response.json();
        message = typeof body.detail === 'string' ? body.detail : body.error || JSON.stringify(body.detail || body);
      } catch {
        message = await response.text();
      }
      throw new Error(message || `Request failed (${response.status})`);
    }
    if (response.status === 204) return null;
    return response.json();
  }
  async function act(button, fn) {
    if (button) button.disabled = true;
    try {
      return await fn();
    } catch (error) {
      notify(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function setView(view) {
    if (!['analysis', 'sessions', 'calibration', 'credits'].includes(view)) view = 'analysis';
    state.view = view;
    window.ACCCComparison?.setActive(view === 'analysis');
    document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === `view-${view}`));
    document.querySelectorAll('.main-tabs [data-view]').forEach(el => {
      const current = el.dataset.view === (view === 'credits' ? 'credits' : 'analysis');
      el.classList.toggle('active', current);
      el.setAttribute('aria-current', current ? 'page' : 'false');
    });
    if (view === 'sessions') loadSessions();
    if (view === 'analysis') renderAnalysis();
    if (view === 'calibration') {loadProfile(); loadCalibrationRecordings();}
    requestAnimationFrame(drawCharts);
  }
  function drawCharts() {
    if (state.view === 'analysis') window.ACCCComparison?.resize();
  }
  function renderAnalysis() {
    const selected = state.analysisReview;
    $('analysisSession').textContent = selected?.session?.name || selected?.session?.source?.name || selected?.session?.source_name || 'No recording selected';
    window.ACCCComparison?.refresh();
  }
  async function loadSessions() {
    try {
      const data = await api('/api/sessions'),
        sessions = (Array.isArray(data) ? data : data.sessions || []).filter(s => !deletedSessions.has(s.id || s.session_id));
      $('sessionsTable').innerHTML = sessions.length ? sessions.map(s => {
        const id = s.id || s.session_id,
          date = s.started_at || s.created_at || s.started;
        let dateString = '--';
        if (date) {
          const d = new Date(typeof date === 'number' ? date * 1000 : date);
          dateString = Number.isNaN(d.getTime()) ? String(date) : d.toLocaleString();
        }
        return `<tr><td><strong class="session-name">${esc(s.name||s.source_name||s.source?.name||'Driving session')}</strong><span class="session-id">${esc(String(id).slice(0,12))}</span></td><td>${esc(dateString)}</td><td class="mono">${seconds(s.duration??s.elapsed_seconds??s.summary?.elapsed_seconds)}</td><td class="mono">${Number(s.sample_count??s.samples??0).toLocaleString()}</td><td>${esc(s.source_kind||s.kind||s.source?.kind||'Video')}</td><td><div class="table-actions"><a class="icon-button" href="/api/export/${encodeURIComponent(id)}.csv" title="Export CSV" aria-label="Export CSV"><i data-lucide="download"></i></a><button class="button subtle compact session-open" data-id="${esc(id)}"><i data-lucide="play"></i>Review</button><button class="icon-button session-delete" data-id="${esc(id)}" title="Delete recording" aria-label="Delete recording"><i data-lucide="trash-2"></i></button></div></td></tr>`;
      }).join('') : '<tr><td colspan="6" class="empty-cell">No saved sessions yet.</td></tr>';
      icons();
    } catch (error) {
      notify(error.message, true);
    }
  }
  async function loadSession(id) {
    if (deletedSessions.has(id)) return;
    setView('analysis');
    await window.ACCCComparison?.openRecording(id);
  }
  async function openDeleteSession(id) {
    if (!id || deletionPending) return;
    const token = ++deletionToken;
    deletionPreview = null;
    $('deleteSessionName').textContent = 'Recording';
    $('deleteSessionError').textContent = '';
    $('deleteSessionVideoReason').textContent = 'Checking recording...';
    $('deleteSessionVideo').checked = false;
    $('deleteSessionVideo').disabled = true;
    $('deleteSessionConfirm').disabled = true;
    $('deleteSessionDialog').showModal();
    try {
      const preview = await api(`/api/sessions/${encodeURIComponent(id)}/deletion`);
      if (token !== deletionToken || !$('deleteSessionDialog').open) return;
      deletionPreview = preview;
      $('deleteSessionName').textContent = preview.name || 'Recording';
      $('deleteSessionVideo').disabled = !preview.video.can_delete;
      $('deleteSessionVideoReason').textContent = preview.video.can_delete ? 'The video will be kept unless selected.' : preview.video.reason;
      $('deleteSessionError').textContent = preview.blocked_reason || '';
      $('deleteSessionConfirm').disabled = !preview.can_delete;
    } catch (error) {
      if (token === deletionToken && $('deleteSessionDialog').open) $('deleteSessionError').textContent = error.message;
    }
  }

  async function confirmDeleteSession() {
    if (!deletionPreview?.can_delete || deletionPending) return;
    const id = deletionPreview.session_id;
    deletionPending = true;
    $('deleteSessionDialog').setAttribute('aria-busy', 'true');
    for (const name of ['deleteSessionConfirm', 'deleteSessionCancel', 'deleteSessionClose', 'deleteSessionVideo']) $(name).disabled = true;
    $('deleteSessionError').textContent = '';
    try {
      const result = await api(`/api/sessions/${encodeURIComponent(id)}?delete_video=${$('deleteSessionVideo').checked}`, {method: 'DELETE'});
      deletedSessions.add(id);
      if (state.analysisReview?.session?.id === id) state.analysisReview = null;
      if ($('calibrationRecording').value === id) {state.calibrationImage = null; drawCalibration();}
      $('deleteSessionDialog').close();
      const warnings = result.warnings || [];
      notify(warnings.length ? warnings.join(' ') : result.video_deleted ? 'Recording and video deleted.' : result.video_retained ? 'Recording deleted. Video file kept.' : 'Recording deleted.', warnings.length > 0);
      await window.ACCCComparison?.removeSession(id);
      await loadSessions();
      if (state.view === 'calibration') await loadCalibrationRecordings();
      renderAnalysis();
    } catch (error) {
      $('deleteSessionError').textContent = error.message;
    } finally {
      deletionPending = false;
      $('deleteSessionDialog').removeAttribute('aria-busy');
      for (const name of ['deleteSessionCancel', 'deleteSessionClose']) $(name).disabled = false;
      $('deleteSessionConfirm').disabled = !deletionPreview?.can_delete;
      $('deleteSessionVideo').disabled = !deletionPreview?.video.can_delete;
    }
  }
  async function loadProfile(force = false) {
    if (state.profileDirty && !force) return;
    try {
      state.profile = await api('/api/profile');
      state.profileDirty = false;
      populateProfile();
    } catch (error) {
      $('profileMessage').textContent = error.message;
    }
  }

  function profileRegions() {
    const p = state.profile || {};
    return p.regions || p.rois || p.channels || p;
  }

  function getRegion(channel) {
    const raw = profileRegions()[channel] || {};
    const roi = raw.roi || raw;
    return Array.isArray(roi) ? {
      x: roi[0],
      y: roi[1],
      w: roi[2],
      h: roi[3]
    } : {
      x: roi.x ?? 0,
      y: roi.y ?? 0,
      w: roi.w ?? roi.width ?? .05,
      h: roi.h ?? roi.height ?? .05
    };
  }

  function putRegion(channel, roi) {
    const regions = profileRegions(),
      old = regions[channel];
    if (Array.isArray(old)) regions[channel] = [roi.x, roi.y, roi.w, roi.h];
    else if (old?.roi) old.roi = Array.isArray(old.roi) ? [roi.x, roi.y, roi.w, roi.h] : roi;
    else regions[channel] = {
      ...(old || {}),
      ...roi
    };
    state.profileDirty = true;
  }

  function populateProfile() {
    const p = state.profile || {};
    $('profileName').value = p.name || 'ACC PS5';
    $('speedUnit').value = p.speed_unit || 'auto';
    $('hudLayout').value = p.hud_layout || 'auto';
    $('autoCalibration').checked = p.auto_calibrate !== false;
    $('minConfidence').value = p.minimum_anchor_score ?? .55;
    $('minConfidenceLabel').textContent = `${Math.round($('minConfidence').value*100)}%`;
    populateRegion();
  }

  function populateRegion() {
    const channel = $('roiChannel').value,
      roi = getRegion(channel);
    for (const key of ['x', 'y', 'w', 'h']) $('roi' + key.toUpperCase()).value = Number(roi[key]).toFixed(4);
    const raw = profileRegions()[channel];
    $('roiDirection').value = state.profile?.[`${channel}_direction`] || raw?.direction || 'lr';
    $('directionField').hidden = !['brake', 'throttle'].includes(channel);
    $('calibrationStatus').textContent = channel.charAt(0).toUpperCase() + channel.slice(1);
    drawCalibration();
  }
  async function loadCalibrationRecordings() {
    try {
      const sessions = await api('/api/sessions');
      const previous = $('calibrationRecording').value || state.analysisReview?.session?.id;
      const files = sessions.filter(item => item.source?.kind === 'file' && !deletedSessions.has(item.id));
      $('calibrationRecording').replaceChildren(new Option(files.length ? 'Choose a recording' : 'Import a recording first', ''),
        ...files.map(item => new Option(item.name || item.source?.name || 'Recording', item.id)));
      $('calibrationRecording').value = files.some(item => item.id === previous) ? previous : files[0]?.id || '';
      if ($('calibrationRecording').value) await freezeCalibration();
    } catch (error) { $('calibrationStatus').textContent = error.message; }
  }
  async function freezeCalibration() {
    const token = ++calibrationToken, id = $('calibrationRecording').value;
    if (!id) {state.calibrationImage = null; drawCalibration(); return;}
    try {
      const time = Math.max(0, Number($('calibrationTime').value) || 0);
      const img = new Image();
      img.src = `/api/sessions/${encodeURIComponent(id)}/frame.jpg?time=${time}&width=1920`;
      await img.decode();
      if (token !== calibrationToken) return;
      state.calibrationImage = img;
      drawCalibration();
    } catch {
      if (token !== calibrationToken) return;
      state.calibrationImage = null;
      $('calibrationStatus').textContent = 'This recording frame is unavailable. Choose another time or recording.';
      drawCalibration();
    }
  }
  function drawCalibration() {
    const canvas = $('calibrationCanvas'),
      ctx = canvas.getContext('2d'),
      img = state.calibrationImage;
    if (img) {
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      ctx.drawImage(img, 0, 0);
    } else {
      ctx.fillStyle = '#0a0e0c';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#526458';
      ctx.font = '28px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Choose a saved recording to preview its HUD', canvas.width / 2, canvas.height / 2);
      return;
    }
    for (const channel of ['brake', 'throttle', 'speed', 'gear', 'rpm', 'steering', 'lap_time', 'labels']) {
      const roi = getRegion(channel);
      if (!profileRegions()[channel]) continue;
      const active = channel === $('roiChannel').value;
      ctx.strokeStyle = colors[channel] || '#d8ee88';
      ctx.lineWidth = active ? Math.max(2, canvas.width / 500) : Math.max(1, canvas.width / 1000);
      ctx.setLineDash(active ? [] : [8, 5]);
      ctx.strokeRect(roi.x * canvas.width, roi.y * canvas.height, roi.w * canvas.width, roi.h * canvas.height);
      if (active) {
        ctx.font = `${Math.max(14,canvas.width/80)}px sans-serif`;
        ctx.textAlign = 'left';
        ctx.fillStyle = ctx.strokeStyle;
        ctx.fillText(channel.toUpperCase(), roi.x * canvas.width, Math.max(20, roi.y * canvas.height - 9));
      }
    }
    ctx.setLineDash([]);
  }

  function bind() {
    document.querySelectorAll('[data-view]').forEach(el => el.onclick = () => setView(el.dataset.view));
    document.querySelectorAll('[data-close]').forEach(el => el.onclick = () => $(el.dataset.close).close());
    document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', e => {
      if (dialog.id === 'deleteSessionDialog' && deletionPending) return;
      if (e.target === dialog) {
        const r = dialog.getBoundingClientRect();
        if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
      }
    }));
    $('deleteSessionDialog').addEventListener('cancel', e => {if (deletionPending) e.preventDefault();});
    $('deleteSessionDialog').addEventListener('close', () => {deletionToken++;});
    $('deleteSessionConfirm').onclick = confirmDeleteSession;
    $('noticeDismiss').onclick = () => $('notice').classList.add('hidden');
    $('importRecording').onclick = () => {setView('analysis'); window.ACCCComparison?.openImport('current');};
    $('refreshSessions').onclick = () => act($('refreshSessions'), loadSessions);
    $('sessionsTable').onclick = e => {
      const button = e.target.closest('.session-open');
      if (button) act(button, () => loadSession(button.dataset.id));
      const remove = e.target.closest('.session-delete');
      if (remove) openDeleteSession(remove.dataset.id);
    };
    $('calibrationRecording').onchange = freezeCalibration;
    $('calibrationTime').onchange = freezeCalibration;
    $('roiChannel').onchange = populateRegion;
    for (const key of ['X', 'Y', 'W', 'H']) $('roi' + key).onchange = () => {
      const roi = Object.fromEntries(['x', 'y', 'w', 'h'].map(k => [k, clamp(Number($('roi' + k.toUpperCase()).value), 0, 1)]));
      roi.w = Math.max(.001, Math.min(roi.w, 1 - roi.x));
      roi.h = Math.max(.001, Math.min(roi.h, 1 - roi.y));
      putRegion($('roiChannel').value, roi);
      populateRegion();
    };
    $('roiDirection').onchange = () => {
      const channel = $('roiChannel').value;
      state.profile[`${channel}_direction`] = $('roiDirection').value;
      state.profileDirty = true;
    };
    $('calibrationZoom').onchange = () => $('calibrationCanvas').style.width = `${Number($('calibrationZoom').value)*100}%`;
    $('refreshCalibration').onclick = freezeCalibration;
    $('reloadProfile').onclick = () => {
      state.profileDirty = false;
      loadProfile(true);
    };
    $('minConfidence').oninput = () => {
      $('minConfidenceLabel').textContent = `${Math.round($('minConfidence').value*100)}%`;
      state.profileDirty = true;
    };
    for (const id of ['profileName', 'speedUnit', 'hudLayout', 'autoCalibration']) $(id).onchange = () => state.profileDirty = true;
    $('saveProfile').onclick = () => act($('saveProfile'), async () => {
      const p = state.profile || {};
      p.name = $('profileName').value;
      p.speed_unit = $('speedUnit').value;
      p.hud_layout = $('hudLayout').value;
      p.auto_calibrate = $('autoCalibration').checked;
      p.minimum_anchor_score = Number($('minConfidence').value);
      const result = await api('/api/profile', {
        method: 'PUT',
        body: JSON.stringify(p)
      });
      state.profile = result?.profile || result || p;
      state.profileDirty = false;
      populateProfile();
      $('profileMessage').textContent = 'Profile saved.';
    });
    $('downloadProfile').onclick = () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(state.profile, null, 2)], {
          type: 'application/json'
        })),
        a = document.createElement('a');
      a.href = url;
      a.download = 'accc-hud-profile.json';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    const canvas = $('calibrationCanvas'),
      point = e => {
        const r = canvas.getBoundingClientRect();
        return {
          x: clamp((e.clientX - r.left) / r.width, 0, 1),
          y: clamp((e.clientY - r.top) / r.height, 0, 1)
        };
      };
    canvas.onpointerdown = e => {
      if (!state.calibrationImage || !state.profile) return;
      state.drag = point(e);
      canvas.setPointerCapture(e.pointerId);
    };
    canvas.onpointermove = e => {
      if (!state.drag) return;
      const p = point(e),
        s = state.drag;
      putRegion($('roiChannel').value, {
        x: Math.min(s.x, p.x),
        y: Math.min(s.y, p.y),
        w: Math.max(.001, Math.abs(s.x - p.x)),
        h: Math.max(.001, Math.abs(s.y - p.y))
      });
      populateRegion();
    };
    canvas.onpointerup = () => state.drag = null;
    canvas.onpointercancel = () => state.drag = null;
    window.addEventListener('resize', drawCharts);
  }
  function boot() {
    bind();
    window.ACCCComparison?.init({api, notify, icons,
      currentSessionId: () => state.analysisReview?.session?.id || null,
      deleteSession: openDeleteSession,
      onCurrentLoaded: data => {
        state.analysisReview = data;
        $('analysisSession').textContent = data?.session?.name || data?.session?.source?.name || 'Select a recording to begin';
      }
    });
    const updateClock = () => {$('footerClock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});};
    updateClock(); setInterval(updateClock, 30000);
    setView(location.hash === '#credits' ? 'credits' : 'analysis');
    window.addEventListener('hashchange', () => {if (location.hash === '#credits') setView('credits');});
    icons();
  }
  window.ACCCTelemetry = {state, drawCharts, setView};
  boot();
})();
