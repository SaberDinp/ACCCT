"""Exercise deliberate lap locks using isolated browser/API fixtures."""
import argparse
import asyncio
import functools
import json
import re
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

from playwright.async_api import async_playwright, expect
from check_delete_swap_browser import (ROOT, CURRENT, REFERENCE, PREFERENCES,
    StaticHandler, new_fixture_context, loaded)
from check_single_recording_browser import rendered


async def lock(page, side, edge, value):
    await page.locator(f"#{side}{edge}Boundary").click()
    await expect(page.locator("#alignmentTitle")).to_have_text(f"Selecting {edge.lower()} of lap")
    await page.locator("#alignmentTime").fill(str(value))
    await expect(page.locator("#alignmentLock")).to_be_enabled(timeout=15000)
    await page.locator("#alignmentLock").click()
    await expect(page.locator("#alignmentDialog")).to_be_hidden()
    await expect(page.locator(f"#{side}{edge}Boundary")).to_have_class("button subtle lap-boundary-button inspect-boundary is-locked")
    await expect(page.locator("#comparisonStatus")).not_to_have_class(re.compile("comparison-dirty"), timeout=20000)
    await rendered(page)


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(StaticHandler, directory=str(ROOT / "web")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks = [], {}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context, page, fixture = await new_fixture_context(browser, f"http://127.0.0.1:{server.server_port}", errors)
            await loaded(page)
            removed = "#currentSegment, #referenceSegment, #comparisonAlignmentMode, #confirmAlignment, #confirmFinishAlignment, #confirmSameTrack, #referenceAutoCalibration, #observedSegments, #contextDialog, .find-crossing, .save-lap-range, .landmark-row"
            await expect(page.locator(removed)).to_have_count(0)
            await expect(page.locator(".run-video-options > summary")).to_have_text(["Video options", "Video options"])
            await expect(page.locator("#currentExportCsv")).to_have_attribute("href", f"/api/export/{CURRENT}.csv")
            await expect(page.locator("#lapBoundaryHint")).to_be_visible()
            assert fixture.comparisons[-1]["alignment_mode"] == "raw"
            assert fixture.comparisons[-1]["landmarks"] == []
            assert not fixture.comparisons[-1]["align_start"] and not fixture.comparisons[-1]["align_end"]
            checks["legacy_flags_dropped_and_controls_simplified"] = True

            before = await page.evaluate("localStorage.getItem('accc.comparison.v1')")
            await page.locator("#currentEndBoundary").click()
            await expect(page.locator("#alignmentTitle")).to_have_text("Selecting end of lap")
            await expect(page.locator("#alignmentDialog .button.primary")).to_have_count(1)
            await expect(page.locator("#alignmentLock")).to_have_text("Lock")
            await page.locator("#alignmentTime").fill("120")
            await expect(page.locator("#alignmentLock")).to_be_enabled(timeout=15000)
            await page.locator("#closeAlignment").click()
            assert await page.evaluate("localStorage.getItem('accc.comparison.v1')") == before
            await expect(page.locator("#currentEnd")).to_have_value("110")
            checks["inspection_and_seek_do_not_change_boundaries"] = True

            await page.locator("#currentStartBoundary").click()
            await page.locator("#alignmentTime").fill("120")
            await expect(page.locator("#alignmentLock")).to_be_enabled(timeout=15000)
            await page.locator("#alignmentLock").click()
            await expect(page.locator("#alignmentError")).to_contain_text("adjust End of lap first")
            await expect(page.locator("#currentEnd")).to_have_value("110")
            await expect(page.locator("#currentStart")).to_have_value("10")
            await page.locator("#closeAlignment").click()
            checks["invalid_order_keeps_both_saved_boundaries"] = True

            await lock(page, "current", "Start", 10)
            assert not fixture.comparisons[-1]["align_start"]
            await lock(page, "reference", "Start", 12)
            assert fixture.comparisons[-1]["align_start"] and not fixture.comparisons[-1]["align_end"]
            await expect(page.locator("#lapBoundaryHint")).to_be_hidden()
            await lock(page, "current", "End", 110)
            await lock(page, "reference", "End", 110)
            assert fixture.comparisons[-1]["align_start"] and fixture.comparisons[-1]["align_end"]
            saved = await page.evaluate("JSON.parse(localStorage.getItem('accc.comparison.v1'))")
            assert saved["current"]["boundary_locks"] == {"start": 10, "end": 110}
            assert saved["reference"]["boundary_locks"] == {"start": 12, "end": 110}
            assert not fixture.comparisons[-1]["confirm_same_track"]
            assert fixture.comparisons[-1]["current_range"].get("segment_id") == 1
            checks["only_deliberate_locks_confirm_alignment"] = True

            await page.locator("#swapComparisonRuns").click()
            await rendered(page)
            await page.reload()
            await loaded(page)
            saved = await page.evaluate("JSON.parse(localStorage.getItem('accc.comparison.v1'))")
            assert saved["current"]["id"] == REFERENCE
            assert saved["current"]["boundary_locks"] == {"start": 12, "end": 110}
            assert saved["reference"]["boundary_locks"] == {"start": 10, "end": 110}
            assert fixture.comparisons[-1]["align_start"] and fixture.comparisons[-1]["align_end"]
            checks["swapping_and_reload_preserve_exact_locks"] = True
            await page.screenshot(path=str(args.output / "lap-boundaries.png"), full_page=True)
            await context.close()

            # Exact timestamp and recording revision prevent stale confirmations.
            context, page, fixture = await new_fixture_context(browser, f"http://127.0.0.1:{server.server_port}", errors)
            await loaded(page)
            pref = json.loads(json.dumps(PREFERENCES))
            for side in ("current", "reference"):
                pref[side]["boundary_locks"] = {"start": pref[side]["start"] + 1, "end": 110}
            await page.evaluate("p => localStorage.setItem('accc.comparison.v1', JSON.stringify(p))", pref)
            await page.reload()
            await loaded(page)
            assert not fixture.comparisons[-1]["align_start"]
            checks["stale_lock_timestamp_not_restored"] = True

            fixture.sessions[CURRENT]["session"]["context"] = {"track_id": "monza"}
            fixture.sessions[REFERENCE]["session"]["context"] = {"track_id": "silverstone"}
            await page.reload()
            await loaded(page)
            await lock(page, "current", "Start", 10)
            await lock(page, "reference", "Start", 12)
            assert not fixture.comparisons[-1]["align_start"]
            checks["known_circuit_mismatch_not_overridden_by_locks"] = True
            assert not fixture.unexpected, fixture.unexpected
            await context.close()

            context, page, fixture = await new_fixture_context(browser, f"http://127.0.0.1:{server.server_port}", errors)
            await loaded(page)
            pref = json.loads(json.dumps(PREFERENCES))
            for data in fixture.sessions.values():
                for sample in data["samples"]:
                    sample["time"] += 25
            for side in ("current", "reference"):
                pref[side]["start"] += 25
                pref[side]["end"] += 25
            await page.evaluate("p => localStorage.setItem('accc.comparison.v1', JSON.stringify(p))", pref)
            await page.reload()
            await loaded(page)
            await page.locator("#currentStartBoundary").click()
            await expect(page.locator("#alignmentTime")).to_have_attribute("min", "25")
            await expect(page.locator("#alignmentTime")).to_have_attribute("max", "185")
            await page.locator("#alignmentTime").fill("0")
            await expect(page.locator("#alignmentTime")).to_have_value("25.000")
            await expect(page.locator("#alignmentLock")).to_be_enabled(timeout=15000)
            await page.locator("#alignmentLock").click()
            await expect(page.locator("#currentStart")).to_have_value("25.000")
            await expect(page.locator("#currentEnd")).to_have_value("135")
            checks["nonzero_source_timestamps_bound_the_picker"] = True
            await context.close()
            assert not errors, errors
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    result = {"checks": checks, "page_errors": errors, "real_library_mutated": False}
    (args.output / "lap-boundaries-checks.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "lap-boundaries-review")
    asyncio.run(run(parser.parse_args()))
