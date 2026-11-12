"""Read-only real-video checks for the reviewed two-recording library."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--current-session", default="0241bca2f4364903a11272f6ca70f724")
    parser.add_argument("--reference-session", default="58d21833cec0498d8f8a1d593c946d76")
    parser.add_argument("--expected-delta", type=float, default=.53)
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    errors, writes, blocked = [], [], []
    defaults, digests = {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        before = page.request.get(args.url + "/api/status").json()
        sessions = page.request.get(args.url + "/api/sessions").json()
        assert {item["id"] for item in sessions} == {args.current_session, args.reference_session}, sessions
        assert all(item["status"] == "completed" for item in sessions)
        for side in ("current", "reference"):
            response = page.request.get(args.url + "/api/sessions/" + getattr(args, side + "_session"))
            digests[side] = hashlib.sha256(response.body()).hexdigest()
            data = response.json()
            metadata = data["session"]["metadata"]
            selected = metadata["comparison_defaults"]["range_id"]
            defaults[side] = next(lap for lap in metadata["reviewed_laps"] if lap["id"] == selected)
            assert defaults[side]["boundaries_verified"]
        assert defaults["current"]["start_marker"] == defaults["reference"]["start_marker"]
        assert defaults["current"]["end_marker"] == defaults["reference"]["end_marker"]

        def guard(route):
            request = route.request
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                path = urlparse(request.url).path
                writes.append((request.method, path))
                if request.method != "POST" or path not in {"/api/compare", "/api/trace"}:
                    blocked.append((request.method, path))
                    route.abort()
                    return
            route.continue_()

        context.route("**/api/**", guard)
        with page.expect_response(lambda response: response.url.endswith("/api/compare") and response.ok, timeout=30000) as initial:
            page.goto(args.url)
            page.get_by_role("button", name="Analysis", exact=True).click()
        comparison = initial.value.json()
        expect(page.locator("#comparisonStatus")).to_have_text("", timeout=30000)
        for side, lap in defaults.items():
            expect(page.locator(f"#{side}Session")).to_have_value(getattr(args, side + "_session"))
            expect(page.locator(f"#{side}Range")).to_have_value(lap["id"])
            expect(page.locator(f"#{side}Start")).to_have_value(f"{lap['start_time']:.3f}")
            expect(page.locator(f"#{side}End")).to_have_value(f"{lap['end_time']:.3f}")
        expect(page.locator("#confirmAlignment")).to_be_checked()
        expect(page.locator("#confirmFinishAlignment")).to_be_checked()
        assert comparison["delta_available"] and comparison["alignment"]["applied"]
        final = comparison["points"][-1]
        expected = ((defaults["current"]["end_time"] - defaults["current"]["start_time"]) -
                    (defaults["reference"]["end_time"] - defaults["reference"]["start_time"]))
        assert abs(final["delta_time_s"] - expected) < 1e-6
        assert abs(final["delta_time_s"] - args.expected_delta) < .06
        for side in ("current", "reference"):
            assert abs(final[side]["time"] - defaults[side]["end_time"]) < 1e-6
            assert max(point[side]["time"] for point in comparison["points"] if point[side]) <= defaults[side]["end_time"] + 1e-6

        canvas = page.locator("#synchronizedChart")
        box = canvas.bounding_box()
        canvas.hover(position={"x": box["width"] - 2, "y": 70})
        source = page.locator("#comparisonValues tr").filter(has_text="Source").locator("td").all_text_contents()
        for side, column in (("current", 1), ("reference", 2)):
            expect(page.locator(f"#{side}FrameTime")).to_have_text(source[column], timeout=20000)
            page.wait_for_function("id => {const image=document.getElementById(id);return !image.hidden && image.naturalWidth > 100}", arg=f"track{side}Image", timeout=20000)
        pixels = canvas.evaluate("""canvas => {
          const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;
          const data=ctx.getImageData(0,0,w,h).data,lane=(h-34)/6;
          return Array.from({length:5},(_,channel)=>[[109,204,223],[224,191,121]].map(color=>{
            let count=0;
            for(let y=Math.ceil(10+channel*lane+22);y<10+(channel+1)*lane-10;y++)
              for(let x=60;x<w-15;x++){const i=(y*w+x)*4;
                if(Math.hypot(data[i]-color[0],data[i+1]-color[1],data[i+2]-color[2])<50)count++;
              }
            return count;
          }));
        }""")
        assert all(all(value > 30 for value in pair) for pair in pixels), pixels
        page.locator(".comparison-legend").scroll_into_view_if_needed()
        page.screenshot(path=str(args.output / "two-video-reviewed-comparison.png"))
        canvas.screenshot(path=str(args.output / "two-video-reviewed-traces.png"))
        requested_frame = round(defaults["reference"]["end_time"], 3)
        frame = page.request.get(f"{args.url}/api/sessions/{args.reference_session}/frame.jpg?time={requested_frame}")
        decoded = float(frame.headers["x-source-time"])
        page.get_by_role("button", name="Inspect reference finish crossing", exact=True).click()
        expect(page.locator("#alignmentFrameStamp")).to_have_text(f"Frame {int(decoded // 60)}:{decoded % 60:06.3f}", timeout=20000)
        expect(page.locator("#alignmentTime")).to_have_value(str(requested_frame))
        page.get_by_role("button", name="Close alignment frame", exact=True).click()

        page.locator("#referenceSession").select_option("")
        expect(page.locator("#comparisonDeltaSummary")).to_be_hidden()
        expect(page.locator("#currentRange")).to_have_value(defaults["current"]["id"])
        expect(page.locator("#comparisonLegendReference")).to_be_hidden()
        page.reload()
        page.get_by_role("button", name="Analysis", exact=True).click()
        expect(page.locator("#referenceSession")).to_have_value("", timeout=20000)
        expect(page.locator("#currentRange")).to_have_value(defaults["current"]["id"], timeout=20000)
        with page.expect_response(lambda response: response.url.endswith("/api/compare") and response.ok, timeout=30000) as restored:
            page.locator("#referenceSession").select_option(args.reference_session)
        assert restored.value.json()["delta_available"]
        expect(page.locator("#confirmFinishAlignment")).to_be_checked()
        for width in (390, 360):
            page.set_viewport_size({"width": width, "height": 844})
            page.locator(".comparison-legend").scroll_into_view_if_needed()
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            page.screenshot(path=str(args.output / f"two-video-reviewed-mobile-{width}.png"))
        for side in ("current", "reference"):
            response = page.request.get(args.url + "/api/sessions/" + getattr(args, side + "_session"))
            assert hashlib.sha256(response.body()).hexdigest() == digests[side]
        after = page.request.get(args.url + "/api/status").json()
        assert after["state"] == before["state"] and after["session_id"] == before["session_id"]
        assert not errors and not blocked, {"errors": errors, "blocked": blocked}
        browser.close()
    report = {"session_count": 2, "reviewed_laps": defaults, "default_delta_s": final["delta_time_s"],
              "no_trace_past_reviewed_finish": True, "single_recording_restore": True,
              "paired_source_frames_loaded": True, "trace_pixels_current_reference": pixels,
              "inspector_decoded_frame_pts": decoded,
              "source_controls_mutated": False, "session_digests_unchanged": True, "page_errors": errors}
    (args.output / "two-video-browser-checks.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
