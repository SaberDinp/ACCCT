"""Read-only browser checks for two saved recordings on a running local server."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--current-session", required=True)
    parser.add_argument("--reference-session", required=True)
    parser.add_argument("--current-lap", default="lap-1")
    parser.add_argument("--reference-start", type=float, default=2.0)
    parser.add_argument("--reference-end", type=float, default=118.9)
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    errors, writes, screenshots = [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: writes.append((request.method, urlparse(request.url).path))
                if request.method not in {"GET", "HEAD", "OPTIONS"} else None)
        before = page.request.get(args.url + "/api/status").json()
        current_data = page.request.get(args.url + "/api/sessions/" + args.current_session).json()
        sessions = {"current": args.current_session, "reference": args.reference_session}
        saved = {side: hashlib.sha256(page.request.get(args.url + "/api/sessions/" + session).body()).hexdigest()
                 for side, session in sessions.items()}
        page.goto(args.url)
        page.get_by_role("button", name="Analysis", exact=True).click()
        expect(page.locator("#currentSession")).to_be_enabled(timeout=20000)
        page.wait_for_function("document.querySelectorAll('#currentSession option').length > 1")
        page.locator("#currentSession").select_option(args.current_session)
        page.locator("#referenceSession").select_option(args.reference_session)
        expect(page.locator("#currentRange")).to_be_enabled(timeout=20000)
        expect(page.locator("#referenceStart")).to_be_enabled(timeout=20000)
        page.locator("#currentRange").select_option(args.current_lap)
        page.locator("#referenceStart").fill(str(args.reference_start))
        page.locator("#referenceStart").dispatch_event("change")
        page.locator("#referenceEnd").fill(str(args.reference_end))
        page.locator("#referenceEnd").dispatch_event("change")
        page.locator("#confirmAlignment").uncheck()
        with page.expect_response(lambda response: response.url.endswith("/api/compare") and response.request.method == "POST") as response:
            page.locator("#runComparison").click()
        comparison = response.value.json()
        assert response.value.ok, comparison
        page.wait_for_function("document.getElementById('comparisonStatus').textContent === ''", timeout=20000)
        assert comparison["channels"]["steering"]["comparable"], comparison["channels"]["steering"]
        assert comparison["delta_available"] and comparison["delta_estimated"]
        assert any(point["delta_time_s"] is not None for point in comparison["points"])
        expect(page.locator(".delta-sign-note")).to_contain_text("Estimated delta")
        expect(page.locator("#registrationStatus")).to_contain_text("Unconfirmed start")
        expect(page.locator("#comparisonWarnings")).to_contain_text("physical start alignment is unconfirmed")
        legend = {}
        for side in ("current", "reference"):
            label = page.locator(f"#comparisonLegend{side.capitalize()}").inner_text()
            assert label and label not in {"Your run", "Reference"}, label
            style = page.locator(f".comparison-legend .run-line.{side}").evaluate("""element => {
              const style = getComputedStyle(element);
              return {color:style.borderTopColor, line:style.borderTopStyle};
            }""")
            assert style["color"] == {"current": "rgb(109, 204, 223)", "reference": "rgb(224, 191, 121)"}[side], style
            assert style["line"] == {"current": "solid", "reference": "dashed"}[side], style
            legend[side] = {"label": label, **style}
        assert legend["current"]["label"] != legend["reference"]["label"], legend
        page.locator("#synchronizedChart").hover(position={"x": 340, "y": 130})
        page.wait_for_function("!document.getElementById('currentFrame').hidden && !document.getElementById('referenceFrame').hidden")
        expect(page.locator("#comparisonCursor")).not_to_have_text("-- m")
        source_times = page.locator("#comparisonValues tr").filter(has_text="Source").locator("td").all_text_contents()
        expect(page.locator("#currentFrameTime")).to_have_text(source_times[1])
        expect(page.locator("#referenceFrameTime")).to_have_text(source_times[2])
        steering = page.locator("#comparisonValues tr").filter(has_text="Steering").locator("td").all_text_contents()
        assert steering[1] != "--" and steering[2] != "--", steering

        pixels = page.locator("#synchronizedChart").evaluate("""canvas => {
          const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
          const data = ctx.getImageData(0, 0, w, h).data, lane = (h - 34) / 6;
          const targets = {current:[109,204,223],reference:[224,191,121]};
          return Array.from({length:5}, (_,channel) => Object.fromEntries(
            Object.entries(targets).map(([side,target]) => {
              let count = 0;
              for (let y = Math.ceil(10 + channel * lane + 22); y < 10 + (channel + 1) * lane - 10; y++) {
                for (let x = 60; x < w - 15; x++) {
                  const i = (y * w + x) * 4;
                  if (Math.hypot(data[i] - target[0], data[i+1] - target[1], data[i+2] - target[2]) < 65) count++;
                }
              }
              return [side,count];
            })
          ));
        }""")
        assert all(count > 80 for lane in pixels for count in lane.values()), pixels
        page.screenshot(path=str(args.output / "reference-comparison-viewport.png"))
        screenshots.append("reference-comparison-viewport.png")
        page.locator("#synchronizedChart").screenshot(path=str(args.output / "reference-comparison-channels.png"))
        screenshots.append("reference-comparison-channels.png")

        page.get_by_role("button", name="Inspect reference source frame", exact=True).click()
        expect(page.locator("#alignmentDialog")).to_be_visible()
        page.locator("#alignmentTime").fill(str(args.reference_start + 0.033))
        page.locator("#alignmentTime").dispatch_event("input")
        page.wait_for_function("document.getElementById('alignmentImage').naturalWidth > 100")
        frame_time = args.reference_start + 0.033
        expected_stamp = f"Frame {int(frame_time // 60)}:{frame_time % 60:05.2f}"
        expect(page.locator("#alignmentFrameStamp")).to_have_text(expected_stamp)
        assert abs(float(page.locator("#alignmentTime").input_value()) - frame_time) < .0005
        page.get_by_role("button", name="Set IN", exact=True).click()
        page.get_by_role("button", name="Close alignment frame", exact=True).click()
        expect(page.locator("#referenceStart")).to_have_value(str(args.reference_start + 0.033))
        page.get_by_role("button", name="Zoom in", exact=True).click()
        page.get_by_role("button", name="Reset zoom", exact=True).click()
        page.reload()
        page.get_by_role("button", name="Analysis", exact=True).click()
        page.wait_for_function("value => document.getElementById('referenceStart').value === value", arg=str(args.reference_start + 0.033))
        expect(page.locator("#currentSession")).to_have_value(args.current_session)
        expect(page.locator("#referenceSession")).to_have_value(args.reference_session)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.getElementById('comparisonStatus').textContent === ''", timeout=20000)
        restored_times = page.locator("#comparisonValues tr").filter(has_text="Source").locator("td").all_text_contents()
        expect(page.locator("#currentFrameTime")).to_have_text(restored_times[1])
        expect(page.locator("#referenceFrameTime")).to_have_text(restored_times[2])
        page.wait_for_function("['currentFrame','referenceFrame'].every(id => { const image = document.getElementById(id); return !image.hidden && image.complete && image.naturalWidth > 100; })")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        page.screenshot(path=str(args.output / "reference-comparison-mobile.png"))
        screenshots.append("reference-comparison-mobile.png")

        after = page.request.get(args.url + "/api/status").json()
        assert after["session_id"] == before["session_id"] and after["state"] == before["state"]
        for side, session in sessions.items():
            digest = hashlib.sha256(page.request.get(args.url + "/api/sessions/" + session).body()).hexdigest()
            assert digest == saved[side], f"Saved {side} recording changed"
        assert all(path == "/api/compare" for _, path in writes), writes
        assert not errors, errors
        browser.close()

    result = {"current_session": args.current_session, "reference_session": args.reference_session,
              "chart_pixels_by_channel": dict(zip(["speed", "brake", "throttle", "gear", "steering"], pixels)),
              "lap_legend": legend, "unconfirmed_start_shows_estimated_delta": True,
              "source_controls_mutated": False, "saved_sessions_unchanged": True, "page_errors": errors, "screenshots": screenshots}
    (args.output / "comparison-browser-checks.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
