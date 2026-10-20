import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import sync_feed as sf  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def release(tag, url="https://example.com/a.zip"):
    return {"tag_name": tag, "published_at": "2026-10-01T00:00:00Z", "body": "notes",
            "assets": [{"browser_download_url": url}]}


class SyncFeedTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.feed = os.path.join(self.dir.name, "feed.json")
        self.cfg = os.path.join(self.dir.name, "config.json")
        self.summary = os.path.join(self.dir.name, "summary.md")
        os.environ["GITHUB_STEP_SUMMARY"] = self.summary
        self.addCleanup(os.environ.pop, "GITHUB_STEP_SUMMARY", None)
        with open(self.cfg, "w") as f:
            json.dump({"feed": self.feed, "repos": [
                {"name": "alpha", "repo": "o/alpha"},
                {"name": "beta", "repo": "o/beta"},
                {"name": "gamma", "repo": "o/gamma"},
            ]}, f)

    def seed(self, names):
        sf.write_feed(self.feed, [{"name": n, "repo": f"o/{n}", "version": "0.9.0"} for n in names])

    def run_sync(self, responses):
        def fetch(repo):
            r = responses[repo]
            if isinstance(r, Exception):
                raise r
            return r
        out = StringIO()
        with redirect_stdout(out):
            rc = sf.main(["--config", self.cfg], fetch_latest=fetch)
        return rc, out.getvalue()

    def feed_versions(self):
        return {e["name"]: e["version"] for e in sf.load_feed(self.feed)}

    def test_all_green(self):
        rc, out = self.run_sync({"o/alpha": release("v1.0.0"), "o/beta": release("v2.0.0"), "o/gamma": None})
        self.assertEqual(rc, sf.EXIT_OK)
        self.assertEqual(self.feed_versions(), {"alpha": "1.0.0", "beta": "2.0.0"})
        self.assertNotIn("[warn]", out)
        self.assertIn("All green", read(self.summary))

    def test_one_transient_failure_keeps_old_entry(self):
        self.seed(["alpha", "beta", "gamma"])
        rc, out = self.run_sync({"o/alpha": release("v1.0.0"), "o/beta": RuntimeError("HTTP 502\nbad gateway"),
                                 "o/gamma": release("v3.0.0")})
        self.assertEqual(rc, sf.EXIT_OK)
        self.assertEqual(self.feed_versions(), {"alpha": "1.0.0", "beta": "0.9.0", "gamma": "3.0.0"})
        self.assertIn("[warn] fetch beta: HTTP 502 bad gateway", out)  # flattened to one line
        self.assertIn("warning(s): not all green", read(self.summary))

    def test_new_repo_failure_is_hard_and_feed_untouched(self):
        self.seed(["alpha", "beta"])
        before = read(self.feed)
        rc, out = self.run_sync({"o/alpha": release("v1.0.0"), "o/beta": release("v2.0.0"),
                                 "o/gamma": RuntimeError("timeout")})
        self.assertEqual(rc, sf.EXIT_HARD_FAIL)
        self.assertIn("[FAIL] no-feed-entry gamma", out)
        self.assertEqual(read(self.feed), before)

    def test_all_failed_is_platform_problem(self):
        self.seed(["alpha", "beta", "gamma"])
        rc, out = self.run_sync({r: RuntimeError("HTTP 503") for r in ("o/alpha", "o/beta", "o/gamma")})
        self.assertEqual(rc, sf.EXIT_HARD_FAIL)
        self.assertIn("[FAIL] all-failed", out)

    def test_persist_failure_is_hard(self):
        os.makedirs(self.feed)  # a directory where the file should be
        rc, out = self.run_sync({"o/alpha": release("v1.0.0"), "o/beta": None, "o/gamma": None})
        self.assertEqual(rc, sf.EXIT_HARD_FAIL)
        self.assertTrue("[FAIL] persist" in out or "[FAIL] unexpected" in out)

    def test_summary_lines_never_start_with_result_markers(self):
        self.seed(["beta"])
        _, out = self.run_sync({"o/alpha": release("v1.0.0"), "o/beta": RuntimeError("x"), "o/gamma": None})
        tail = out.split("=== sync result ===", 1)[1]
        for line in tail.splitlines():
            self.assertFalse(line.startswith("[warn]") or line.startswith("[FAIL]"), line)


if __name__ == "__main__":
    unittest.main()
