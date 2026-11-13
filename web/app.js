/* ACCC Telemetry: recorded-lap analysis and Rice Racing credits. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const state = {view: 'analysis', analysisReview: null};
  const icons = () => window.lucide?.createIcons();
  let deletionToken = 0, deletionPreview = null, deletionPending = false;
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
  function setView(view) {
    if (!['analysis', 'credits'].includes(view)) view = 'analysis';
    state.view = view;
    window.ACCCComparison?.setActive(view === 'analysis');
    document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === `view-${view}`));
    document.querySelectorAll('.main-tabs [data-view]').forEach(el => {
      const current = el.dataset.view === (view === 'credits' ? 'credits' : 'analysis');
      el.classList.toggle('active', current);
      el.setAttribute('aria-current', current ? 'page' : 'false');
    });
    if (view === 'analysis') renderAnalysis();
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
      if (state.analysisReview?.session?.id === id) state.analysisReview = null;
      $('deleteSessionDialog').close();
      const warnings = result.warnings || [];
      notify(warnings.length ? warnings.join(' ') : result.video_deleted ? 'Recording and video deleted.' : result.video_retained ? 'Recording deleted. Video file kept.' : 'Recording deleted.', warnings.length > 0);
      await window.ACCCComparison?.removeSession(id);
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
