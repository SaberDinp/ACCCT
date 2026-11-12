"""Read-only browser regression for clearing either comparison recording.

Requires the running app and two saved sessions. Browser storage is isolated;
source controls, imports, and other mutating API requests are blocked.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


async def canvas_pixels(page, lanes):
    return await page.locator("#synchronizedChart").evaluate("""(canvas, count) => {
      const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
      const data = ctx.getImageData(0, 0, w, h).data, lane = (h - 34) / count;
      return Array.from({length:5}, (_, channel) => Object.fromEntries(
        Object.entries({current:[109,204,223], reference:[224,191,121]}).map(([side, rgb]) => {
          let pixels = 0;
          for (let y = Math.ceil(10 + channel * lane + 22); y < 10 + (channel + 1) * lane - 10; y++) {
            for (let x = 60; x < w - 15; x++) {
              const i = (y * w + x) * 4;
              if (data[i+3] > 60 && Math.hypot(data[i]-rgb[0], data[i+1]-rgb[1], data[i+2]-rgb[2]) < 40) pixels++;
            }
          }
          return [side, pixels];
        })
      ));
    }""", lanes)


async def rendered(page):
    await expect(page.locator("#runComparison")).to_have_attribute("aria-busy", "false", timeout=30000)
    await expect(page.locator("#comparisonValues")).to_contain_text("Source", timeout=30000)
    await page.wait_for_function(r"""() => {
      const rows = [...document.querySelectorAll('#comparisonValues tr')];
      const source = rows.find(row => row.textContent.includes('Source'));
      return source && [...source.querySelectorAll('td')].slice(1).some(cell => /\d/.test(cell.textContent));
    }""", timeout=30000)


async def settle_response(page):
    await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


async def assert_single(page, side="current"):
    await rendered(page)
    other = "reference" if side == "current" else "current"
    expect_selected = page.locator(f"#comparisonLegend{side.capitalize()}")
    expect_other = page.locator(f"#comparisonLegend{other.capitalize()}")
    await expect(expect_selected).to_be_visible()
    await expect(expect_other).to_be_hidden()
    await expect(page.locator("#comparisonDeltaSummary")).to_be_hidden()
    await expect(page.locator(f"#comparison{other.capitalize()}Summary")).to_be_hidden()
    await expect(page.locator(f"#{other}Frame")).to_be_hidden()
    await expect(page.locator(f"#{other}FrameTime")).to_be_hidden()
    for suffix in ("Range", "Start", "End", "Segment"):
        await expect(page.locator(f"#{other}{suffix}")).to_be_disabled()
    assert not await page.locator(f"#track{other}Image").evaluate("element => !!element.closest('figure').getClientRects().length")
    assert await page.locator(f"#track{side}Image").evaluate("element => !!element.closest('figure').getClientRects().length")
    headers = await page.locator(".cursor-table thead th:visible").all_text_contents()
    assert len(headers) == 2, headers
    assert not await page.locator("#comparisonValues").get_by_text("Delta / s", exact=True).count()
    assert "Both runs" not in await page.locator("#comparisonAvailability").inner_text()
    pixels = await canvas_pixels(page, 5)
    assert all(lane[side] > 50 for lane in pixels), pixels
    assert all(lane[other] == 0 for lane in pixels), pixels
    return pixels


async def assert_pair(page):
    await rendered(page)
    for side in ("current", "reference"):
        await expect(page.locator(f"#comparisonLegend{side.capitalize()}")).to_be_visible()
    await expect(page.locator("#comparisonDeltaSummary")).to_be_visible()
    headers = await page.locator(".cursor-table thead th:visible").all_text_contents()
    assert len(headers) == 4, headers
    pixels = await canvas_pixels(page, 6)
    assert all(lane[side] > 50 for lane in pixels for side in ("current", "reference")), pixels
    return pixels


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    errors, blocked, posts, checks, screenshots = [], [], [], {}, []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def guard(route):
            request = route.request
            path = urlparse(request.url).path
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                posts.append((request.method, path))
                if request.method != "POST" or path not in {"/api/compare", "/api/trace"}:
                    blocked.append((request.method, path))
                    await route.abort()
                    return
            await route.continue_()

        await context.route("**/api/**", guard)
        before = await (await page.request.get(args.url + "/api/status")).json()
        digests = {}
        for session in (args.current_session, args.reference_session):
            data = await (await page.request.get(args.url + "/api/sessions/" + session)).body()
            digests[session] = hashlib.sha256(data).hexdigest()
        preferences = {"axis": "distance", "alignment_mode": "raw", "align_start": False,
                       "align_end": False, "landmarks": [],
                       "current": {"id": args.current_session, "range": args.current_lap},
                       "reference": {"id": args.reference_session, "range": "custom",
                                     "start": args.reference_start, "end": args.reference_end,
                                     "segment": args.reference_segment}}
        await context.add_init_script("""if (!sessionStorage.getItem('single-recording-check')) {
          localStorage.clear(); localStorage.setItem('accc.comparison.v1', %s);
          sessionStorage.setItem('single-recording-check', '1');
        }""" % json.dumps(json.dumps(preferences)))
        await page.goto(args.url)
        await page.get_by_role("button", name="Analysis", exact=True).click()
        await expect(page.locator("#currentSession")).to_be_enabled(timeout=30000)
        await expect(page.locator("#referenceSession")).to_have_value(args.reference_session)
        checks["paired_pixels"] = await assert_pair(page)
        for selector in ("#eventsTable", ".events-section", ".analysis-stats"):
            assert not await page.locator(f"#view-analysis {selector}").count(), selector
        removed = ["Brake events", "Median brake duration", "Pedal overlap", "Valid HUD coverage", "Braking events"]
        analysis_text = await page.locator("#view-analysis").inner_text()
        assert all(label.lower() not in analysis_text.lower() for label in removed)
        checks["analysis_statistics_and_bottom_events_removed"] = True

        current_range = await page.locator("#currentRange").input_value()
        current_start = await page.locator("#currentStart").input_value()
        current_end = await page.locator("#currentEnd").input_value()
        await page.locator("#referenceSession").select_option("")
        checks["current_only_pixels"] = await assert_single(page)
        await expect(page.locator("#currentSession")).to_have_value(args.current_session)
        await expect(page.locator("#currentRange")).to_have_value(current_range)
        await expect(page.locator("#currentStart")).to_have_value(current_start)
        await expect(page.locator("#currentEnd")).to_have_value(current_end)
        async with page.expect_response(lambda response: response.url.endswith("/api/trace")) as axis_response:
            await page.locator("#axisElapsed").click()
        assert (await (await axis_response.value).json())["axis"] == "elapsed"
        await assert_single(page)
        async with page.expect_response(lambda response: response.url.endswith("/api/trace")) as axis_response:
            await page.locator("#axisDistance").click()
        assert (await (await axis_response.value).json())["axis"] == "distance"
        await assert_single(page)
        checks["current_range_preserved_and_both_axes_work"] = True
        await expect(page.locator("#referenceStart")).to_have_value("")
        await expect(page.locator("#referenceEnd")).to_have_value("")
        await expect(page.locator("#referenceDetail")).to_have_text("No recording selected")
        track = await (await page.request.get(args.url + "/api/tracks/silverstone")).json()
        await page.locator("#trackSelect").select_option("silverstone")
        await expect(page.locator("#trackSetOrigin")).to_be_enabled()
        box = await page.locator("#trackMap").bounding_box()
        size = min(box["width"], box["height"]) - 40
        origin = track["points"][0]
        await page.locator("#trackMap").click(position={"x": (box["width"] - size) / 2 + origin["x"] * size,
                                                        "y": (box["height"] - size) / 2 + origin["y"] * size})
        await page.locator("#trackRangesAligned").check()
        await expect(page.locator("#trackReviewPosition")).to_be_enabled()
        await expect(page.locator("#trackReviewStatus")).to_contain_text("Estimated lap progress")
        await expect(page.locator("#trackcurrentImage")).to_be_visible(timeout=15000)
        checks["single_recording_track_and_source_frame_work"] = True
        await page.locator("#synchronizedChart").screenshot(path=str(args.output / "single-recording-channels.png"))
        screenshots.append("single-recording-channels.png")
        await page.locator("#referenceWorkspace").scroll_into_view_if_needed()
        await page.screenshot(path=str(args.output / "single-recording-desktop.png"))
        screenshots.append("single-recording-desktop.png")

        await page.reload()
        await page.get_by_role("button", name="Analysis", exact=True).click()
        await expect(page.locator("#currentSession")).to_be_enabled(timeout=30000)
        await expect(page.locator("#referenceSession")).to_have_value("")
        await assert_single(page)
        checks["intentional_empty_reference_survives_reload"] = True
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.locator("#synchronizedChart").scroll_into_view_if_needed()
        assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        await page.screenshot(path=str(args.output / "single-recording-mobile.png"))
        screenshots.append("single-recording-mobile.png")
        await page.set_viewport_size({"width": 1440, "height": 1050})

        await page.locator("#referenceSession").select_option(args.reference_session)
        await expect(page.locator("#referenceRange")).to_be_enabled(timeout=30000)
        await assert_pair(page)
        checks["reselect_restores_comparison"] = True

        compare_arrived, compare_release, compare_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def hold_compare(route):
            response = await route.fetch()
            compare_arrived.set()
            await compare_release.wait()
            await route.fulfill(response=response)
            compare_finished.set()

        await context.route("**/api/compare", hold_compare)
        await page.locator("#runComparison").click()
        await asyncio.wait_for(compare_arrived.wait(), 30)
        await page.locator("#referenceSession").select_option("")
        await assert_single(page)
        compare_release.set()
        await asyncio.wait_for(compare_finished.wait(), 30)
        await context.unroute("**/api/compare", hold_compare)
        await settle_response(page)
        await assert_single(page)
        checks["late_comparison_does_not_restore_reference"] = True

        session_arrived, session_release, session_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def hold_session(route):
            response = await route.fetch()
            session_arrived.set()
            await session_release.wait()
            await route.fulfill(response=response)
            session_finished.set()

        session_pattern = f"**/api/sessions/{args.reference_session}"
        await context.route(session_pattern, hold_session)
        await page.locator("#referenceSession").select_option(args.reference_session)
        await asyncio.wait_for(session_arrived.wait(), 30)
        await page.locator("#referenceSession").select_option("")
        await assert_single(page)
        session_release.set()
        await asyncio.wait_for(session_finished.wait(), 30)
        await context.unroute(session_pattern, hold_session)
        await settle_response(page)
        await assert_single(page)
        checks["late_session_load_does_not_restore_reference"] = True

        await page.locator("#referenceSession").select_option(args.reference_session)
        await expect(page.locator("#referenceRange")).to_be_enabled(timeout=30000)
        await assert_pair(page)
        await page.locator("#synchronizedChart").hover(position={"x": 360, "y": 130})
        await expect(page.locator("#referenceFrame")).to_be_visible(timeout=15000)
        frame_pattern = f"**/api/sessions/{args.reference_session}/frame.jpg?**"
        frame_arrived, frame_release, frame_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        frame_held = False

        async def hold_frame(route):
            nonlocal frame_held
            if frame_held:
                await route.continue_()
                return
            frame_held = True
            response = await route.fetch()
            frame_arrived.set()
            await frame_release.wait()
            await route.fulfill(response=response)
            frame_finished.set()

        await context.route(frame_pattern, hold_frame)
        await page.locator("#synchronizedChart").hover(position={"x": 520, "y": 130})
        await asyncio.wait_for(frame_arrived.wait(), 30)
        await page.locator("#referenceSession").select_option("")
        await assert_single(page)
        frame_release.set()
        await asyncio.wait_for(frame_finished.wait(), 30)
        await context.unroute(frame_pattern, hold_frame)
        await settle_response(page)
        await assert_single(page)
        checks["late_reference_frame_stays_hidden"] = True

        await page.locator("#referenceSession").select_option(args.reference_session)
        await expect(page.locator("#referenceRange")).to_be_enabled(timeout=30000)
        await assert_pair(page)
        await expect(page.locator("#referenceFrame")).to_be_visible(timeout=15000)
        failure_arrived, failure_release, failure_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        failure_held = False

        async def hold_frame_failure(route):
            nonlocal failure_held
            if failure_held:
                await route.continue_()
                return
            failure_held = True
            failure_arrived.set()
            await failure_release.wait()
            await route.fulfill(status=503, body="Delayed failed frame")
            failure_finished.set()

        await context.route(frame_pattern, hold_frame_failure)
        await page.locator("#synchronizedChart").hover(position={"x": 480, "y": 130})
        await asyncio.wait_for(failure_arrived.wait(), 30)
        await page.locator("#referenceSession").select_option("")
        await assert_single(page)
        await page.locator("#referenceSession").select_option(args.reference_session)
        await expect(page.locator("#referenceRange")).to_be_enabled(timeout=30000)
        await assert_pair(page)
        await expect(page.locator("#referenceFrame")).to_be_visible(timeout=15000)
        new_frame = await page.locator("#referenceFrame").get_attribute("src")
        failure_release.set()
        await asyncio.wait_for(failure_finished.wait(), 30)
        await context.unroute(frame_pattern, hold_frame_failure)
        await settle_response(page)
        await expect(page.locator("#referenceFrame")).to_be_visible()
        await expect(page.locator("#referenceFrame")).to_have_attribute("src", new_frame)
        checks["late_failed_frame_does_not_hide_new_selection"] = True

        await page.locator("#currentSession").select_option("")
        checks["reference_only_pixels"] = await assert_single(page, "reference")
        await page.locator("#referenceSession").select_option("")
        await expect(page.locator("#comparisonLegendCurrent")).to_be_hidden()
        await expect(page.locator("#comparisonLegendReference")).to_be_hidden()
        pixels = await canvas_pixels(page, 5)
        assert all(value == 0 for lane in pixels for value in lane.values()), pixels
        await page.reload()
        await page.get_by_role("button", name="Analysis", exact=True).click()
        await expect(page.locator("#currentSession")).to_be_enabled(timeout=30000)
        await expect(page.locator("#currentSession")).to_have_value("")
        await expect(page.locator("#referenceSession")).to_have_value("")
        checks["both_empty_survive_reload"] = True

        after = await (await page.request.get(args.url + "/api/status")).json()
        assert after["session_id"] == before["session_id"] and after["state"] == before["state"]
        for session, original in digests.items():
            data = await (await page.request.get(args.url + "/api/sessions/" + session)).body()
            assert hashlib.sha256(data).hexdigest() == original, "Saved recording changed"
        assert not blocked, blocked
        assert not errors, errors
        await browser.close()
    result = {"checks": checks, "page_errors": errors, "blocked_mutations": blocked,
              "allowed_post_paths": sorted({path for _, path in posts}),
              "source_controls_mutated": False, "saved_sessions_unchanged": True,
              "screenshots": screenshots}
    (args.output / "single-recording-browser-checks.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--current-session", required=True)
    parser.add_argument("--reference-session", required=True)
    parser.add_argument("--current-lap", default="lap-1")
    parser.add_argument("--reference-start", type=float, default=2.0)
    parser.add_argument("--reference-end", type=float, default=118.9)
    parser.add_argument("--reference-segment", default="1")
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    asyncio.run(run(parser.parse_args()))
