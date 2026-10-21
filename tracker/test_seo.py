"""Public SEO: canonical host, robots, sitemap, structured data, noindex on private."""
import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from tracker.guides import GUIDES
from tracker.seo import public_paths


def jsonld(response):
    return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                                response.content.decode(), re.S).group(1))


@override_settings(SITE_URL="")
class PublicPagesTests(TestCase):
    def test_every_public_page_has_meta_valid_jsonld_and_no_brand_term(self):
        for path in public_paths():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("X-Robots-Tag", response)
                body = response.content.decode()
                self.assertIn('<link rel="canonical" href="http://testserver%s">' % path, body)
                self.assertIn('property="og:image"', body)
                self.assertIn('name="description"', body)
                self.assertNotIn("9Sig", body)
                self.assertEqual(body.count("<h1"), 1)
                types = {item["@type"] for item in jsonld(response)["@graph"]}
                self.assertTrue({"Organization", "WebSite"} <= types)

    def test_guides_carry_article_breadcrumb_and_visible_faq(self):
        for guide in GUIDES:
            response = self.client.get(reverse("guide", args=[guide["slug"]]))
            graph = jsonld(response)["@graph"]
            types = {item["@type"] for item in graph}
            self.assertTrue({"Article", "BreadcrumbList", "FAQPage"} <= types)
            faq = next(item for item in graph if item["@type"] == "FAQPage")
            # Structured FAQ must match what the page visibly shows.
            for question in faq["mainEntity"]:
                self.assertContains(response, question["name"].replace("'", "&#x27;"))
            self.assertLessEqual(len(guide["description"]), 160)
        self.assertEqual(self.client.get("/guides/no-such-guide/").status_code, 404)

    def test_robots_and_sitemap_list_only_public_pages(self):
        robots = self.client.get("/robots.txt").content.decode()
        for prefix in ("/s/", "/overview/", "/settings/", "/login/", "/django-admin/", "/compare.json"):
            self.assertIn("Disallow: %s" % prefix, robots)
        self.assertIn("Sitemap: http://testserver/sitemap.xml", robots)
        sitemap = self.client.get("/sitemap.xml").content.decode()
        locs = re.findall(r"<loc>(.*?)</loc>", sitemap)
        self.assertEqual(locs, ["http://testserver" + p for p in public_paths()])
        manifest = self.client.get("/site.webmanifest").json()
        self.assertEqual(manifest["name"], "Wealtharr")

    def test_private_pages_are_noindex(self):
        user = get_user_model().objects.create_user("seo-owner", password="a-long-test-password-1")
        self.assertEqual(self.client.get("/login/")["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(self.client.get("/compare.json")["X-Robots-Tag"], "noindex, nofollow")
        self.client.force_login(user)
        response = self.client.get(reverse("v2_overview"))
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")


@override_settings(SITE_URL="https://wealtharr.example", ALLOWED_HOSTS=["testserver", "wealtharr.example"])
class CanonicalHostTests(TestCase):
    def test_other_hosts_redirect_permanently_and_canonical_uses_site_url(self):
        response = self.client.get("/guides/?x=1")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://wealtharr.example/guides/?x=1")
        page = self.client.get("/guides/", HTTP_HOST="wealtharr.example")
        self.assertContains(page, '<link rel="canonical" href="https://wealtharr.example/guides/">')
        robots = self.client.get("/robots.txt", HTTP_HOST="wealtharr.example").content.decode()
        self.assertIn("Sitemap: https://wealtharr.example/sitemap.xml", robots)
        # Health checks are never redirected.
        self.assertEqual(self.client.get("/health/").status_code, 200)
