import unittest

from app import create_app


class InstallConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False


class InstallableAppTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app(InstallConfig).test_client()

    def test_manifest_describes_an_installable_app(self):
        response = self.client.get("/manifest.webmanifest")
        manifest = response.get_json()

        self.assertEqual(response.mimetype, "application/manifest+json")
        self.assertEqual(manifest["name"], "Duka Credit")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/dashboard")
        sizes = {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]}
        self.assertTrue({("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes)
        for icon in manifest["icons"]:
            self.assertEqual(self.client.get(icon["src"]).status_code, 200, icon["src"])

    def test_manifest_lets_the_app_tell_a_full_install_from_a_shortcut(self):
        manifest = self.client.get("/manifest.webmanifest").get_json()

        self.assertIn({"platform": "webapp", "url": "/manifest.webmanifest"}, manifest["related_applications"])

    def test_banner_steers_android_to_install_app_not_a_shortcut(self):
        page = self.client.get("/").get_data(as_text=True)
        script = self.client.get("/static/js/install-prompt.js").get_data(as_text=True)

        self.assertIn('data-install-variant="android"', page)
        self.assertIn("Install app", page)
        self.assertIn('data-install-variant="shortcut"', page)
        self.assertIn("getInstalledRelatedApps", script)
        self.assertIn('isIos ? "ios" : "android"', script)

    def test_service_worker_is_served_from_the_root_and_always_rechecked(self):
        response = self.client.get("/service-worker.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertIn(b'"/offline"', response.data)

    def test_service_worker_never_caches_app_pages(self):
        source = self.client.get("/service-worker.js").get_data(as_text=True)

        # Only the offline page is ever stored.
        self.assertEqual(source.count("cache.add("), 1)
        self.assertNotIn("cache.put(", source)

    def test_offline_page_needs_no_login(self):
        response = self.client.get("/offline")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"You're offline", response.data)

    def test_pages_link_the_manifest_icons_and_install_banner(self):
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn('rel="manifest" href="/manifest.webmanifest"', page)
        self.assertIn("apple-touch-icon.png", page)
        self.assertIn('id="install-banner"', page)
        self.assertIn("install-prompt.js", page)


if __name__ == "__main__":
    unittest.main()
