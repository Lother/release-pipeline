#!/usr/bin/env python3
"""Collect the latest release of every configured repo into one JSON feed.

Runs on a schedule (see .github/workflows/sync-feed.yml). Each source repo builds
and publishes its own release; this script only notices new releases and records
them, so publishing never waits on CI.

Failure grading. One repo's transient error must not block everyone else, but no
failure may disappear silently:

  [warn] fetch          fetching one repo failed and the feed already has an
                        entry for it: keep the old entry, retry next run.
  [FAIL] no-feed-entry  fetching failed and there is no old entry to keep:
                        a new repo's first release must not be skipped silently.
  [FAIL] all-failed     every repo failed: a platform problem, writing is pointless.
  [FAIL] persist        the feed file could not be written.
  [FAIL] unexpected     the grading itself hit a state it does not handle.

Exit code: 0 (success, possibly with warnings) or 2 (hard failure, feed untouched).
Result lines start with [warn] / [FAIL]; the recap at the end uses a [summary]
prefix so a line-start grep in the workflow never double-counts it.
"""
import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import github_api  # noqa: E402

EXIT_OK, EXIT_HARD_FAIL = 0, 2


def flatten(text, limit=400):
    """One line per result: multi-line stderr would break the line-start contract."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + " ...(truncated)"


def gha_escape(text):
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


class Report:
    """Per-repo results of one run. Thread-safe."""

    def __init__(self, targets):
        self.targets = targets
        self.ok, self.warns, self.fatals = [], [], []
        self._lock = threading.Lock()

    def add_ok(self, name):
        with self._lock:
            self.ok.append(name)

    def warn(self, kind, name, reason):
        with self._lock:
            self.warns.append((kind, name, flatten(reason)))
        print(f"[warn] {kind} {name}: {flatten(reason)}")

    def fatal(self, kind, name, reason):
        with self._lock:
            self.fatals.append((kind, name, flatten(reason)))
        print(f"[FAIL] {kind} {name}: {flatten(reason)}")

    @property
    def failed_repos(self):
        with self._lock:
            return ({n for k, n, _ in self.warns if k == "fetch"}
                    | {n for k, n, _ in self.fatals if k == "no-feed-entry"})

    @property
    def hard_fail(self):
        with self._lock:
            return bool(self.fatals)

    def print_tail(self):
        print("\n=== sync result ===")
        print(f"[summary] targets={self.targets} ok={len(self.ok)} warn={len(self.warns)} fatal={len(self.fatals)}")
        for kind, name, reason in self.warns:
            print(f"[summary][warn] {kind} {name}: {reason}")
        for kind, name, reason in self.fatals:
            print(f"[summary][FAIL] {kind} {name}: {reason}")

    @staticmethod
    def _table(rows):
        out = ["| repo | kind | reason |", "| --- | --- | --- |"]
        for kind, name, reason in rows:
            out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in (name, kind, reason)) + " |")
        return out

    def emit_github(self):
        """Run summary first block + one annotation per warning (no-op outside Actions)."""
        if os.environ.get("GITHUB_ACTIONS"):
            for kind, name, reason in self.warns:
                print(f"::warning title=sync {kind} {name}::{gha_escape(reason)}")
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not path:
            return
        if self.fatals:
            lines = [f"## Hard failure ({len(self.fatals)}): feed NOT updated", ""] + self._table(self.fatals)
            if self.warns:
                lines += ["", f"Plus {len(self.warns)} warning(s):", ""] + self._table(self.warns)
        elif self.warns:
            lines = [f"## {len(self.warns)} warning(s): not all green", "",
                     f"{len(self.ok)} repo(s) updated normally. `fetch` warnings keep the previous feed entry "
                     "and retry on the next run.", ""] + self._table(self.warns)
        else:
            lines = [f"## All green ({self.targets} repos, 0 warnings)"]
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def entry_from_release(name, repo, release):
    assets = release.get("assets") or []
    return {
        "name": name,
        "repo": repo,
        "version": release["tag_name"].lstrip("v"),
        "published_at": release.get("published_at"),
        "download_url": assets[0]["browser_download_url"] if assets else release.get("zipball_url"),
        "changelog": (release.get("body") or "").strip()[:2000],
    }


def load_feed(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_feed(path, entries):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)  # atomic: readers never see half a feed


def sync(config, fetch_latest, report, workers=8):
    """Build the new feed. Returns the entry list, or None on a hard failure."""
    old = {e["name"]: e for e in load_feed(config["feed"])}
    repos = config["repos"]
    results = {}

    def one(item):
        name, repo = item["name"], item["repo"]
        try:
            release = fetch_latest(repo)
        except Exception as ex:
            if name in old:
                report.warn("fetch", name, ex)
                results[name] = old[name]
            else:
                report.fatal("no-feed-entry", name, ex)
            return
        if release is None:  # no release yet: not an error
            if name in old:
                results[name] = old[name]
            report.add_ok(name)
            return
        results[name] = entry_from_release(name, repo, release)
        report.add_ok(name)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, repos))

    if repos and len(report.failed_repos) == len(repos):
        report.fatal("all-failed", "*", f"all {len(repos)} repos failed; treating as a platform problem")
    accounted = set(report.ok) | report.failed_repos
    missing = [r["name"] for r in repos if r["name"] not in accounted]
    if missing:
        report.fatal("unexpected", ",".join(missing), "repo finished without being graded")
    if report.hard_fail:
        return None
    return [results[r["name"]] for r in repos if r["name"] in results]


def main(argv=None, fetch_latest=None):
    ap = argparse.ArgumentParser(description="Sync latest releases into a JSON feed.")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args(argv)

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    report = Report(len(config["repos"]))
    try:
        if fetch_latest is None:
            gh = github_api.GitHub(github_api.resolve_token(config.get("app_owner")))
            fetch_latest = gh.latest_release
        entries = sync(config, fetch_latest, report)
        if entries is not None:
            try:
                write_feed(config["feed"], entries)
            except OSError as ex:
                report.fatal("persist", config["feed"], ex)
    except Exception as ex:
        report.fatal("unexpected", "*", ex)
    report.print_tail()
    report.emit_github()
    return EXIT_HARD_FAIL if report.hard_fail else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
