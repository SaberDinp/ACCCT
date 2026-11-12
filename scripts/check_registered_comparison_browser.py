"""Read-only browser regression for registered laps, delta, legends and landmarks.

Requires the app server and two saved recordings. Only /api/compare reaches the
server as POST. The asynchronous crossing picker uses a browser-local fixture;
the vision detector itself is covered by its separate integration checks.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--current-session", default="0462ac78f63b4d078901d395ca77bac7")
    parser.add_argument("--reference-session", default="d296ac6b613944dc8bb0959c505a6615")
    parser.add_argument("--current-start", type=float, default=147.727)
    parser.add_argument("--current-end", type=float, default=270.082)
    parser.add_argument("--reference-start", type=float, default=2.0)
    parser.add_argument("--reference-end", type=float, default=118.9)
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    errors, writes, cancelled_jobs = [], [], []
    preferences = {"axis": "distance", "alignment_mode": "lap_endpoints",
                   "align_start": False, "align_end": False, "landmarks": []}
    for side in ("current", "reference"):
        preferences[side] = {"id": getattr(args, side + "_session"), "range": "custom",
                             "start": str(getattr(args, side + "_start")),
                             "end": str(getattr(args, side + "_end")), "segment": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: writes.append((request.method, urlparse(request.url).path))
                if request.method not in {"GET", "HEAD", "OPTIONS"} else None)
        before = page.request.get(args.url + "/api/status").json()
        page.add_init_script("""if (!sessionStorage.getItem('registration-test-seeded')) {
          localStorage.setItem('accc.comparison.v1', %s);
          sessionStorage.setItem('registration-test-seeded','1');
        }""" % json.dumps(json.dumps(preferences)))
        page.goto(args.url)
        page.get_by_role("button", name="Analysis", exact=True).click()
        expect(page.locator("#currentSession")).to_be_enabled(timeout=30000)
        expect(page.locator("#referenceStart")).to_have_value(str(args.reference_start), timeout=30000)
        expect(page.locator("#comparisonAlignmentMode")).to_have_value("lap_endpoints")
        expect(page.locator("#comparisonStatus")).to_contain_text("confirm", timeout=20000, ignore_case=True)
        page.locator("#confirmAlignment").check()
        page.locator("#confirmFinishAlignment").check()

        def compare():
            with page.expect_response(lambda response: response.url.endswith("/api/compare")
                                      and response.request.method == "POST") as response:
                page.locator("#runComparison").click()
            result = response.value.json()
            assert response.value.ok, result
            expect(page.locator("#comparisonStatus")).to_have_text("", timeout=20000)
            return result

        endpoints = compare()
        assert endpoints["alignment"]["mode"] == "lap_endpoints"
        assert endpoints["delta_available"]
        expected_delta = (args.current_end - args.current_start) - (args.reference_end - args.reference_start)
        assert abs(endpoints["points"][-1]["delta_time_s"] - expected_delta) < 1e-6
        expect(page.locator("#comparisonLegendCurrent")).not_to_have_text("Your run")
        expect(page.locator("#comparisonLegendReference")).not_to_have_text("Reference")
        legend = page.locator(".comparison-legend").inner_text()
        assert "SOLID" in legend and "DASHED" in legend

        page.locator("#comparisonAlignmentMode").select_option("landmarks")
        page.locator("#addComparisonLandmark").click()
        landmark_current = round(args.current_start + (args.current_end - args.current_start) * .45, 3)
        landmark_reference = round(args.reference_start + (args.reference_end - args.reference_start) * .43, 3)
        page.get_by_label("Landmark 1 name", exact=True).fill("Bridge crossing (test)")
        page.get_by_label("Landmark 1 your run source time", exact=True).fill(str(landmark_current))
        page.get_by_label("Landmark 1 reference source time", exact=True).fill(str(landmark_reference))
        page.get_by_label("Landmark 1 reference source time", exact=True).press("Tab")
        landmarks = compare()
        assert len(landmarks["alignment"]["anchors"]) == 3
        anchor = landmarks["alignment"]["anchors"][1]
        paired = min(landmarks["points"], key=lambda point: abs(point["position"] - anchor["reference_distance_m"]))
        assert abs(paired["current"]["time"] - landmark_current) < 1e-6
        assert abs(paired["reference"]["time"] - landmark_reference) < 1e-6
        assert abs(paired["delta_time_s"] - ((landmark_current - args.current_start) -
                                            (landmark_reference - args.reference_start))) < 1e-6

        page.get_by_role("button", name="Pick landmark 1 your run frame", exact=True).click()
        expect(page.locator("#alignmentDialog")).to_be_visible()
        expect(page.locator("#alignmentSetLandmark")).to_be_enabled(timeout=20000)
        page.get_by_role("button", name="Forward 1/30 second", exact=True).click()
        expect(page.locator("#alignmentSetLandmark")).to_be_enabled(timeout=20000)
        picked_time = float(page.locator("#alignmentTime").input_value())
        assert picked_time > landmark_current
        page.get_by_role("button", name="Use landmark", exact=True).click()
        expect(page.get_by_label("Landmark 1 your run source time", exact=True)).to_have_value(str(picked_time))
        landmarks = compare()
        page.reload()
        page.get_by_role("button", name="Analysis", exact=True).click()
        expect(page.locator("#comparisonAlignmentMode")).to_have_value("landmarks")
        expect(page.get_by_label("Landmark 1 your run source time", exact=True)).to_have_value(str(picked_time), timeout=30000)
        expect(page.locator("#comparisonStatus")).to_have_text("", timeout=30000)

        match = {"anchor_time": picked_time, "target_time": round(landmark_reference + .2, 3),
                 "score": .72, "inliers": 45, "method": "cross_view_landmark",
                 "confirmed": False, "requires_confirmation": True, "timing_accuracy_s": None}
        page.route("**/api/landmark-detection", lambda route: route.fulfill(json={"id": "landmark-fixture", "state": "running"}))
        page.route("**/api/lap-detection/landmark-fixture", lambda route: route.fulfill(json={
            "id": "landmark-fixture", "state": "completed", "result": {"candidates": [match], "warnings": []}}))
        page.get_by_role("button", name="Find landmark 1 matching reference scene", exact=True).click()
        review = page.get_by_role("button", name="Review landmark 1 suggested reference scene", exact=True)
        expect(review).to_be_visible(timeout=10000)
        expect(page.get_by_label("Landmark 1 reference source time", exact=True)).to_have_value(str(landmark_reference))
        review.click()
        expect(page.locator("#alignmentTime")).to_have_value(str(match["target_time"]))
        expect(page.locator("#alignmentSetLandmark")).to_be_enabled(timeout=20000)
        page.get_by_role("button", name="Use landmark", exact=True).click()
        expect(page.get_by_label("Landmark 1 reference source time", exact=True)).to_have_value(str(match["target_time"]))
        landmarks = compare()

        pixels = page.locator("#synchronizedChart").evaluate("""canvas => {
          const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
          const data = ctx.getImageData(0,0,w,h).data, lane = (h-34)/6;
          return Array.from({length:5}, (_, channel) => [[109,204,223],[224,191,121]].map(color => {
            let count = 0;
            for(let y=Math.ceil(10+channel*lane+22);y<10+(channel+1)*lane-10;y++)
              for(let x=60;x<w-15;x++) {const i=(y*w+x)*4;
                if(Math.hypot(data[i]-color[0],data[i+1]-color[1],data[i+2]-color[2])<50) count++;
              }
            return count;
          }));
        }""")
        assert all(all(count > 30 for count in pair) for pair in pixels), pixels
        page.locator(".lap-registration").scroll_into_view_if_needed()
        page.screenshot(path=str(args.output / "registered-comparison-desktop.png"))
        page.locator("#synchronizedChart").screenshot(path=str(args.output / "registered-comparison-traces.png"))
        for width in (390, 360):
            page.set_viewport_size({"width": width, "height": 844})
            page.locator(".lap-registration").scroll_into_view_if_needed()
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            page.screenshot(path=str(args.output / f"registered-comparison-mobile-{width}.png"))

        def cancellation_fixture(route):
            job_id = urlparse(route.request.url).path.split("/")[-2]
            cancelled_jobs.append(job_id)
            route.fulfill(json={"id": job_id, "state": "cancelled"})

        page.route("**/api/lap-detection/stale-*/cancel", cancellation_fixture)
        page.route("**/api/landmark-detection", lambda route: route.fulfill(json={"id": "stale-landmark", "state": "running"}))
        page.route("**/api/lap-detection/stale-landmark", lambda route: route.fulfill(json={"id": "stale-landmark", "state": "running"}))
        with page.expect_response(lambda response: response.url.endswith("/api/landmark-detection")):
            page.get_by_role("button", name="Find landmark 1 matching reference scene", exact=True).click()
        page.get_by_label("Landmark 1 your run source time", exact=True).fill(str(round(picked_time + .1, 3)))
        page.get_by_label("Landmark 1 your run source time", exact=True).press("Tab")
        expect(page.locator(".landmark-matches [role=status]")).to_have_text("Landmark changed. Search cancelled.", timeout=10000)
        expect(page.get_by_role("button", name="Find landmark 1 matching reference scene", exact=True)).to_be_enabled()
        assert "stale-landmark" in cancelled_jobs, cancelled_jobs
        page.get_by_label("Landmark 1 your run source time", exact=True).fill(str(picked_time))
        page.get_by_label("Landmark 1 your run source time", exact=True).press("Tab")

        page.route("**/api/lap-detection", lambda route: route.fulfill(json={"id": "stale-crossing", "state": "running"}))
        page.route("**/api/lap-detection/stale-crossing", lambda route: route.fulfill(json={"id": "stale-crossing", "state": "running"}))
        page.locator("#referenceFindCrossing").click()
        expect(page.locator("#referenceCancelCrossing")).to_be_visible()
        page.locator("#referenceStart").fill(str(args.reference_start + .1))
        page.locator("#referenceStart").dispatch_event("change")
        expect(page.locator("#referenceCrossingStatus")).to_have_text("Start changed. Search cancelled.", timeout=10000)
        expect(page.locator("#referenceFindCrossing")).to_be_enabled()
        expect(page.locator("#referenceCancelCrossing")).to_be_hidden()
        assert "stale-crossing" in cancelled_jobs, cancelled_jobs
        page.locator("#referenceStart").fill(str(args.reference_start))
        page.locator("#referenceStart").dispatch_event("change")
        page.locator("#confirmAlignment").check()

        crossing = {"start_time": args.reference_start, "end_time": args.reference_end - .1,
                    "duration": args.reference_end - args.reference_start - .1,
                    "score": .9, "method": "visual_landmark", "confirmed": False}
        page.route("**/api/lap-detection", lambda route: route.fulfill(json={"id": "browser-fixture", "state": "running"}))
        page.route("**/api/lap-detection/browser-fixture", lambda route: route.fulfill(json={
            "id": "browser-fixture", "state": "completed", "result": {"candidates": [crossing], "warnings": []}}))
        page.locator("#referenceFindCrossing").click()
        expect(page.locator("#referenceCrossingCandidates")).to_be_visible(timeout=10000)
        expect(page.locator("#referenceCrossingStatus")).to_contain_text("unconfirmed")
        page.locator("#referenceApplyCrossing").click()
        expect(page.locator("#referenceEnd")).to_have_value(f"{args.reference_end - .1:.3f}")
        expect(page.locator("#confirmFinishAlignment")).not_to_be_checked()
        expect(page.locator("#referenceRange")).to_have_value("custom")
        page.locator("#axisElapsed").click()
        elapsed = compare()
        assert elapsed["axis"] == "elapsed" and elapsed["alignment"]["mode"] == "raw"
        assert elapsed["delta_available"] and elapsed["delta_estimated"]
        assert any(point["delta_time_s"] is not None for point in elapsed["points"])
        expect(page.locator("#comparisonAlignmentMode")).to_be_disabled()
        after = page.request.get(args.url + "/api/status").json()
        assert after["state"] == before["state"] and after["session_id"] == before["session_id"]
        allowed = {"/api/compare", "/api/lap-detection", "/api/landmark-detection",
                   "/api/lap-detection/stale-landmark/cancel", "/api/lap-detection/stale-crossing/cancel"}
        assert all(path in allowed for _, path in writes), writes
        assert not errors, errors
        browser.close()

    report = {"source_controls_mutated": False, "page_errors": errors,
              "endpoint_delta_s": endpoints["points"][-1]["delta_time_s"],
              "paired_landmark_count": len(landmarks["alignment"]["anchors"]),
              "chart_current_reference_pixels": pixels,
              "crossing_picker": "isolated browser fixture, not detector accuracy",
              "matching_scene_picker": "isolated browser fixture; no timestamp applied before frame review",
              "stale_search_cancellations": cancelled_jobs,
              "physical_landmark_accuracy": "not asserted; test timestamps exercise registration only"}
    (args.output / "registered-comparison-browser-checks.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
