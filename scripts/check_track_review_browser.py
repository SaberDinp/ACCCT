"""Read-only real-recording regression for track and paired-frame review.

The chosen map origin is a synthetic browser-test input, not a verified start line.
Only /api/compare POSTs are allowed; saved recordings and source controls are untouched.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


def canvas_pixels(page):
    return page.locator("#trackMap").evaluate("""canvas => {
      const data = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
      const colors = {centerline:[116,139,128],origin:[213,233,145],marker:[109,204,223]};
      return Object.fromEntries(Object.entries(colors).map(([name,color]) => {
        let count=0;
        for(let i=0;i<data.length;i+=4) {
          if(data[i+3]>180 && Math.hypot(data[i]-color[0],data[i+1]-color[1],data[i+2]-color[2])<12) count++;
        }
        return [name,count];
      }));
    }""")


def click_track_point(page, point):
    canvas = page.locator("#trackMap")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    size = min(box["width"], box["height"]) - 40
    canvas.click(position={"x": (box["width"] - size) / 2 + point["x"] * size,
                           "y": (box["height"] - size) / 2 + point["y"] * size})


def seconds(label):
    minute, second = label.split(":")
    return int(minute) * 60 + float(second)


def wait_comparison(page):
    page.wait_for_function("document.getElementById('comparisonStatus').textContent === ''", timeout=30000)
    expect(page.locator("#runComparison")).to_be_enabled()


def seek_distance(page, value):
    page.locator("#trackReviewDistance").fill(str(round(value)))
    page.locator("#trackReviewDistance").dispatch_event("input")


def assert_source_frames(page, comparison, frame_requests, sessions):
    page.wait_for_function("""() => ['current','reference'].every(side => {
      const image=document.getElementById(`track${side}Image`);
      return !image.hidden && image.complete && image.naturalWidth>100;
    })""", timeout=30000)
    position = float(page.locator("#trackReviewDistance").input_value())
    point = min(comparison["points"], key=lambda item: abs(item["position"] - position))
    row = page.locator("#comparisonValues tr").filter(has_text="Source").locator("td").all_text_contents()
    result = {}
    for index, side in enumerate(("current", "reference"), 1):
        label = page.locator(f"#track{side}Time").inner_text()
        assert label == row[index], (side, label, row)
        stamp = seconds(label)
        assert abs(stamp - point[side]["time"]) < 0.011, (side, stamp, point[side]["time"])
        assert any(request["session"] == sessions[side] and abs(request["time"] - point[side]["time"]) < .0005
                   for request in frame_requests), (side, point[side]["time"], frame_requests[-8:])
        pixels = page.locator(f"#track{side}Image").evaluate("""image => {
          const canvas=document.createElement('canvas'); canvas.width=96; canvas.height=54;
          const ctx=canvas.getContext('2d'); ctx.drawImage(image,0,0,96,54);
          const data=ctx.getImageData(0,0,96,54).data; const colors=new Set();
          let sum=0,squares=0;
          for(let i=0;i<data.length;i+=4) {
            colors.add(`${data[i]>>4},${data[i+1]>>4},${data[i+2]>>4}`);
            const brightness=(data[i]+data[i+1]+data[i+2])/3;
            sum+=brightness; squares+=brightness*brightness;
          }
          const n=data.length/4;
          return {width:image.naturalWidth,height:image.naturalHeight,colors:colors.size,variance:squares/n-(sum/n)**2};
        }""")
        assert pixels["colors"] > 30 and pixels["variance"] > 100, pixels
        result[side] = {"time": stamp, "source_time": point[side]["time"], "pixels": pixels}
    return result


def assert_layout(page):
    measurement = page.evaluate("""() => {
      const root=document.documentElement;
      const offenders=[...document.querySelectorAll('#trackReview *')].filter(e=> {
        const r=e.getBoundingClientRect();
        return r.width && r.height && (r.left < -1 || r.right > innerWidth+1);
      }).map(e=>e.id || e.className);
      const boxes=['trackSelect','trackSetOrigin','trackReviewPosition','trackReverse','trackRangesAligned']
        .map(id=> {const r=document.getElementById(id).getBoundingClientRect();
          return {id,x:r.x,y:r.y,width:r.width,height:r.height};});
      return {viewport:innerWidth,scrollWidth:root.scrollWidth,offenders,boxes};
    }""")
    assert measurement["scrollWidth"] <= measurement["viewport"] + 1, measurement
    assert not measurement["offenders"], measurement
    for index, a in enumerate(measurement["boxes"]):
        for b in measurement["boxes"][index + 1:]:
            assert not (a["x"] < b["x"] + b["width"] and a["x"] + a["width"] > b["x"] and
                        a["y"] < b["y"] + b["height"] and a["y"] + a["height"] > b["y"]), (a, b)
    return measurement


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--current-session", default="0462ac78f63b4d078901d395ca77bac7")
    parser.add_argument("--reference-session", default="d296ac6b613944dc8bb0959c505a6615")
    parser.add_argument("--current-lap", default="lap-1")
    parser.add_argument("--reference-start", type=float, default=2.0)
    parser.add_argument("--reference-end", type=float, default=118.9)
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    errors, writes, blocked_writes, frame_requests = [], [], [], []
    result = {"current_session": args.current_session, "reference_session": args.reference_session,
              "origin_kind": "synthetic test input; not a verified ACC start line", "checks": {}, "screenshots": []}
    sessions = {"current": args.current_session, "reference": args.reference_session}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def guard(route):
            request = route.request
            path = urlparse(request.url).path
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                writes.append({"method": request.method, "path": path})
                if request.method != "POST" or path != "/api/compare":
                    blocked_writes.append(writes[-1])
                    route.abort()
                    return
            if path.endswith("/frame.jpg"):
                frame_requests.append({"session": path.split("/")[-2],
                                       "time": float(parse_qs(urlparse(request.url).query)["time"][0])})
            route.continue_()

        context.route("**/*", guard)
        try:
            for attempt in range(30):
                tracks_response = page.request.get(args.url + "/api/tracks")
                if tracks_response.ok:
                    break
                time.sleep(2)
            assert tracks_response.ok, tracks_response.status
            tracks = tracks_response.json()
            assert len(tracks) >= 6, tracks
            before = page.request.get(args.url + "/api/status").json()
            saved = {side: hashlib.sha256(page.request.get(args.url + "/api/sessions/" + session).body()).hexdigest()
                     for side, session in sessions.items()}
            page.goto(args.url)
            page.get_by_role("button", name="Analysis", exact=True).click()
            page.wait_for_function("document.querySelectorAll('#currentSession option').length > 1")
            page.locator("#currentSession").select_option(args.current_session)
            page.locator("#referenceSession").select_option(args.reference_session)
            expect(page.locator("#currentRange")).to_be_enabled(timeout=20000)
            expect(page.locator("#referenceStart")).to_be_enabled(timeout=20000)
            page.locator("#currentRange").select_option(args.current_lap)
            for edge, value in (("Start", args.reference_start), ("End", args.reference_end)):
                page.locator("#reference" + edge).fill(str(value))
                page.locator("#reference" + edge).dispatch_event("change")
            page.locator("#confirmAlignment").uncheck()
            with page.expect_response(lambda response: response.url.endswith("/api/compare") and response.request.method == "POST") as pending:
                page.locator("#runComparison").click()
            comparison = pending.value.json()
            assert pending.value.ok and comparison["axis"] == "distance", comparison
            assert comparison["delta_available"] and comparison["delta_estimated"]
            assert any(point["delta_time_s"] is not None for point in comparison["points"])
            wait_comparison(page)
            expect(page.locator(".delta-sign-note")).to_contain_text("Estimated delta")
            expect(page.locator("#registrationStatus")).to_contain_text("Unconfirmed start")
            page.wait_for_function("document.querySelectorAll('#trackSelect option').length >= 7")
            expect(page.locator("#trackReview")).to_contain_text("Racing line not measured")
            result["checks"]["tracks"] = {}
            silverstone = None
            for track in tracks:
                data = page.request.get(args.url + "/api/tracks/" + track["id"]).json()
                assert data["geometry_kind"] == "approximate_map_centerline" and not data["racing_line_available"]
                page.locator("#trackSelect").select_option(track["id"])
                expect(page.locator("#trackOriginStatus")).to_have_text("Lap origin not set")
                expect(page.locator("#trackReviewStatus")).to_contain_text("Lap origin required")
                expect(page.locator("#trackRangesAligned")).to_be_disabled()
                expect(page.locator("#trackReviewPosition")).to_be_disabled()
                pixels = canvas_pixels(page)
                assert pixels["centerline"] > 400 and pixels["origin"] == pixels["marker"] == 0, pixels
                result["checks"]["tracks"][track["id"]] = {"points": len(data["points"]), "pixels": pixels}
                if track["id"] == "silverstone":
                    silverstone = data
            assert silverstone, "Silverstone missing from the track library"
            page.locator("#trackSelect").select_option("silverstone")
            expect(page.locator("#trackReviewStatus")).to_contain_text("Lap origin required")
            origin = min(silverstone["points"], key=lambda item: abs(item["distance_m"] - .15 * silverstone["length_m"]))
            target = min(silverstone["points"], key=lambda item: abs(item["distance_m"] - .30 * silverstone["length_m"]))
            click_track_point(page, origin)
            expect(page.locator("#trackRangesAligned")).to_be_enabled()
            expect(page.locator("#trackRangesAligned")).not_to_be_checked()
            expect(page.locator("#trackReviewPosition")).to_be_disabled()
            expect(page.locator("#trackReviewStatus")).to_contain_text("unconfirmed")
            pixels = canvas_pixels(page)
            assert pixels["origin"] > 60 and pixels["marker"] == 0, pixels
            page.locator("#trackRangesAligned").check()
            expect(page.locator("#trackReviewPosition")).to_be_enabled()
            expect(page.locator("#trackReviewStatus")).to_contain_text("Estimated lap progress only")
            assert canvas_pixels(page)["marker"] > 40
            click_track_point(page, target)
            position = float(page.locator("#trackReviewDistance").input_value())
            expected_distance = target["distance_m"] - origin["distance_m"]
            assert abs(position - expected_distance) < 20, (position, expected_distance)
            result["checks"]["map_click"] = {"distance_m": position, "expected_distance_m": expected_distance}
            result["checks"]["paired_frames"] = assert_source_frames(page, comparison, frame_requests, sessions)
            page.locator("#trackMap").press("ArrowRight")
            after_key = float(page.locator("#trackReviewDistance").input_value())
            assert 15 <= after_key - position <= 35, (position, after_key)
            result["checks"]["keyboard_seek"] = True

            # Both the chosen origin and its confirmation survive a same-range reload.
            page.reload()
            page.get_by_role("button", name="Analysis", exact=True).click()
            wait_comparison(page)
            expect(page.locator("#trackSelect")).to_have_value("silverstone", timeout=20000)
            expect(page.locator("#trackRangesAligned")).to_be_checked(timeout=20000)
            expect(page.locator("#trackOriginStatus")).to_have_text("Manually placed lap origin")
            expect(page.locator("#trackReviewPosition")).to_be_enabled()
            result["checks"]["same_range_persistence"] = True
            seek_distance(page, 1500)
            result["checks"]["reloaded_frames"] = assert_source_frames(page, comparison, frame_requests, sessions)

            for width, height in ((1440, 1050), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.locator("#trackReview").scroll_into_view_if_needed()
                result["checks"][f"layout_{width}"] = assert_layout(page)
                pixels = canvas_pixels(page)
                assert pixels["centerline"] > 400 and pixels["marker"] > 40, pixels
                result["checks"][f"canvas_{width}"] = pixels
                name = f"track-review-{width}.png"
                page.locator("#trackReview").screenshot(path=str(args.output / name))
                result["screenshots"].append(name)
                name = f"track-review-viewport-{width}.png"
                page.screenshot(path=str(args.output / name))
                result["screenshots"].append(name)

            page.set_viewport_size({"width": 1440, "height": 1050})
            page.locator("#trackreferenceInspect").click()
            expect(page.locator("#alignmentDialog")).to_be_visible()
            expect(page.locator("#alignmentFrameStamp")).to_have_text("Frame " + page.locator("#trackreferenceTime").inner_text())
            inspected_time = float(page.locator("#alignmentTime").input_value())
            selected_time = result["checks"]["reloaded_frames"]["reference"]["source_time"]
            assert abs(inspected_time - selected_time) <= .00051, (inspected_time, selected_time)
            page.get_by_role("button", name="Close alignment frame", exact=True).click()
            result["checks"]["inspect_paired_frame"] = {"source_time": inspected_time, "expected_source_time": selected_time,
                                                        "input_precision_s": .001, "display_precision_s": .01}

            page.locator("#referenceEnd").fill(str(args.reference_end - 1))
            page.locator("#referenceEnd").dispatch_event("change")
            wait_comparison(page)
            expect(page.locator("#trackRangesAligned")).not_to_be_checked()
            expect(page.locator("#trackReviewPosition")).to_be_disabled()
            expect(page.locator("#trackReviewStatus")).to_contain_text("unconfirmed")
            assert canvas_pixels(page)["marker"] == 0
            page.reload()
            page.get_by_role("button", name="Analysis", exact=True).click()
            wait_comparison(page)
            expect(page.locator("#trackSelect")).to_have_value("silverstone", timeout=20000)
            expect(page.locator("#trackRangesAligned")).not_to_be_checked()
            result["checks"]["new_range_revokes_alignment_after_reload"] = True
            page.locator("#trackRangesAligned").check()
            page.locator("#axisElapsed").click()
            wait_comparison(page)
            expect(page.locator("#trackReviewDistance")).to_be_disabled()
            expect(page.locator("#trackReviewPosition")).to_be_disabled()
            assert canvas_pixels(page)["marker"] == 0
            result["checks"]["elapsed_axis_has_no_map_marker"] = True
            after = page.request.get(args.url + "/api/status").json()
            assert after["session_id"] == before["session_id"] and after["state"] == before["state"]
            for side, session in sessions.items():
                digest = hashlib.sha256(page.request.get(args.url + "/api/sessions/" + session).body()).hexdigest()
                assert digest == saved[side], f"Saved {side} recording changed"
            assert not blocked_writes, blocked_writes
            assert not errors, errors
            result.update({"passed": True, "source_controls_mutated": False, "saved_sessions_unchanged": True})
        except Exception as error:
            result.update({"passed": False, "error": f"{type(error).__name__}: {error}"})
            page.screenshot(path=str(args.output / "track-review-failure.png"), full_page=True)
            raise
        finally:
            result.update({"page_errors": errors, "writes": writes, "blocked_writes": blocked_writes})
            (args.output / "track-review-browser-checks.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result, indent=2))
            browser.close()


if __name__ == "__main__":
    main()
