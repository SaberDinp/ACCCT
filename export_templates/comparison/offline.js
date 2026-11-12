/* Offline data adapter for the production Analysis review modules. */
'use strict';
window.ACCCSharedReview = (() => {
  const clone = value => JSON.parse(JSON.stringify(value));
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const same = (a, b) => finite(a) && finite(b) && Math.abs(a - b) <= 1e-7;
  const sides = ['current', 'reference'];
  const $ = id => document.getElementById(id);

  function createDataAdapter(data) {
    if (!data?.id || !Array.isArray(data.sessions) || !data.sessions.length) throw new Error('The packaged comparison data is missing. Extract all files before opening index.html.');
    const records = new Map(data.sessions.map(detail => [detail.session?.id, detail]));
    if (records.has(undefined) || records.size !== data.sessions.length) throw new Error('The packaged recordings have invalid identifiers.');
    const revision = `shared:${data.id}`;
    const fixedSelection = id => {
      const selection = data.selections?.[id];
      if (selection) return clone(selection);
      const bounds = data.traces?.[id]?.distance?.current?.selection || data.traces?.[id]?.elapsed?.current?.selection;
      if (!bounds) throw new Error('This recording has no packaged lap selection.');
      return {start_time: bounds.requested_start_time ?? bounds.start_time, end_time: bounds.requested_end_time ?? bounds.end_time};
    };
    function selectionBounds(id, selection = fixedSelection(id)) {
      const samples = records.get(id)?.samples;
      if (!samples) throw new Error('This recording is not included in the package.');
      if ('start_index' in selection || 'end_index' in selection) {
        const start = selection.start_index ?? 0, end = selection.end_index ?? samples.length - 1;
        if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end >= samples.length || end <= start) throw new Error('Invalid packaged lap indices.');
        return {start: samples[start].time, end: samples[end].time};
      }
      const start = selection.start_time, end = selection.end_time;
      if (!finite(start) || !finite(end) || end <= start) throw new Error('Invalid packaged lap boundaries.');
      return {start, end};
    }
    function assertSelection(id, selection) {
      if (!records.has(id)) throw new Error('This recording is not included in the package.');
      if (!selection || typeof selection !== 'object' || Array.isArray(selection) ||
          Object.keys(selection).some(key => !['start_time', 'end_time', 'start_index', 'end_index', 'segment_id'].includes(key))) throw new Error('This shared copy uses its saved lap boundaries.');
      if (('start_index' in selection || 'end_index' in selection) && ('start_time' in selection || 'end_time' in selection)) throw new Error('Choose sample indices or source times, not both.');
      const expected = fixedSelection(id), actual = selectionBounds(id, selection), bounds = selectionBounds(id, expected);
      if (!same(actual.start, bounds.start) || !same(actual.end, bounds.end) || (selection.segment_id ?? null) !== (expected.segment_id ?? null)) throw new Error('This shared copy uses its saved lap boundaries.');
    }
    function fixedRanges(detail) {
      const id = detail.session.id, selection = fixedSelection(id), {start, end} = selectionBounds(id);
      return [{id: 'shared-lap', label: data.selectionLabels?.[id] || `Shared lap / ${Math.floor((end - start) / 60)}:${((end - start) % 60).toFixed(3).padStart(6, '0')}`,
        selection, start, end, duration: end - start, lap: true, valid: null,
        reviewed: false, boundariesVerified: false, landmarks: [], segment: selection.segment_id}];
    }
    function detail(id) {
      if (!records.has(id)) throw new Error('This recording is not included in the package.');
      const result = clone(records.get(id));
      result.session.metadata ||= {};
      result.session.metadata.comparison_defaults = {range_id: 'shared-lap', revision};
      return result;
    }
    async function api(path, options = {}) {
      const url = new URL(path, 'https://offline.invalid/'), method = options.method || 'GET';
      if (url.origin !== 'https://offline.invalid') throw new Error('Only packaged data is available offline.');
      const route = url.pathname;
      if (method === 'GET') {
        if (route === '/api/sessions') return [...records.keys()].map(id => detail(id).session);
        if (route === '/api/catalog') return clone(data.catalog || {});
        if (route === '/api/imports') return [];
        if (route === '/api/tracks') return clone(data.tracks || []);
        const trackMatch = route.match(/^\/api\/tracks\/([^/]+)$/);
        if (trackMatch) {
          const track = data.tracks?.find(item => item.id === decodeURIComponent(trackMatch[1]));
          if (!track) throw new Error('This circuit is not included in the package.');
          return clone(track);
        }
        const sessionMatch = route.match(/^\/api\/sessions\/([^/]+)(?:\/(video|lap-candidates))?$/);
        if (sessionMatch) {
          const id = decodeURIComponent(sessionMatch[1]);
          if (!records.has(id)) throw new Error('This recording is not included in the package.');
          if (sessionMatch[2] === 'video') {
            const media = data.media?.[id];
            if (!media?.url || /^(?:[a-z]+:|\/)/i.test(media.url) || media.url.split('/').includes('..')) throw new Error('The local video path is missing or invalid.');
            return clone(media);
          }
          if (sessionMatch[2] === 'lap-candidates') return {candidates: []};
          return detail(id);
        }
      }
      if (method === 'POST' && (route === '/api/compare' || route === '/api/trace')) {
        const body = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
        if (!body || !['distance', 'elapsed'].includes(body.axis)) throw new Error('Choose Distance or Time.');
        if (route === '/api/trace') {
          assertSelection(body.session_id, body.range);
          const result = data.traces?.[body.session_id]?.[body.axis];
          if (!result) throw new Error('This recording view was not included in the package.');
          return clone(result);
        }
        assertSelection(body.current_session_id, body.current_range);
        assertSelection(body.reference_session_id, body.reference_range);
        if (body.current_session_id === body.reference_session_id) throw new Error('Choose the other recording for comparison, or clear one selector for a single recording.');
        const mode = body.alignment_mode || 'raw';
        const key = `${body.current_session_id}|${body.reference_session_id}|${body.axis}|${mode}`;
        const result = data.comparisons?.[key];
        if (!result) throw new Error('This registration mode was not included in the shared comparison.');
        if (Boolean(body.align_start) !== Boolean(result.align_start) || Boolean(body.align_end) !== Boolean(result.align_end)) throw new Error('The alignment confirmations must match the saved comparison.');
        if (mode === 'landmarks') {
          const anchors = (result.alignment?.anchors || []).slice(1, -1), requested = body.landmarks || [];
          if (anchors.length !== requested.length || anchors.some((anchor, index) => !same(anchor.current_time, requested[index].current_time) || !same(anchor.reference_time, requested[index].reference_time))) throw new Error('The landmarks must match the saved comparison.');
        } else if (body.landmarks?.length) throw new Error('This registration mode does not use landmarks.');
        return clone(result);
      }
      throw new Error('This action is unavailable in the shared comparison.');
    }
    return {api, fixedRanges, fixedSelection, selectionBounds, revision, records};
  }

  const videoRequests = new WeakMap();
  function seekVideo(video, url, time) {
    const previous = videoRequests.get(video);
    previous?.finish(false);
    video.pause();
    return new Promise((resolve, reject) => {
      const request = {seeking: false, done: false, timer: null};
      const events = ['loadedmetadata', 'loadeddata', 'seeked', 'canplay'];
      request.finish = (ready, error) => {
        if (request.done) return;
        request.done = true;
        clearTimeout(request.timer);
        events.forEach(name => video.removeEventListener(name, check));
        video.removeEventListener('error', fail);
        if (error) reject(error); else resolve(ready);
      };
      const fail = () => request.finish(false, new Error('The included video could not be opened. Extract the full package and keep the media folder beside index.html.'));
      const check = () => {
        if (request.done || videoRequests.get(video) !== request || video.readyState < 1) return;
        const target = Math.max(0, Math.min(time, Number.isFinite(video.duration) ? Math.max(0, video.duration - .001) : time));
        if (Math.abs(video.currentTime - target) > .025) {
          if (!request.seeking || !video.seeking) { request.seeking = true; video.currentTime = target; }
        } else if (video.readyState >= 2 && !video.seeking) request.finish(true);
      };
      videoRequests.set(video, request);
      events.forEach(name => video.addEventListener(name, check));
      video.addEventListener('error', fail);
      request.timer = setTimeout(() => request.finish(false, new Error('Video loading timed out. Check that the media folder was extracted.')), 20000);
      if (video.getAttribute('src') !== url) {video.src = url; video.load();}
      check();
    });
  }

  function boot() {
    try {
      const data = window.SHARED_COMPARISON, adapter = createDataAdapter(data);
      const memory = new Map(), prefix = `accc.shared.${data.id}.`;
      const storage = window.ACCCSharedStorage = {
        getItem(key) {try {return localStorage.getItem(prefix + key) ?? localStorage.getItem(`apex.shared.${data.id}.` + key.replace(/^accc\./, 'apex.')) ?? memory.get(key) ?? null;} catch {return memory.get(key) ?? null;}},
        setItem(key, value) {memory.set(key, String(value)); try {localStorage.setItem(prefix + key, String(value));} catch {}}
      };
      const preferences = clone(data.preferences || {});
      for (const side of sides) {
        const id = preferences[side]?.id || data.sessions[sides.indexOf(side)]?.session.id;
        if (!id) {preferences[side] = {id: ''}; continue;}
        const bounds = adapter.selectionBounds(id), selection = adapter.fixedSelection(id);
        preferences[side] = {id, range: 'shared-lap', start: bounds.start, end: bounds.end, segment: selection.segment_id ?? '', defaults_revision: adapter.revision};
      }
      // Always open the shared pair as supplied, independent of other exports.
      storage.setItem('accc.comparison.v1', JSON.stringify(preferences));
      const ids = [...adapter.records.keys()];
      for (const current of ['', ...ids]) for (const reference of ['', ...ids]) {
        if ((!current && !reference) || (current && current === reference)) continue;
        const selected = id => id ? (data.traces[id].distance || data.traces[id].elapsed).current.selection : null;
        const track = {...{track: data.tracks?.[0]?.id || '', origin: null, reverse: false, aligned: false, progressMode: 'one_lap'}, ...(data.trackPreferences || {}), selectionKey: JSON.stringify([selected(current), selected(reference)])};
        storage.setItem(`accc.track-review.v2.${current}:${reference}`, JSON.stringify(track));
      }
      $('sharedTitle').textContent = data.title || 'Shared comparison';
      const lapTime = seconds => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(3).padStart(6, '0')}`;
      const durations = sides.map(side => {
        const range = adapter.selectionBounds(preferences[side].id);
        return range.end - range.start;
      });
      const difference = durations[0] - durations[1];
      $('sharedLapSummary').textContent = `Shared laps · Your run ${lapTime(durations[0])} · Reference ${lapTime(durations[1])} · ${difference >= 0 ? '+' : ''}${difference.toFixed(3)} s`;
      document.title = `${data.title || 'Shared comparison'} · ACCC Telemetry`;
      const icons = () => window.lucide?.createIcons();
      const notify = message => {$('noticeText').textContent = message; $('notice').classList.remove('hidden');};
      $('noticeDismiss').onclick = () => $('notice').classList.add('hidden');
      function lockControls() {
        for (const side of sides) {
          const range = $(`${side}Range`); if (range) range.disabled = true;
          for (const edge of ['Start', 'End']) {const input = $(`${side}${edge}`); if (input) input.readOnly = true;}
          const segment = $(`${side}Segment`); if (segment) segment.disabled = true;
          const selector = $(`${side}Session`), other = $(`${side === 'current' ? 'reference' : 'current'}Session`)?.value;
          if (selector) for (const option of selector.options) option.disabled = Boolean(option.value && option.value === other);
        }
        for (const id of ['confirmAlignment', 'confirmSameTrack', 'confirmFinishAlignment']) if ($(id)) $(id).disabled = true;
        for (const input of document.querySelectorAll('#landmarkRows input')) input.readOnly = true;
        const modes = $('comparisonAlignmentMode'), current = $('currentSession')?.value, reference = $('referenceSession')?.value;
        if (modes) {
          for (const option of modes.options) {
            const available = data.comparisons?.[`${current}|${reference}|distance|${option.value}`];
            option.hidden = !available;
            option.disabled = !available;
          }
          if ([...modes.options].filter(option => !option.disabled).length <= 1) modes.disabled = true;
        }
      }
      for (const credit of data.credits || []) {
        const paragraph = document.createElement('p');
        if (typeof credit === 'string') paragraph.textContent = credit;
        else {
          paragraph.textContent = credit.text || credit.label || credit.title || credit.name || '';
          const url = credit.url || credit.href;
          if (url && /^(?:https?:\/\/|(?:licenses|credits)\/)/.test(url)) {
            const link = document.createElement('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = credit.linkLabel || 'Source'; paragraph.append(' · ', link);
          }
        }
        $('sharedCredits').append(paragraph);
      }
      window.ACCCComparison.init({api: adapter.api, icons, notify, currentSessionId: () => preferences.current.id,
        readOnly: true, fixedRanges: adapter.fixedRanges, seekVideo, lockControls});
      $('alignmentDialog').querySelector('h2').textContent = 'Source recording';
      $('alignmentDialog').addEventListener('close', () => $('alignmentImage').pause());
      window.ACCCComparison.refresh(true);
      window.addEventListener('resize', () => window.ACCCComparison.resize());
      icons(); lockControls();
      return adapter;
    } catch (error) {
      $('sharedFailure').hidden = false;
      $('sharedFailure').textContent = error.message;
      return null;
    }
  }
  return {createDataAdapter, seekVideo, boot};
})();
if (typeof document !== 'undefined') window.ACCCSharedReview.adapter = window.ACCCSharedReview.boot();
