"""Isolated UI regression for recording deletion and comparison-role swaps.

Only static web assets are served. Every API and WebSocket uses local in-memory
fixtures, including DELETE, so the user's library and media are never touched.
"""
import argparse
import asyncio
import copy
import functools
import json
import math
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from telemetry.comparison import compare_session_ranges, trace_session_range
from telemetry.tracks import get_track, list_tracks
from browser_fixtures import SOCKET_FIXTURE, StaticHandler
from check_single_recording_browser import assert_pair, assert_single, settle_response

CURRENT, REFERENCE, PROTECTED = "delete-fixture-current", "delete-fixture-reference", "delete-fixture-protected"
PREFERENCES = {
    "axis": "distance", "alignment_mode": "landmarks", "align_start": True, "align_end": True,
    "landmarks": [{"label": "Bridge", "current_time": 60, "reference_time": 61}],
    "current": {"id": CURRENT, "range": "custom", "start": 10, "end": 110, "segment": "1"},
    "reference": {"id": REFERENCE, "range": "custom", "start": 12, "end": 110, "segment": "1"},
}


class Gate:
    def __init__(self):
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def wait(self):
        self.arrived.set()
        await asyncio.wait_for(self.release.wait(), 45)


class Fixtures:
    def __init__(self):
        self.sessions, self.images = {}, {}
        self.requests, self.unexpected, self.deletes = [], [], []
        self.next_compare = self.next_delete = self.next_frame = self.next_session = None
        self.delete_error = None
        self.gates = []
        self.status = {"state": "idle", "session_id": None, "source": None, "latest": None,
                       "summary": {}, "events": [], "laps": [], "source_time": 0, "stale": True}
        for index, (sid, name, role) in enumerate(((CURRENT, "Driver A", "current"),
                (REFERENCE, "Driver B", "reference"), (PROTECTED, "Protected external video", "archive"))):
            samples = [{"time": i / 10, "active": True, "status": "driving", "hud_visible": True,
                        "segment_id": 1, "confidence": 1, "speed": 185 - 5 * index + 25 * math.sin(i / 90 + index),
                        "brake": max(0, 50 * math.sin(i / 25 + index)),
                        "throttle": 65 + 25 * math.cos(i / 50 + index), "gear": str(3 + ((i // 150 + index) % 4)),
                        "steering": 25 * math.sin(i / 35 + index), "steering_confidence": 1,
                        "steering_status": "tracked", "steering_unit": "hud_percent"} for i in range(1601)]
            session = {"id": sid, "name": name, "status": "completed", "created_at": "2026-09-25T12:00:00Z",
                       "source": {"kind": "file", "name": name}, "metadata": {"role": role},
                       "sample_count": len(samples), "summary": {}}
            self.sessions[sid] = {"session": session, "samples": samples, "summary": {}, "events": [], "laps": []}
            image = np.full((180, 320, 3), ((35, 95, 35), (85, 45, 105), (45, 65, 90))[index], np.uint8)
            cv2.putText(image, name[:20], (15, 95), cv2.FONT_HERSHEY_SIMPLEX, .7, (235, 245, 235), 1)
            self.images[sid] = cv2.imencode(".jpg", image)[1].tobytes()

    def gate(self, kind, sid=None):
        gate = Gate()
        self.gates.append(gate)
        setattr(self, "next_" + kind, (sid, gate) if kind in {"frame", "session"} else gate)
        return gate

    async def route(self, route):
        request = route.request
        parsed = urlparse(request.url)
        path, method = parsed.path, request.method
        self.requests.append((method, path))
        body = request.post_data_json if request.post_data else {}
        gate, status, result, image = None, 200, None, None
        if path == "/api/status":
            result = self.status
        elif path == "/api/history":
            result = []
        elif path == "/api/sessions":
            result = [item["session"] for item in self.sessions.values()]
        elif path.startswith("/api/sessions/"):
            sid = path.split("/")[3]
            data = self.sessions.get(sid)
            if not data:
                status, result = 404, {"detail": "Unknown fixture recording"}
            elif path.endswith("/deletion"):
                protected, shared = sid == PROTECTED, sid == REFERENCE
                result = {"session_id": sid, "name": data["session"]["name"], "can_delete": True,
                          "blocked_reason": None, "video": {"can_delete": not (protected or shared),
                          "reason": "Outside managed storage; video is retained." if protected else
                                    "Shared by another recording; video is retained." if shared else "Managed video can be deleted.",
                          "name": "fixture.mp4", "path": "/fixture/fixture.mp4", "exists": True,
                          "managed": not protected, "shared_session_ids": [CURRENT] if shared else []}}
            elif method == "DELETE":
                self.deletes.append((sid, parse_qs(parsed.query).get("delete_video", ["false"])[0]))
                gate, self.next_delete = self.next_delete, None
                if self.delete_error:
                    status, result = 409, {"detail": self.delete_error}
                    self.delete_error = None
                else:
                    remove_video = parse_qs(parsed.query).get("delete_video", ["false"])[0] == "true"
                    result = {"session_id": sid, "deleted": True, "video_deleted": remove_video,
                              "video_retained": not remove_video, "warnings": []}
                    if gate:
                        await gate.wait()
                    del self.sessions[sid]
            elif path.endswith("/frame.jpg"):
                image = self.images[sid]
                if self.next_frame and self.next_frame[0] == sid:
                    _, gate = self.next_frame
                    self.next_frame = None
            elif path.endswith("/lap-candidates"):
                result = {"candidates": []}
            else:
                result = copy.deepcopy(data)
                if self.next_session and self.next_session[0] == sid:
                    _, gate = self.next_session
                    self.next_session = None
        elif path in {"/api/compare", "/api/trace"} and method == "POST":
            try:
                if path == "/api/compare":
                    result = compare_session_ranges(self.sessions[body["reference_session_id"]]["samples"],
                        self.sessions[body["current_session_id"]]["samples"], reference_range=body["reference_range"],
                        current_range=body["current_range"], axis=body["axis"], align_start=body["align_start"],
                        align_end=body["align_end"], alignment_mode=body["alignment_mode"], landmarks=body["landmarks"])
                    gate, self.next_compare = self.next_compare, None
                else:
                    result = trace_session_range(self.sessions[body["session_id"]]["samples"], body["range"], body["axis"])
            except (ValueError, KeyError) as error:
                status, result = 400, {"detail": str(error)}
        elif path == "/api/tracks":
            result = list_tracks()
        elif path.startswith("/api/tracks/"):
            result = get_track(path.rsplit("/", 1)[1])
        elif path.endswith(".jpg"):
            await route.fulfill(status=404)
            return
        elif method == "GET":
            result = []
        else:
            self.unexpected.append((method, path, body))
            status, result = 400, {"detail": "Unexpected fixture API mutation"}
        if gate and not gate.release.is_set():
            await gate.wait()
        if image:
            await route.fulfill(body=image, content_type="image/jpeg", headers={"X-Source-Time": parse_qs(parsed.query).get("time", ["0"])[0]})
        else:
            await route.fulfill(status=status, json=result)
        if gate:
            gate.finished.set()


async def loaded(page):
    await page.get_by_role("button", name="Analysis", exact=True).click()
    await expect(page.locator("#currentSession")).to_be_enabled(timeout=20000)
    await assert_pair(page)


async def check_swap(page, checks):
    await expect(page.locator("#comparisonDeltaSummary")).to_contain_text("+2.000")
    await page.locator("#swapComparisonRuns").click()
    await expect(page.locator("#currentSession")).to_have_value(REFERENCE)
    await expect(page.locator("#referenceSession")).to_have_value(CURRENT)
    for side, start in (("current", "12"), ("reference", "10")):
        await expect(page.locator(f"#{side}Range")).to_have_value("custom")
        await expect(page.locator(f"#{side}Start")).to_have_value(start)
        await expect(page.locator(f"#{side}End")).to_have_value("110")
        await expect(page.locator(f"#{side}Segment")).to_have_value("1")
    await expect(page.get_by_label("Landmark 1 your run source time", exact=True)).to_have_value("61")
    await expect(page.get_by_label("Landmark 1 reference source time", exact=True)).to_have_value("60")
    await expect(page.locator("#comparisonAlignmentMode")).to_have_value("landmarks")
    await expect(page.locator("#confirmAlignment")).to_be_checked()
    await expect(page.locator("#confirmFinishAlignment")).to_be_checked()
    await expect(page.locator("#comparisonDeltaSummary")).to_contain_text("-2.000", timeout=20000)
    await assert_pair(page)
    checks["swap_preserves_ranges_segments_landmarks_and_flips_delta"] = True


async def open_delete(page, button):
    await page.locator(button).click()
    await expect(page.locator("#deleteSessionDialog")).to_be_visible()
    await expect(page.locator("#deleteSessionConfirm")).to_be_enabled()


async def new_fixture_context(browser, url, errors, missing=None):
    fixture = Fixtures()
    if missing:
        del fixture.sessions[missing]
    context = await browser.new_context(viewport={"width": 1440, "height": 1050})
    await context.route("**/api/**", fixture.route)
    await context.add_init_script(SOCKET_FIXTURE)
    await context.add_init_script("""if (!sessionStorage.getItem('delete-race-test')) {
      localStorage.clear(); localStorage.setItem('accc.comparison.v1', %s);
      sessionStorage.setItem('delete-race-test','1');
    }""" % json.dumps(json.dumps(PREFERENCES)))
    page = await context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.goto(url)
    return context, page, fixture


async def check_races(browser, url, errors, checks):
    context, page, fixture = await new_fixture_context(browser, url, errors)
    await page.get_by_role("button", name="Recordings", exact=True).click()
    await open_delete(page, f'.session-delete[data-id="{PROTECTED}"]')
    await page.locator("#deleteSessionConfirm").click()
    await expect(page.locator("#deleteSessionDialog")).to_be_hidden()
    await page.wait_for_function("!document.querySelector('.session-delete[data-id=\"delete-fixture-protected\"]')")
    saved = await page.evaluate("JSON.parse(localStorage.getItem('accc.comparison.v1'))")
    for side in ("current", "reference"):
        assert saved[side]["id"] == PREFERENCES[side]["id"], saved
        assert float(saved[side]["start"]) == PREFERENCES[side]["start"], saved
        assert float(saved[side]["end"]) == PREFERENCES[side]["end"], saved
    checks["unselected_deletion_before_analysis_preserves_saved_ranges"] = True
    await open_delete(page, f'.session-delete[data-id="{REFERENCE}"]')
    await page.locator("#deleteSessionConfirm").click()
    await expect(page.locator("#deleteSessionDialog")).to_be_hidden()
    await page.get_by_role("button", name="Analysis", exact=True).click()
    await expect(page.locator("#currentSession")).to_have_value(CURRENT)
    await expect(page.locator("#referenceSession")).to_have_value("")
    await expect(page.locator("#currentRange")).to_have_value("custom")
    await expect(page.locator("#currentStart")).to_have_value("10")
    await expect(page.locator("#currentEnd")).to_have_value("110")
    await assert_single(page)
    checks["delete_before_analysis_preserves_unrelated_saved_range"] = True
    await context.close()

    context, page, fixture = await new_fixture_context(browser, url, errors, missing=REFERENCE)
    await page.get_by_role("button", name="Analysis", exact=True).click()
    await expect(page.locator("#currentSession")).to_have_value(CURRENT)
    await expect(page.locator("#referenceSession")).to_have_value("")
    await expect(page.locator("#currentStart")).to_have_value("10")
    await expect(page.locator("#currentEnd")).to_have_value("110")
    await assert_single(page)
    checks["missing_saved_recording_keeps_other_custom_range"] = True
    await context.close()

    context, page, fixture = await new_fixture_context(browser, url, errors)
    await loaded(page)
    gate = fixture.gate("frame", CURRENT)
    await page.locator('.comparison-run').filter(has=page.locator('#currentSession')).locator('.run-review-tools summary').click()
    await page.locator('.inspect-boundary[data-side="current"][data-edge="Start"]').click()
    await page.locator("#alignmentTime").fill("37")
    await page.locator("#alignmentTime").dispatch_event("change")
    await asyncio.wait_for(gate.arrived.wait(), 10)
    await page.locator("#closeAlignment").click()
    await page.locator("#swapComparisonRuns").click()
    await expect(page.locator("#currentSession")).to_have_value(REFERENCE)
    await expect(page.locator("#trackcurrentImage")).to_be_visible()
    await expect(page.locator("#comparisonDeltaSummary")).to_contain_text("-2.000")
    current_src = await page.locator("#currentFrame").get_attribute("src")
    gate.release.set()
    await asyncio.wait_for(gate.finished.wait(), 10)
    await settle_response(page)
    assert await page.locator("#currentFrame").get_attribute("src") == current_src
    checks["late_source_frame_cannot_replace_swapped_video"] = True

    # Deleting a selected recording must discard any delayed comparison frame.
    gate = fixture.gate("frame", CURRENT)
    await page.locator('#referenceStart').fill('47')
    await page.locator('#referenceStart').dispatch_event('change')
    await asyncio.wait_for(gate.arrived.wait(), 10)
    await page.get_by_role("button", name="Recordings", exact=True).click()
    await open_delete(page, f'.session-delete[data-id="{CURRENT}"]')
    await page.locator("#deleteSessionConfirm").click()
    await expect(page.locator("#deleteSessionDialog")).to_be_hidden()
    gate.release.set()
    await asyncio.wait_for(gate.finished.wait(), 10)
    await settle_response(page)
    await page.get_by_role("button", name="Analysis", exact=True).click()
    await expect(page.locator('#referenceSession')).to_have_value('')
    await expect(page.locator('#referenceFrame')).to_be_hidden()
    assert not await page.locator('#referenceFrame').get_attribute('src')
    checks["deleted_analysis_recording_ignores_late_frame"] = True

    await page.get_by_role("button", name="Recordings", exact=True).click()
    gate = fixture.gate("session", REFERENCE)
    await page.locator(f'.session-open[data-id="{REFERENCE}"]').click()
    await asyncio.wait_for(gate.arrived.wait(), 10)
    await page.get_by_role("button", name="Recordings", exact=True).click()
    await open_delete(page, f'.session-delete[data-id="{REFERENCE}"]')
    await page.locator("#deleteSessionConfirm").click()
    await expect(page.locator("#deleteSessionDialog")).to_be_hidden()
    gate.release.set()
    await asyncio.wait_for(gate.finished.wait(), 10)
    await settle_response(page)
    await page.get_by_role("button", name="Analysis", exact=True).click()
    await expect(page.locator('#currentSession')).to_have_value('')
    await expect(page.locator('#currentFrame')).to_be_hidden()
    assert not await page.locator('#currentFrame').get_attribute('src')
    checks["deleted_recording_cannot_load_from_late_review_fetch"] = True
    assert not fixture.unexpected, fixture.unexpected
    await context.close()


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(StaticHandler, directory=str(ROOT / "web")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    errors, checks = [], {}
    fixture = Fixtures()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(viewport={"width": 1440, "height": 1050})
            await context.route("**/api/**", fixture.route)
            await context.add_init_script(SOCKET_FIXTURE)
            await context.add_init_script("""if (!sessionStorage.getItem('delete-swap-test')) {
              localStorage.clear(); localStorage.setItem('accc.comparison.v1', %s);
              sessionStorage.setItem('delete-swap-test','1');
            }""" % json.dumps(json.dumps(PREFERENCES)))
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(url)
            await loaded(page)
            print("Initial fixture comparison rendered", flush=True)
            await check_swap(page, checks)
            print("Swap fields and delta verified", flush=True)
            for selector, label in (("#swapComparisonRuns", "Swap your run and reference"),
                                    ("#currentDeleteSession", "Delete your run recording"),
                                    ("#referenceDeleteSession", "Delete reference recording")):
                await expect(page.locator(selector)).to_have_attribute("aria-label", label)
                assert await page.locator(selector).get_attribute("title")
                await expect(page.locator(selector + " svg")).to_be_visible()
            for width, height in ((1440, 1050), (390, 844)):
                await page.set_viewport_size({"width": width, "height": height})
                await page.locator(".reference-header").evaluate("element => element.scrollIntoView({block:'start'})")
                assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                await page.screenshot(path=str(args.output / f"swap-controls-{width}.png"))
            await page.set_viewport_size({"width": 1440, "height": 1050})
            checks["swap_delete_icons_labeled_and_mobile_fit"] = True
            await page.reload()
            await loaded(page)
            await expect(page.locator("#currentSession")).to_have_value(REFERENCE)
            await expect(page.locator("#comparisonDeltaSummary")).to_contain_text("-2.000")
            await expect(page.get_by_label("Landmark 1 your run source time", exact=True)).to_have_value("61")
            checks["swapped_roles_and_landmarks_survive_reload"] = True
            print("Reload persistence verified", flush=True)

            gate = fixture.gate("compare")
            await page.locator("#runComparison").click()
            await asyncio.wait_for(gate.arrived.wait(), 10)
            await page.locator("#swapComparisonRuns").click()
            await expect(page.locator("#comparisonDeltaSummary")).to_contain_text("+2.000", timeout=20000)
            gate.release.set()
            await asyncio.wait_for(gate.finished.wait(), 10)
            await settle_response(page)
            await expect(page.locator("#comparisonDeltaSummary")).to_contain_text("+2.000")
            checks["stale_compare_after_swap_ignored"] = True
            print("Stale comparison ignored", flush=True)

            await page.locator("#referenceSession").select_option("")
            await assert_single(page)
            await page.locator("#swapComparisonRuns").click()
            await expect(page.locator("#currentSession")).to_have_value("")
            await expect(page.locator("#referenceSession")).to_have_value(CURRENT)
            checks["one_sided_swap_pixels"] = await assert_single(page, "reference")
            await page.locator("#swapComparisonRuns").click()
            await assert_single(page)
            await page.locator("#referenceSession").select_option(REFERENCE)
            await page.locator("#comparisonAlignmentMode").select_option("raw")
            await assert_pair(page)
            print("One-sided swap verified", flush=True)

            await open_delete(page, "#referenceDeleteSession")
            await expect(page.locator("#deleteSessionVideo")).not_to_be_checked()
            await expect(page.locator("#deleteSessionVideo")).to_be_disabled()
            await expect(page.locator("#deleteSessionVideoReason")).to_contain_text("Shared")
            await page.locator("#deleteSessionCancel").click()
            await expect(page.locator("#deleteSessionDialog")).to_be_hidden()
            assert not fixture.deletes
            await assert_pair(page)
            checks["cancel_keeps_recordings_and_shared_media_is_protected"] = True

            fixture.delete_error = "Fixture recording is busy; stop the source first."
            await open_delete(page, "#referenceDeleteSession")
            await page.locator("#deleteSessionConfirm").click()
            await expect(page.locator("#deleteSessionError")).to_contain_text("busy")
            await expect(page.locator("#deleteSessionDialog")).to_be_visible()
            await expect(page.locator("#referenceSession")).to_have_value(REFERENCE)
            await expect(page.locator("#deleteSessionConfirm")).to_be_enabled()
            await page.locator("#deleteSessionCancel").click()
            checks["failed_delete_keeps_dialog_and_recording"] = True

            gate = fixture.gate("delete")
            await open_delete(page, "#referenceDeleteSession")
            await page.locator("#deleteSessionConfirm").click()
            await asyncio.wait_for(gate.arrived.wait(), 10)
            await expect(page.locator("#deleteSessionConfirm")).to_be_disabled()
            await expect(page.locator("#deleteSessionCancel")).to_be_disabled()
            await expect(page.locator("#referenceSession")).to_have_value(REFERENCE)
            await page.keyboard.press("Escape")
            await expect(page.locator("#deleteSessionDialog")).to_be_visible()
            assert len(fixture.deletes) == 2
            gate.release.set()
            await asyncio.wait_for(gate.finished.wait(), 10)
            await expect(page.locator("#deleteSessionDialog")).to_be_hidden()
            await expect(page.locator("#referenceSession")).to_have_value("")
            checks["deleted_reference_has_no_stale_trace"] = await assert_single(page)
            assert await page.locator(f'#referenceSession option[value="{REFERENCE}"]').count() == 0
            await page.reload()
            await page.get_by_role("button", name="Analysis", exact=True).click()
            await expect(page.locator("#referenceSession")).to_have_value("")
            await assert_single(page)
            checks["deleted_reference_remains_empty_after_reload"] = True
            print("Reference deletion, pending, error and reload verified", flush=True)

            await page.get_by_role("button", name="Recordings", exact=True).click()
            await open_delete(page, f'.session-delete[data-id="{PROTECTED}"]')
            await expect(page.locator("#deleteSessionVideo")).to_be_disabled()
            await expect(page.locator("#deleteSessionVideoReason")).to_contain_text("Outside managed")
            await page.set_viewport_size({"width": 390, "height": 844})
            assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            box = await page.locator("#deleteSessionDialog").bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= 391, box
            await page.screenshot(path=str(args.output / "delete-recording-mobile.png"))
            await page.locator("#deleteSessionCancel").click()
            await page.set_viewport_size({"width": 1440, "height": 1050})
            checks["external_media_protected_and_dialog_mobile_fits"] = True

            await page.locator(f'.session-open[data-id="{CURRENT}"]').click()
            await expect(page.locator("#currentSession")).to_have_value(CURRENT)
            await expect(page.locator("#trackReviewPlay")).to_be_enabled()
            await page.locator("#trackReviewPlay").click()
            await expect(page.locator("#trackReviewPlay")).to_have_attribute('aria-label', 'Pause comparison')
            await page.get_by_role("button", name="Recordings", exact=True).click()
            await open_delete(page, f'.session-delete[data-id="{CURRENT}"]')
            await expect(page.locator("#deleteSessionVideo")).to_be_enabled()
            await expect(page.locator("#deleteSessionVideo")).not_to_be_checked()
            await page.locator("#deleteSessionVideo").check()
            await page.screenshot(path=str(args.output / "delete-recording-desktop.png"))
            await page.locator("#deleteSessionConfirm").click()
            await expect(page.locator("#deleteSessionDialog")).to_be_hidden()
            assert fixture.deletes[-1] == (CURRENT, "true")
            assert await page.evaluate("ACCCTelemetry.state.analysisReview") is None
            checks["delete_loaded_analysis_clears_graphs_preview_and_playback"] = True
            await page.get_by_role("button", name="Analysis", exact=True).click()
            await expect(page.locator("#currentSession")).to_have_value("")
            await expect(page.locator("#referenceSession")).to_have_value("")
            await expect(page.locator("#comparisonLegendCurrent")).to_be_hidden()
            await expect(page.locator("#comparisonLegendReference")).to_be_hidden()
            await expect(page.locator("#swapComparisonRuns")).to_be_disabled()
            checks["empty_comparison_disables_swap"] = True
            print("Recording deletion and empty comparison verified", flush=True)
            await context.close()
            await check_races(browser, url, errors, checks)
            assert not fixture.unexpected, fixture.unexpected
            assert not errors, errors
            await browser.close()
    finally:
        for gate in fixture.gates:
            gate.release.set()
        server.shutdown()
        server.server_close()
    result = {"checks": checks, "fixture_deletes": fixture.deletes, "real_library_mutated": False,
              "unexpected_mutations": fixture.unexpected, "page_errors": errors}
    (args.output / "delete-swap-browser-checks.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "delete-swap-review")
    asyncio.run(run(parser.parse_args()))
