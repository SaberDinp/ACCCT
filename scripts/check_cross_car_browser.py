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

from check_delete_swap_browser import CURRENT, REFERENCE, PREFERENCES, Fixtures
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler
from telemetry.catalog import catalog
from telemetry.comparison import compare_session_ranges
from telemetry.session_context import comparison_context, enrich_comparison, session_context, validate_context


class CrossCarFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        self.results = []
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
                self.results.append(result)
                await route.fulfill(json=result)
            except ValueError as error:
                await route.fulfill(status=400, json={"detail": str(error)})
        else:
            await super().route(route)


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
            await page.get_by_role("button", name="Analysis", exact=True).click()
            await expect(page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
            await expect(page.locator("#comparisonWarnings")).to_contain_text("Different cars")
            await expect(page.locator("#comparisonWarnings")).to_contain_text("intrinsic car ranking")
            await expect(page.locator("#currentContext, #referenceContext, #recordingContextDialog, #observedSegments, #comparisonAlignmentMode")).to_have_count(0)
            result = fixture.results[-1]
            assert result["context"]["different_cars"] and result["context"]["same_track"]
            assert result["delta_available"] and result["delta_mode"] == "approximate"
            assert result["alignment"]["mode"] == "raw" and not result["alignment"]["applied"]
            delta = next(point["delta_time_s"] for point in reversed(result["points"]) if point["delta_time_s"] is not None)
            assert delta > 0
            await expect(page.locator("#comparisonDeltaSummary")).to_contain_text(f"+{delta:.3f}")
            checks["different_cars_retain_approximate_delta_and_warnings"] = True
            await expect(page.locator("#trackSelect")).to_have_value("silverstone")
            await page.locator("#trackSelect").select_option("monza")
            await expect(page.locator("#trackReviewStatus")).to_contain_text("differs from the recording circuit label")
            await expect(page.locator("#trackCarMarker")).to_be_hidden()
            await expect(page.locator("#trackReferenceCarMarker")).to_be_hidden()
            await page.locator("#trackSelect").select_option("silverstone")
            await expect(page.locator("#trackReviewStatus")).not_to_contain_text("differs")
            checks["known_recording_circuit_blocks_mismatched_map_position"] = True
            await page.screenshot(path=str(args.output / "cross-car-desktop.png"), full_page=True)

            await page.locator("#swapComparisonRuns").click()
            await expect(page.locator("#currentSession")).to_have_value(REFERENCE)
            await expect(page.locator("#referenceSession")).to_have_value(CURRENT)
            await expect(page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
            swapped = fixture.results[-1]
            assert swapped["context"]["current"]["car_id"] == "ferrari_296_gt3"
            reversed_delta = next(point["delta_time_s"] for point in reversed(swapped["points"]) if point["delta_time_s"] is not None)
            assert abs(reversed_delta + delta) < 1e-6
            await expect(page.locator("#comparisonDeltaSummary")).to_contain_text(f"{reversed_delta:.3f}")
            checks["swap_preserves_car_context_and_reverses_delta"] = True

            await page.reload()
            await page.get_by_role("button", name="Analysis", exact=True).click()
            await expect(page.locator("#currentSession")).to_have_value(REFERENCE, timeout=20000)
            await expect(page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
            for width in (390, 360):
                await page.set_viewport_size({"width": width, "height": 844})
                assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                await page.screenshot(path=str(args.output / f"cross-car-mobile-{width}.png"), full_page=True)
            checks["car_context_and_swap_restore_and_mobile_fits"] = True

            await page.locator("#referenceSession").select_option("")
            await expect(page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
            await expect(page.locator("#comparisonLegendReference")).to_be_hidden()
            await expect(page.locator("#comparisonDeltaSummary")).to_be_hidden()
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
