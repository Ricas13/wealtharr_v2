"""Chart crosshair, keyboard reading and drag-to-zoom on a synthetic chart."""
import json
from pathlib import Path

from django.conf import settings
from django.test import override_settings

from browser_tests.harness import BrowserHarness

MARKUP = """
<div id="probe-test" class="w2-chart" data-chart-probe tabindex="0" data-readout='%s' style="width:800px;margin:40px">
  <svg viewBox="0 0 800 200" preserveAspectRatio="none" style="height:190px;width:800px"><polyline points="0,100 800,50"></polyline></svg>
  <div class="w2-probe" hidden><span class="w2-probe-line"></span><div class="w2-probe-tip"></div></div>
  <div class="w2-zoom-box" hidden></div>
  <button type="button" class="w2-linklike w2-zoom-reset" hidden>Reset zoom</button>
  <p class="w2-sr" data-probe-live></p>
</div>"""


@override_settings(TURNSTILE_REQUIRED=False)
class ChartProbeBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        self.new_page(1100)
        self.sign_in()
        self.visit("v2_settings")
        samples = [{"x": i * 100, "d": "2026-01-%02d" % (i + 1), "t": ["Value £%d" % (100 + i * 10)]}
                   for i in range(9)]
        self.page.evaluate("html => document.querySelector('#v2-main').insertAdjacentHTML('afterbegin', html)",
                           MARKUP % json.dumps(samples))
        self.page.add_script_tag(path=str(Path(settings.BASE_DIR) / "static/js/chart_probe.js"))

    def test_hover_shows_the_nearest_reading(self):
        box = self.page.locator("#probe-test")
        rect = box.bounding_box()
        self.page.mouse.move(rect["x"] + 410, rect["y"] + 60)
        tip = self.page.locator("#probe-test .w2-probe-tip")
        tip.wait_for(state="visible")
        self.assertIn("2026-01-05", tip.inner_text())
        self.assertIn("£140", tip.inner_text())

    def test_arrow_keys_read_values_and_announce_them(self):
        self.page.locator("#probe-test").focus()
        self.page.keyboard.press("ArrowRight")
        self.page.keyboard.press("ArrowLeft")
        live = self.page.locator("#probe-test [data-probe-live]").inner_text()
        self.assertTrue(live.startswith("2026-01-"), live)

    def test_drag_zooms_in_and_reset_restores(self):
        box = self.page.locator("#probe-test")
        rect = box.bounding_box()
        self.page.mouse.move(rect["x"] + 200, rect["y"] + 60)
        self.page.mouse.down()
        self.page.mouse.move(rect["x"] + 400, rect["y"] + 60, steps=5)
        self.page.mouse.up()
        view = self.page.locator("#probe-test svg").get_attribute("viewBox")
        _, w = float(view.split()[0]), float(view.split()[2])
        self.assertLess(w, 400)
        reset = self.page.locator("#probe-test .w2-zoom-reset")
        reset.wait_for(state="visible")
        reset.click()
        self.assertEqual(self.page.locator("#probe-test svg").get_attribute("viewBox"), "0 0 800 200")
