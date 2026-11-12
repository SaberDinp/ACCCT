"""Browser-only fixtures verify reviewed lap defaults without changing the library."""
import argparse
import base64
import copy
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry.comparison import compare_session_ranges, trace_session_range


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    fixtures, errors, comparisons, saves = {}, [], [], []
    for side, start, end in (("current", 10, 110), ("reference", 12, 110)):
        session_id = "review-fixture-" + side
        metadata = {"role": side, "reviewed_laps": [{"id": "reviewed-1", "lap_number": 1,
            "start_time": start, "end_time": end, "valid": None, "boundaries_verified": True,
            "start_marker": "fixture-track-sf", "end_marker": "fixture-track-sf"}],
            "comparison_defaults": {"range_id": "reviewed-1", "revision": "review-v1", "alignment_mode": "lap_endpoints"}}
        samples = [{"time": i / 10, "active": True, "hud_visible": True, "segment_id": 1,
                    "speed": 180 + 30 * math.sin(i / 100), "brake": max(0, 40 * math.sin(i / 20)),
                    "throttle": 70, "gear": 4, "steering": 10 * math.sin(i / 30), "steering_unit": "hud_percent"}
                   for i in range(1601)]
        session = {"id": session_id, "name": "Reviewed fixture " + side, "status": "completed",
                   "metadata": metadata, "source": {"kind": "file", "name": "Reviewed fixture " + side},
                   "sample_count": len(samples), "summary": {}}
        fixtures[session_id] = {"session": session, "samples": samples, "laps": [], "events": [], "summary": {}}
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aSxQAAAAASUVORK5CYII=")

    def route_api(route):
        path = urlparse(route.request.url).path
        body = route.request.post_data_json if route.request.post_data else {}
        if path == "/api/sessions":
            route.fulfill(json=[item["session"] for item in fixtures.values()])
        elif path.startswith("/api/sessions/"):
            parts = path.split("/")
            data = fixtures.get(parts[3])
            if not data:
                route.fulfill(status=404, json={"detail": "Unknown fixture session"})
            elif path.endswith("/frame.jpg"):
                route.fulfill(body=png, content_type="image/png")
            elif path.endswith("/lap-candidates"):
                route.fulfill(json={"candidates": []})
            elif path.endswith("/reviewed-laps") and route.request.method == "PATCH":
                saves.append(copy.deepcopy(body))
                metadata = data["session"]["metadata"]
                metadata["reviewed_laps"].append(body["lap"])
                metadata["comparison_defaults"] = {"range_id": body["lap"]["id"], "revision": "saved-v3", "alignment_mode": "raw"}
                route.fulfill(json=metadata)
            else:
                route.fulfill(json=data)
        elif path in {"/api/compare", "/api/trace"}:
            try:
                if path.endswith("compare"):
                    result = compare_session_ranges(fixtures[body["reference_session_id"]]["samples"],
                        fixtures[body["current_session_id"]]["samples"], reference_range=body["reference_range"],
                        current_range=body["current_range"], axis=body["axis"], align_start=body["align_start"],
                        align_end=body["align_end"], alignment_mode=body["alignment_mode"], landmarks=body["landmarks"])
                    comparisons.append(result)
                else:
                    result = trace_session_range(fixtures[body["session_id"]]["samples"], body["range"], body["axis"])
                route.fulfill(json=result)
            except ValueError as error:
                route.fulfill(status=400, json={"detail": str(error)})
        elif path == "/api/imports":
            route.fulfill(json=[])
        elif route.request.method not in {"GET", "HEAD"}:
            route.abort()
            raise AssertionError("Unexpected mutation: " + path)
        else:
            route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.route("**/api/**", route_api)
        stale = {"axis": "elapsed", "alignment_mode": "landmarks", "align_start": True, "align_end": True,
                 "landmarks": [{"current_time": 15, "reference_time": 16}],
                 "current": {"id": "deleted-current", "range": "full"},
                 "reference": {"id": "deleted-reference", "range": "custom", "start": 1, "end": 155}}
        context.add_init_script("""if (!sessionStorage.getItem('review-check')) {
          localStorage.setItem('accc.comparison.v1', %s); sessionStorage.setItem('review-check','1');
        }""" % json.dumps(json.dumps(stale)))
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.get_by_role("button", name="Analysis", exact=True).click()
        expect(page.locator("#currentRange")).to_have_value("reviewed-1", timeout=20000)
        expect(page.locator("#referenceRange")).to_have_value("reviewed-1", timeout=20000)
        expect(page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
        expect(page.locator("#currentStart")).to_have_value("10.000")
        expect(page.locator("#referenceStart")).to_have_value("12.000")
        expect(page.locator("#confirmAlignment")).to_be_checked()
        expect(page.locator("#confirmFinishAlignment")).to_be_checked()
        expect(page.locator("#comparisonAlignmentMode")).to_have_value("lap_endpoints")
        assert comparisons[-1]["delta_available"] and abs(comparisons[-1]["points"][-1]["delta_time_s"] - 2) < 1e-6
        page.locator("#currentEnd").fill("111")
        page.locator("#currentEnd").dispatch_event("change")
        expect(page.locator("#confirmFinishAlignment")).not_to_be_checked()
        page.reload()
        page.get_by_role("button", name="Analysis", exact=True).click()
        expect(page.locator("#currentEnd")).to_have_value("111", timeout=20000)
        expect(page.locator("#currentRange")).to_have_value("custom")
        expect(page.locator("#confirmFinishAlignment")).not_to_be_checked()

        current = fixtures["review-fixture-current"]["session"]["metadata"]
        current["comparison_defaults"]["revision"] = "review-v2"
        current["reviewed_laps"][0].update(start_time=11, end_time=111)
        page.locator("#refreshComparisons").click()
        expect(page.locator("#currentRange")).to_have_value("reviewed-1", timeout=20000)
        expect(page.locator("#currentStart")).to_have_value("11.000")
        expect(page.locator("#confirmFinishAlignment")).to_be_checked()
        expect(page.locator("#comparisonStatus")).to_have_text("", timeout=20000)

        page.locator("#currentEnd").fill("112")
        page.locator("#currentEnd").dispatch_event("change")
        page.locator("#currentSaveLapRange").click()
        try:
            expect(page.locator("#currentSaveLapRange")).to_be_disabled()
        except AssertionError as error:
            raise AssertionError({"notice": page.locator("#noticeText").inner_text(), "saves": saves,
                                  "errors": errors, "range": page.locator("#currentRange").input_value()}) from error
        page.wait_for_function("document.getElementById('currentRange').value.startsWith('saved-')")
        assert len(saves) == 1 and saves[0]["lap"]["boundaries_verified"] is False
        assert "start_marker" not in saves[0]["lap"] and "end_marker" not in saves[0]["lap"]

        fresh = browser.new_context(viewport={"width": 390, "height": 844})
        fresh.route("**/api/**", route_api)
        new_page = fresh.new_page()
        new_page.on("pageerror", lambda error: errors.append(str(error)))
        new_page.goto(args.url)
        new_page.get_by_role("button", name="Analysis", exact=True).click()
        expect(new_page.locator("#currentRange")).to_have_value(saves[0]["lap"]["id"], timeout=20000)
        expect(new_page.locator("#currentEnd")).to_have_value("112.000")
        expect(new_page.locator("#confirmAlignment")).not_to_be_checked()
        expect(new_page.locator("#comparisonAlignmentMode")).to_have_value("raw")
        expect(new_page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
        assert comparisons[-1]["delta_available"] and comparisons[-1]["delta_mode"] == "approximate"
        assert not new_page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        new_page.locator(".lap-registration").scroll_into_view_if_needed()
        new_page.screenshot(path=str(args.output / "reviewed-defaults-mobile.png"))

        reference = fixtures["review-fixture-reference"]["session"]["metadata"]
        current["comparison_defaults"] = {"range_id": "reviewed-1", "revision": "review-v4", "alignment_mode": "lap_endpoints"}
        current["reviewed_laps"][0]["landmarks"] = [{"marker_id": "fixture-bridge", "time": 60, "label": "Bridge"}]
        reference["reviewed_laps"][0]["start_marker"] = "other-track-sf"
        reference["comparison_defaults"]["revision"] = "mismatched-v2"
        new_page.locator("#refreshComparisons").click()
        expect(new_page.locator("#currentRange")).to_have_value("reviewed-1", timeout=20000)
        expect(new_page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
        expect(new_page.locator("#confirmAlignment")).not_to_be_checked()
        assert comparisons[-1]["delta_available"] and comparisons[-1]["delta_mode"] == "approximate"
        reference["reviewed_laps"][0]["start_marker"] = "fixture-track-sf"
        reference["reviewed_laps"][0]["landmarks"] = [{"marker_id": "fixture-bridge", "time": 58, "label": "Bridge"}]
        reference["comparison_defaults"]["revision"] = "matched-v3"
        new_page.locator("#refreshComparisons").click()
        expect(new_page.locator("#comparisonAlignmentMode")).to_have_value("landmarks", timeout=20000)
        expect(new_page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
        expect(new_page.get_by_label("Landmark 1 your run source time", exact=True)).to_have_value("60")
        expect(new_page.get_by_label("Landmark 1 reference source time", exact=True)).to_have_value("58")
        assert comparisons[-1]["delta_available"] and len(comparisons[-1]["alignment"]["anchors"]) == 3
        assert not errors, errors
        browser.close()
    result = {"stale_ids_ignored": True, "verified_defaults_enable_delta": True,
              "unchanged_revision_preserves_manual_edits": True, "new_revision_replaces_old_custom_range": True,
              "saved_manual_range_never_auto_verified": True, "new_browser_uses_saved_default": True,
              "mismatched_markers_never_auto_align": True, "matching_reviewed_landmarks_auto_loaded": True,
              "real_library_mutated": False, "page_errors": errors}
    (args.output / "reviewed-defaults-browser-checks.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
