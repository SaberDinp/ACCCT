"""Isolated car/circuit labels, cross-car comparison and mobile layout checks."""

import argparse
import asyncio
import functools
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from check_delete_swap_browser import CURRENT, REFERENCE, PREFERENCES, Fixtures, loaded
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler
from telemetry.catalog import catalog
from telemetry.comparison import compare_session_ranges
from telemetry.session_context import comparison_context, enrich_comparison, session_context, validate_context


class CrossCarFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        for sid, car in ((CURRENT, "mclaren_720s_gt3_evo"), (REFERENCE, "ferrari_296_gt3")):
            session = self.sessions[sid]["session"]
            session["metadata"]["telemetry_context"] = validate_context({"car_id": car, "track_id": "silverstone"})
            session["context"] = session_context(session)
            for sample in self.sessions[sid]["samples"]:
                sample["speed"] = 180 if sid == CURRENT else 195

    async def route(self, route):
        request = route.request
        path = urlparse(request.url).path
        body = request.post_data_json if request.post_data else {}
        if path == "/api/catalog":
            await route.fulfill(json=catalog())
        elif path.endswith("/context") and request.method == "PATCH":
            sid = path.split("/")[3]
            session = self.sessions[sid]["session"]
            session["metadata"]["telemetry_context"] = validate_context(body)
            session["context"] = session_context(session)
            await route.fulfill(json={"context": session["context"]})
        elif path == "/api/compare":
            current = self.sessions[body["current_session_id"]]
            reference = self.sessions[body["reference_session_id"]]
            confirmed = body.get("confirm_same_track", False) or body["align_start"]
            try:
                comparison_context(current["session"], reference["session"], confirmed)
                result = compare_session_ranges(reference["samples"], current["samples"],
                    reference_range=body["reference_range"], current_range=body["current_range"],
                    axis=body["axis"], align_start=body["align_start"], align_end=body["align_end"],
                    alignment_mode=body["alignment_mode"], landmarks=body["landmarks"])
                enrich_comparison(result, current["session"], reference["session"], confirmed)
                await route.fulfill(json=result)
            except ValueError as error:
                await route.fulfill(status=400, json={"detail": str(error)})
        else:
            await super().route(route)


async def edit_track(page, side, value):
    await page.locator(f"#{side}EditContext").click()
    await expect(page.locator("#recordingContextDialog")).to_be_visible()
    await expect(page.locator("#saveRecordingContext")).to_be_enabled()
    await page.locator("#recordingContextTrack").select_option(value)
    await page.locator("#saveRecordingContext").click()
    await expect(page.locator("#recordingContextDialog")).to_be_hidden()


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(StaticHandler, directory=str(ROOT / "web")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checks, errors = {}, []
    fixture = CrossCarFixtures()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(viewport={"width": 1440, "height": 1050})
            await context.route("**/api/**", fixture.route)
            await context.add_init_script(SOCKET_FIXTURE)
            await context.add_init_script("""if (!sessionStorage.getItem('cross-car-fixture')) {
              localStorage.clear(); localStorage.setItem('accc.comparison.v1', %s);
              sessionStorage.setItem('cross-car-fixture','1');
            }""" % json.dumps(json.dumps(PREFERENCES)))
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(f"http://127.0.0.1:{server.server_port}")
            await loaded(page)
            await expect(page.locator("#currentContext")).to_contain_text("McLaren")
            await expect(page.locator("#referenceContext")).to_contain_text("Ferrari")
            await expect(page.locator("#comparisonWarnings")).to_contain_text("Different cars")
            await expect(page.locator("#comparisonWarnings")).to_contain_text("intrinsic car ranking")
            await expect(page.locator("#observedSegmentsRows tr")).to_have_count(2)
            await expect(page.locator("#comparisonDeltaSummary")).to_contain_text("+2.000")
            checks["different_cars_same_track_have_observed_segments_and_warnings"] = True
            await page.locator("#trackSelect").select_option("monza")
            await expect(page.locator("#trackReviewStatus")).to_contain_text("differs from the recording circuit label")
            await expect(page.locator("#trackSetOrigin")).to_be_disabled()
            await expect(page.locator("#trackRangesAligned")).to_be_disabled()
            await expect(page.locator("#trackOriginStatus")).to_have_text("Lap origin not set")
            await page.locator("#trackSelect").select_option("silverstone")
            await expect(page.locator("#trackSetOrigin")).to_be_enabled()
            checks["known_recording_circuit_blocks_mismatched_map_alignment"] = True
            await page.screenshot(path=str(args.output / "cross-car-desktop.png"), full_page=True)

            await page.locator("#swapComparisonRuns").click()
            await expect(page.locator("#currentContext")).to_contain_text("Ferrari")
            await expect(page.locator("#referenceContext")).to_contain_text("McLaren")
            await expect(page.locator("#comparisonDeltaSummary")).to_contain_text("-2.000")
            await expect(page.locator("#observedSegmentsRows tr")).to_have_count(2)
            checks["swap_carries_labels_and_reverses_delta"] = True

            await page.locator("#currentEditContext").click()
            await expect(page.locator("#saveRecordingContext")).to_be_enabled()
            await expect(page.locator("#recordingContextCar option")).to_have_count(32)
            await expect(page.locator("#recordingContextTrack option")).to_have_count(26)
            await page.locator("#recordingContextConditions").fill("Dry / 24 C / 30 L")
            await page.locator("#recordingContextPlatform").select_option("ps5")
            await page.set_viewport_size({"width": 390, "height": 844})
            assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            box = await page.locator("#recordingContextDialog").bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= 391, box
            await page.screenshot(path=str(args.output / "cross-car-editor-mobile.png"))
            await page.locator("#saveRecordingContext").click()
            await expect(page.locator("#recordingContextDialog")).to_be_hidden()
            assert fixture.sessions[REFERENCE]["session"]["context"]["conditions"] == "Dry / 24 C / 30 L"
            await page.reload()
            await loaded(page)
            await expect(page.locator("#currentContext")).to_contain_text("Ferrari")
            await page.screenshot(path=str(args.output / "cross-car-mobile.png"), full_page=True)
            assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            checks["full_catalog_metadata_persists_and_mobile_fits"] = True

            await edit_track(page, "current", "monza")
            await expect(page.locator("#comparisonStatus")).to_contain_text("different track labels")
            await expect(page.locator("#observedSegments")).to_be_hidden()
            checks["different_tracks_block_and_clear_observed_comparison"] = True

            await edit_track(page, "current", "")
            await page.locator("#comparisonAlignmentMode").select_option("raw")
            await expect(page.locator("#comparisonWarnings")).to_contain_text("Circuit identity is unknown")
            await expect(page.locator("#observedSegmentsRows tr")).to_have_count(0)
            await expect(page.locator("#sameTrackConfirmation")).to_be_visible()
            await page.locator("#confirmSameTrack").check()
            await expect(page.locator("#comparisonWarnings")).to_contain_text("Same circuit layout is user-confirmed")
            await page.reload()
            await loaded(page)
            await expect(page.locator("#confirmSameTrack")).to_be_checked()
            await expect(page.locator("#comparisonWarnings")).to_contain_text("Same circuit layout is user-confirmed")
            checks["unknown_track_raw_overlay_warns_and_confirmation_persists"] = True

            await page.locator("#referenceSession").select_option("")
            await expect(page.locator("#observedSegments")).to_be_hidden()
            await expect(page.locator("#comparisonLegendReference")).to_be_hidden()
            await expect(page.locator("#sameTrackConfirmation")).to_be_hidden()
            checks["single_recording_has_no_reference_assessments"] = True
            assert not errors, errors
            assert not fixture.unexpected, fixture.unexpected
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    result = {"checks": checks, "page_errors": errors, "real_library_mutated": False}
    (args.output / "cross-car-browser-checks.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "cross-car-review")
    asyncio.run(run(parser.parse_args()))
