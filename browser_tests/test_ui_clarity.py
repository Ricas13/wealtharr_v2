"""Touch targets, labelled fill fields, no clipped review form, and sent-form feedback on a phone."""
from browser_tests.test_v2_phase4 import Phase4TodayBrowserTests as Fixture


class UiClarityTests(Fixture):
    def open_review(self, width):
        self.new_page(width)
        self.sign_in()
        self.visit("v2_guided_review", self.strategy.pk, self.signal.pk)
        self.page.locator(".w2-fill-form").first.wait_for()

    def test_review_form_fits_a_phone_and_every_field_is_labelled(self):
        self.open_review(390)
        boxes = self.page.evaluate("""() => [...document.querySelectorAll('.w2-fill-form input:not([type=hidden]), .w2-fill-submit')]
            .map(el => { const r = el.getBoundingClientRect(); return {name: el.name || el.className, left: r.left, right: r.right, height: r.height}; })""")
        self.assertGreaterEqual(len(boxes), 5)
        for box in boxes:
            self.assertGreaterEqual(box["left"], 0, box)
            self.assertLessEqual(box["right"], 390, box)
        for box in boxes:
            if "confirmation" not in box["name"]:
                self.assertGreaterEqual(box["height"], 43.5, box)
        for label in ("Date done", "Shares got", "Fee"):
            self.assertTrue(self.page.locator(".w2-fill-field label", has_text=label).first.is_visible(), label)
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 391)
        self.assertEqual(self.browser_errors, [])

    def test_main_buttons_are_tap_sized_on_a_phone(self):
        self.new_page(390)
        self.sign_in()
        self.visit("v2_instance_page", self.strategy.pk, "today")
        for name in ("I did this", "How it was calculated"):
            height = self.page.get_by_role("link", name=name, exact=True).bounding_box()["height"]
            self.assertGreaterEqual(height, 43.5, name)

    def test_status_chips_are_not_styled_like_buttons(self):
        self.new_page(390)
        self.sign_in()
        self.visit("v2_instance_page", self.strategy.pk, "today")
        chip = self.page.locator(".w2-chip").first
        self.assertEqual(chip.evaluate("el => getComputedStyle(el).cursor"), "default")
        self.assertEqual(chip.evaluate("el => getComputedStyle(el).borderTopColor"), "rgba(0, 0, 0, 0)")

    def test_a_sent_form_shows_busy_and_cannot_be_sent_twice(self):
        self.open_review(390)
        # Nothing is recorded: the request is dropped before it reaches the server.
        self.page.route("**/*", lambda route: route.abort() if route.request.method == "POST" else route.continue_())
        with self.page.expect_event("requestfailed"):
            state = self.page.evaluate("""() => {
                const form = document.querySelector('.w2-fill-form');
                form.querySelector('[name=executed_on]').value = new Date().toISOString().slice(0, 10);
                form.querySelector('[name=unit_price]').value = '100';
                form.querySelector('[name=actual_confirmation]').checked = true;
                form.requestSubmit(form.querySelector('.w2-fill-submit'));
                const second = new Event('submit', {cancelable: true, bubbles: true});
                form.dispatchEvent(second);
                return [form.querySelector('.w2-fill-submit').classList.contains('is-busy'), second.defaultPrevented];
            }""")
        self.assertEqual(state, [True, True])
        self.page.wait_for_load_state("networkidle")


# The inherited fixtures are reused; their own tests already run in test_v2_phase4.
for _name in [n for n in dir(Fixture) if n.startswith("test_")]:
    setattr(UiClarityTests, _name, None) if _name not in UiClarityTests.__dict__ else None
del Fixture  # not collected a second time from this module
