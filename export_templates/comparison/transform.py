"""Adapt production review modules for a fixed, fully offline comparison export.

Only exported copies change. The renderer and playback calculations stay in the
production files; the adapter supplies immutable, server-calculated results.
"""

from __future__ import annotations


def _replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError(f"Expected one export transform anchor: {before[:90]!r}")
    return source.replace(before, after, 1)


def _replace_function(source: str, beginning: str, following: str, body: str) -> str:
    start = source.index(beginning)
    end = source.index(following, start)
    return source[:start] + body.rstrip() + "\n\n" + source[end:]


def transform_comparison(source: str) -> str:
    """Keep production UI behavior, replacing mutable ranges and frame HTTP."""
    source = source.replace("localStorage.", "window.ACCCSharedStorage.")
    source = _replace_once(source,
        '<img id="${side}Frame" alt="${names[side]} source frame" hidden>',
        '<video id="${side}Frame" aria-label="${names[side]} source frame" muted playsinline preload="metadata" hidden></video>')
    source = _replace_once(source,
        '<img id="alignmentImage" alt="Full source frame for range alignment">',
        '<video id="alignmentImage" aria-label="Full source recording frame" muted playsinline preload="metadata"></video>')
    source = _replace_once(source,
        "    $('alignmentImage').src = $(`${side}Frame`).src;",
        "    $('alignmentImage').pause();")
    source = _replace_function(source, "  function makeRanges(data) {", "  async function loadRun(",
        "  function makeRanges(data) { return helpers.fixedRanges(data); }")
    source = _replace_once(source,
        "  function resetAlignment(clearLandmarks = false, edge = null) {",
        "  function resetAlignment(clearLandmarks = false, edge = null) {\n    if (helpers.readOnly) { updateRegistration(); return; }")
    source = _replace_once(source,
        "  function applyReviewedAlignment(force = false) {",
        "  function applyReviewedAlignment(force = false) {\n    if (helpers.readOnly) return;")
    source = _replace_once(source,
        "  function updateSessionActions() {",
        "  function updateSessionActions() {\n    helpers.lockControls?.();")
    source = _replace_once(source,
        "    $('addComparisonLandmark').disabled = state.landmarks.length >= 32;",
        "    $('addComparisonLandmark').disabled = state.landmarks.length >= 32;\n    helpers.lockControls?.();")
    source = _replace_once(source,
        "    $('registrationStatus').classList.toggle('unconfirmed', missingStart || missingEnd);",
        "    $('registrationStatus').classList.toggle('unconfirmed', missingStart || missingEnd);\n    helpers.lockControls?.();")
    source = _replace_function(source, "  async function updateFrame(side, time) {", "  function zoom(", r'''
  async function updateFrame(side, time) {
    const run = state.runs[side];
    if (!run.id || !finite(time)) return;
    const sequence = run.frameSequence = (run.frameSequence || 0) + 1;
    try {
      const media = await helpers.api(`/api/sessions/${encodeURIComponent(run.id)}/video`);
      if (state.runs[side] !== run || run.frameSequence !== sequence) return;
      const thumbnail = $(`${side}Frame`);
      const tasks = [helpers.seekVideo(thumbnail, media.url, Number(time)).then(ready => {
        if (!ready || state.runs[side] !== run || run.frameSequence !== sequence) return;
        thumbnail.hidden = false;
        $(`${side}FrameEmpty`).hidden = true;
        $(`${side}FrameTime`).hidden = false;
        $(`${side}FrameTime`).textContent = stamp(time);
      })];
      if (state.viewer?.side === side && $('alignmentDialog').open &&
          Math.abs(Number($('alignmentTime').value) - Number(time)) < .005) {
        tasks.push(helpers.seekVideo($('alignmentImage'), media.url, Number(time)).then(ready => {
          if (!ready || state.runs[side] !== run || state.viewer?.side !== side ||
              Math.abs(Number($('alignmentTime').value) - Number(time)) > .005) return;
          const displayed = $('alignmentImage').currentTime;
          $('alignmentFrameStamp').textContent = `Frame ${Math.floor(displayed / 60)}:${(displayed % 60).toFixed(3).padStart(6, '0')}`;
          setFrameReady(true);
        }));
      }
      await Promise.all(tasks);
    } catch (error) {
      if (state.runs[side] !== run || run.frameSequence !== sequence) return;
      $(`${side}FrameEmpty`).textContent = 'Video unavailable';
      $(`${side}FrameEmpty`).hidden = false;
      if (state.viewer?.side === side) $('alignmentFrameStamp').textContent = error.message;
    }
  }
''')
    return source


def transform_track_review(source: str) -> str:
    """Retain production native-video seeks against original source timestamps."""
    return source.replace("localStorage.", "window.ACCCSharedStorage.")
