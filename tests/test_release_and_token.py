import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import app_token  # noqa: E402
import release  # noqa: E402


class VersionFloorTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(release.parse_version("v1.12.3"), (1, 12, 3))
        with self.assertRaises(ValueError):
            release.parse_version("1.2.3")

    def test_floor_compares_numerically(self):
        release.check_floor("v1.10.0", ["v1.9.9", "v1.2.0", "nightly"])
        with self.assertRaises(ValueError):
            release.check_floor("v1.9.0", ["v1.10.0"])

    def test_same_version_is_rejected(self):
        with self.assertRaises(ValueError):
            release.check_floor("v2.0.0", ["v2.0.0"])


class AppKeyShapeTests(unittest.TestCase):
    def test_pem_content_is_used_as_is(self):
        pem = "-----BEGIN PRIVATE KEY-----\nZmFrZQ==\n-----END PRIVATE KEY-----\n"
        self.assertEqual(app_token.load_key_pem(pem), pem.encode())

    def test_path_is_read(self):
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"path-shape-bytes")
        self.addCleanup(os.remove, fh.name)
        self.assertEqual(app_token.load_key_pem(fh.name), b"path-shape-bytes")

    def test_missing_path_returns_none(self):
        with redirect_stdout(StringIO()):
            self.assertIsNone(app_token.load_key_pem("/nonexistent/key.pem"))

    def test_unconfigured_fallback_is_announced(self):
        out = StringIO()
        with mock.patch.dict(os.environ, {"RP_APP_ID": "", "RP_APP_KEY": ""}), redirect_stdout(out):
            self.assertIsNone(app_token.installation_token("someone"))
        self.assertIn("WARNING", out.getvalue())


if __name__ == "__main__":
    unittest.main()
