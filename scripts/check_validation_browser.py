"""Read-only validation coverage UI tests using synthetic corpus records."""

import argparse
import asyncio
import functools
import json
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from check_delete_swap_browser import Fixtures
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler
from telemetry.profiles import default_profile
from telemetry.validation import coverage_report


class CoverageFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        with tempfile.TemporaryDirectory() as folder:
            self.coverage = coverage_report(Path(folder))
        pair = next(pair for pair in self.coverage["pairs"] if pair["car_id"] == "mclaren_720s_gt3_evo" and pair["track_id"] == "silverstone")
        pair.update(candidates=5, smoke_checked=1, analyzed=2, reviewed=1, errors=1, state="partially_reviewed")
        pair["videos"] = [{"id": str(index), "title": f"Fixture video {index} / {state}",
            "url": "https://www.youtube.com/watch?v=c0SJgXUahEM", "state": state,
            "identity_basis": "unverified_title", "uploader": "Fixture uploader",
            "coverage": {"speed": 91.5, "brake": 88}, "error": "Fixture download failure" if state == "error" else None}
            for index, state in enumerate(("candidate", "smoke_checked", "analyzed", "reviewed", "error"))]
        pair["videos"][0].update(title='<script>alert("untrusted title")</script>', url="javascript:alert(1)")
        self.coverage.update(total_candidates=5, pairs_with_candidates=1)
        self.calls, self.fail = 0, False

    async def route(self, route):
        path = urlparse(route.request.url).path
        if path == "/api/validation":
            assert route.request.method == "GET", "Coverage must remain read-only."
            self.calls += 1
            if self.fail:
                await route.fulfill(status=503, json={"detail": "Fixture corpus temporarily unavailable"})
            else:
                await route.fulfill(json=self.coverage)
        elif path == "/api/profile":
            await route.fulfill(json=default_profile())
        else:
            await super().route(route)


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(StaticHandler, directory=str(ROOT / "web")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checks, errors = {}, []
    fixture = CoverageFixtures()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(viewport={"width": 1440, "height": 1050})
            await context.route("**/api/**", fixture.route)
            await context.add_init_script(SOCKET_FIXTURE)
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(f"http://127.0.0.1:{server.server_port}")
            await expect(page.locator("#compatibilityCoverage")).to_be_hidden()
            assert fixture.calls == 0
            await page.get_by_role("button", name="Calibration", exact=True).click()
            await expect(page.locator("#validationRows > tr")).to_have_count(50)
            await expect(page.locator("#validationPageCount")).to_contain_text("of 775 pairs")
            await expect(page.locator("#validationScope")).to_contain_text("not accuracy validation")
            await expect(page.locator("#validationCounts")).to_contain_text("0pairs at review target")
            checks["lazy_read_only_report_exposes_all_775_pairs_and_zero_universal_pass"] = True

            await page.locator("#validationNext").click()
            await expect(page.locator("#validationPageCount")).to_contain_text("51-100")
            await page.locator("#validationPrevious").click()
            await expect(page.locator("#validationPageCount")).to_contain_text("1-50")
            await page.locator("#validationState").select_option("not_tested")
            await expect(page.locator("#validationPageCount")).to_contain_text("of 774 pairs")
            assert await page.locator("#validationRows .coverage-state.not_tested").count() == 50
            checks["pagination_and_explicit_untested_filter"] = True

            await page.locator("#validationState").select_option("")
            await page.locator("#validationCar").select_option("mclaren_720s_gt3_evo")
            await page.locator("#validationTrack").select_option("silverstone")
            await expect(page.locator("#validationRows > tr")).to_have_count(1)
            await expect(page.locator("#validationRows")).to_contain_text("Partially reviewed")
            await page.locator("#validationRows .coverage-expand").click()
            await expect(page.locator(".coverage-detail li")).to_have_count(5)
            await expect(page.locator(".coverage-detail")).to_contain_text("unverified title hints")
            await expect(page.locator(".coverage-detail")).to_contain_text("Analyzed / not reviewed")
            await expect(page.locator(".coverage-detail")).to_contain_text("Fixture download failure")
            assert await page.locator(".coverage-detail a").count() == 4
            assert await page.locator(".coverage-detail script").count() == 0
            await expect(page.locator(".coverage-detail")).to_contain_text('<script>alert("untrusted title")</script>')
            checks["filters_expand_evidence_distinguish_validation_states_and_escape_titles"] = True
            await page.locator("#compatibilityCoverage").screenshot(path=str(args.output / "validation-desktop.png"))

            fixture.fail = True
            await page.locator("#refreshValidation").click()
            await expect(page.locator("#validationStatus")).to_contain_text("Showing previous report")
            await expect(page.locator(".coverage-detail li")).to_have_count(5)
            await expect(page.locator("#refreshValidation")).to_be_enabled()
            fixture.fail = False
            await page.locator("#refreshValidation").click()
            await expect(page.locator("#validationStatus")).to_contain_text("Read-only coverage")
            await expect(page.locator("#validationCar")).to_have_value("mclaren_720s_gt3_evo")
            checks["refresh_failure_keeps_prior_report_and_filter_selections"] = True

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.locator("#compatibilityCoverage").scroll_into_view_if_needed()
            assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            await page.locator("#compatibilityCoverage").screenshot(path=str(args.output / "validation-mobile.png"))
            await page.locator("#validationState").select_option("review_target_met")
            await expect(page.locator(".coverage-no-results")).to_have_text("No car / circuit pairs match these filters.")
            await expect(page.locator("#validationPrevious")).to_be_disabled()
            await expect(page.locator("#validationNext")).to_be_disabled()
            checks["mobile_width_constrained_and_empty_filter_is_explicit"] = True
            pair = next(pair for pair in fixture.coverage["pairs"] if pair["candidates"])
            for state, label in (("stale_analysis", "Needs reanalysis"), ("channel_review_only", "Some channels reviewed")):
                pair["state"] = state
                pair["videos"][1]["state"] = state
                await page.locator("#refreshValidation").click()
                await expect(page.locator("#refreshValidation")).to_be_enabled()
                await page.locator("#validationState").select_option(state)
                await expect(page.locator("#validationRows .coverage-state")).to_have_text(label)
                await expect(page.locator(".coverage-detail")).to_contain_text(label)
            checks["stale_and_partial_channel_reviews_are_not_labeled_untested"] = True
            assert not errors, errors
            assert not fixture.unexpected, fixture.unexpected
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    result = {"checks": checks, "page_errors": errors, "real_library_mutated": False}
    (args.output / "validation-browser-checks.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "validation-review")
    asyncio.run(run(parser.parse_args()))
