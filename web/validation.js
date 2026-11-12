/* Read-only corpus coverage: roster membership is not extraction validation. */
'use strict';
window.ACCCValidation = (() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
  const labels = {not_tested: 'Not tested', discovered: 'Candidates only', smoke_only: 'Smoke check only',
    analysis_needs_review: 'Analysis needs review', partially_reviewed: 'Partially reviewed',
    review_target_met: 'Review target met', download_error: 'Download / analysis error',
    stale_analysis: 'Needs reanalysis', channel_review_only: 'Some channels reviewed'};
  const videoLabels = {candidate: 'Candidate only', smoke_checked: 'Smoke checked', analyzed: 'Analyzed / not reviewed',
    reviewed: 'Independently reviewed', error: 'Error', stale_analysis: 'Needs reanalysis', channel_review_only: 'Some channels reviewed'};
  const state = {report: null, page: 0, pageSize: 50, expanded: new Set(), loading: false, ready: false};
  const icons = () => window.lucide?.createIcons();
  const count = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;

  function init() {
    const parent = $('view-calibration');
    if (state.ready || !parent) return;
    state.ready = true;
    const section = document.createElement('section');
    section.id = 'compatibilityCoverage';
    section.className = 'compatibility-coverage';
    section.innerHTML = '<div class="coverage-heading"><h2>Car and circuit validation</h2><button class="icon-button" id="refreshValidation" aria-label="Refresh validation coverage" title="Refresh validation coverage"><i data-lucide="refresh-cw"></i></button></div><p class="coverage-scope" id="validationScope">Not tested is not supported: discovery and extraction checks are not accuracy validation.</p><div class="coverage-counts" id="validationCounts"></div><div class="coverage-filters"><label>Car<select id="validationCar"><option value="">All GT3 cars</option></select></label><label>Circuit<select id="validationTrack"><option value="">All circuit layouts</option></select></label><label>Review state<select id="validationState"><option value="">All states</option></select></label></div><div class="coverage-status" id="validationStatus" role="status">Coverage has not been loaded.</div><div class="coverage-table-scroll"><table class="coverage-table"><thead><tr><th><span class="sr-only">Details</span></th><th>Car / circuit</th><th>State</th><th>Candidates</th><th>Smoke checked</th><th>Analyzed</th><th id="validationReviewedHeading">Reviewed / 3</th></tr></thead><tbody id="validationRows"></tbody></table></div><div class="coverage-pagination"><span id="validationPageCount"></span><button class="icon-button" id="validationPrevious" aria-label="Previous validation page" title="Previous page" disabled><i data-lucide="chevron-left"></i></button><button class="icon-button" id="validationNext" aria-label="Next validation page" title="Next page" disabled><i data-lucide="chevron-right"></i></button></div>';
    parent.appendChild(section);
    $('validationScope').textContent = 'Untested pairs have no validation evidence. Discovery and extraction checks are not accuracy validation.';
    $('validationState').insertAdjacentHTML('beforeend', Object.entries(labels).map(([value, label]) => `<option value="${value}">${label}</option>`).join(''));
    for (const id of ['validationCar', 'validationTrack', 'validationState']) $(id).onchange = () => { state.page = 0; render(); };
    $('refreshValidation').onclick = refresh;
    $('validationPrevious').onclick = () => { state.page--; render(); };
    $('validationNext').onclick = () => { state.page++; render(); };
    $('validationRows').onclick = event => {
      const button = event.target.closest('.coverage-expand');
      if (!button) return;
      state.expanded.has(button.dataset.key) ? state.expanded.delete(button.dataset.key) : state.expanded.add(button.dataset.key);
      render();
    };
    const observer = new MutationObserver(() => {
      if (parent.classList.contains('active') && !state.report && !state.loading) refresh();
    });
    observer.observe(parent, {attributes: true, attributeFilter: ['class']});
    if (parent.classList.contains('active')) refresh();
    icons();
  }

  async function refresh() {
    if (state.loading) return;
    state.loading = true;
    $('refreshValidation').disabled = true;
    $('refreshValidation').setAttribute('aria-busy', 'true');
    $('validationStatus').textContent = 'Loading validation records...';
    try {
      const response = await fetch('/api/validation', {cache: 'no-store'});
      const report = await response.json();
      if (!response.ok) throw new Error(report.detail || 'Validation records are unavailable.');
      if (!Array.isArray(report.pairs)) throw new Error('Validation report is not available from this server.');
      state.report = report;
      for (const [id, key, name, title] of [['validationCar', 'car_id', 'car_name', 'All GT3 cars'], ['validationTrack', 'track_id', 'track_name', 'All circuit layouts']]) {
        const selected = $(id).value;
        const entries = [...new Map(report.pairs.map(pair => [pair[key], pair[name]])).entries()].sort((a, b) => String(a[1]).localeCompare(String(b[1])));
        $(id).innerHTML = `<option value="">${title}</option>` + entries.map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join('');
        if (entries.some(([value]) => value === selected)) $(id).value = selected;
      }
      $('validationScope').textContent = report.scope || 'Title hints are not verified identities. Extraction checks are not accuracy validation.';
      $('validationReviewedHeading').textContent = `Reviewed / ${count(report.target_per_pair) || 3}`;
      const sums = key => report.pairs.reduce((total, pair) => total + count(pair[key]), 0);
      const statistics = [[report.pairs.length, 'car / circuit pairs'], [count(report.pairs_with_candidates), 'pairs with candidates'], [sums('smoke_checked'), 'smoke checked'], [sums('analyzed'), 'fully analyzed'], [sums('reviewed'), 'reviewed recordings'], [count(report.pairs_review_target_met), 'pairs at review target']];
      $('validationCounts').innerHTML = statistics.map(([value, label]) => `<div><strong>${value.toLocaleString()}</strong><span>${label}</span></div>`).join('');
      const time = new Date(report.generated_at);
      $('validationStatus').textContent = `Read-only coverage${Number.isNaN(time.getTime()) ? '' : ' / ' + time.toLocaleString()}`;
      render();
    } catch (error) {
      $('validationStatus').textContent = `${state.report ? 'Showing previous report. ' : ''}${error.message}`;
    } finally {
      state.loading = false;
      $('refreshValidation').disabled = false;
      $('refreshValidation').setAttribute('aria-busy', 'false');
    }
  }

  function videoMarkup(video) {
    let url;
    try {
      const candidate = new URL(video.url);
      if (candidate.protocol === 'https:' && ['youtube.com', 'www.youtube.com', 'youtu.be'].includes(candidate.hostname) && !candidate.username && !candidate.password) url = candidate.href;
    } catch { /* Invalid corpus URLs remain plain text. */ }
    const title = esc(video.title || video.id || 'Untitled video');
    const identity = video.identity_basis === 'unverified_title' ? 'Discovery car / circuit: unverified title hints' : 'Identity evidence not shown';
    const coverage = Object.entries(video.coverage || {}).map(([channel, value]) => `${channel}: ${Number.isFinite(Number(value)) ? Number(value).toFixed(1) + '%' : 'unavailable'}`).join(' / ');
    return `<li><div>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${title}<i data-lucide="external-link" aria-hidden="true"></i></a>` : `<span>${title}</span>`}<strong>${esc(videoLabels[video.state] || 'Unrecognized state')}</strong></div><p>${esc(identity)}${video.uploader ? ' / ' + esc(video.uploader) : ''}</p>${coverage ? `<p>Observed channel coverage / ${esc(coverage)}</p>` : ''}${video.error ? `<p class="coverage-error">${esc(video.error)}</p>` : ''}</li>`;
  }

  function render() {
    if (!state.report) return;
    const filtered = state.report.pairs.filter(pair => (!$('validationCar').value || pair.car_id === $('validationCar').value) && (!$('validationTrack').value || pair.track_id === $('validationTrack').value) && (!$('validationState').value || pair.state === $('validationState').value));
    const pages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
    state.page = Math.max(0, Math.min(state.page, pages - 1));
    const start = state.page * state.pageSize;
    $('validationRows').innerHTML = filtered.slice(start, start + state.pageSize).map(pair => {
      const key = `${pair.car_id}:${pair.track_id}`;
      const expanded = state.expanded.has(key), videos = Array.isArray(pair.videos) ? pair.videos : [];
      const status = Object.hasOwn(labels, pair.state) ? pair.state : 'not_tested';
      return `<tr><td><button class="icon-button coverage-expand" data-key="${esc(key)}" aria-label="${expanded ? 'Collapse' : 'Expand'} ${esc(pair.car_name)} at ${esc(pair.track_name)} recordings" title="${expanded ? 'Collapse' : 'Expand'} recordings" aria-expanded="${expanded}" ${videos.length ? '' : 'disabled'}><i data-lucide="${expanded ? 'chevron-down' : 'chevron-right'}"></i></button></td><td><strong>${esc(pair.car_name)}</strong><span>${esc(pair.track_name)}</span></td><td><span class="coverage-state ${status}">${labels[status]}</span></td>${['candidates', 'smoke_checked', 'analyzed', 'reviewed'].map(field => `<td>${count(pair[field])}</td>`).join('')}</tr>${expanded && videos.length ? `<tr class="coverage-detail"><td colspan="7"><ul>${videos.map(videoMarkup).join('')}</ul></td></tr>` : ''}`;
    }).join('') || '<tr><td colspan="7" class="coverage-no-results">No car / circuit pairs match these filters.</td></tr>';
    $('validationPageCount').textContent = `${filtered.length ? start + 1 : 0}-${Math.min(start + state.pageSize, filtered.length)} of ${filtered.length} pairs / page ${state.page + 1} of ${pages}`;
    $('validationPrevious').disabled = state.page === 0;
    $('validationNext').disabled = state.page >= pages - 1;
    icons();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
  return {refresh};
})();
