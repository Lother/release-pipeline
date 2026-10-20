#!/usr/bin/env python3
"""Publish a release: check, push the tag, trigger the repo's release workflow, return.

This script deliberately does not wait for CI. The release workflow builds and
publishes the release; the scheduled sync workflow picks it up into the feed.
Waiting here used to cost 60-190 s per repo, about 30 minutes for a batch, all of
it spent idle.

Checks before anything leaves the machine:
- the tag follows vMAJOR.MINOR.PATCH and is greater than every existing tag (floor);
- optionally, leak-guard scans the objects this push would send (fail-closed).

Usage:
    release.py --repo-path ../my-tool --repo owner/my-tool --tag v1.4.0 [--leak-guard]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import github_api  # noqa: E402

VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag):
    m = VERSION_RE.match(tag)
    if not m:
        raise ValueError(f"tag {tag!r} does not match vMAJOR.MINOR.PATCH")
    return tuple(int(x) for x in m.groups())


def check_floor(tag, existing_tags):
    """The new tag must be greater than every existing well-formed tag."""
    new = parse_version(tag)
    versions = [parse_version(t) for t in existing_tags if VERSION_RE.match(t)]
    if versions and new <= max(versions):
        top = "v" + ".".join(map(str, max(versions)))
        raise ValueError(f"{tag} is not greater than the latest tag {top}")


def git(repo_path, *args):
    r = subprocess.run(["git", "-C", repo_path, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def leak_guard(repo_path, ref, remote):
    exe = shutil.which("leak-guard")
    if exe is None:
        raise RuntimeError("leak-guard not installed; refusing to push (fail-closed)")
    r = subprocess.run([exe, "git", ref, "--repo", repo_path, "--remote", remote])
    if r.returncode != 0:
        raise RuntimeError(f"leak-guard did not pass (rc={r.returncode}); tag not pushed")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--repo", required=True, help="owner/name on GitHub")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--workflow", default="release.yml")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--leak-guard", action="store_true", help="scan outgoing objects before pushing")
    args = ap.parse_args(argv)

    try:
        git(args.repo_path, "fetch", "--tags", args.remote)
        check_floor(args.tag, git(args.repo_path, "tag", "--list").splitlines())
        git(args.repo_path, "tag", "-a", args.tag, "-m", f"Release {args.tag}")
        try:
            if args.leak_guard:
                leak_guard(args.repo_path, args.tag, args.remote)
            git(args.repo_path, "push", args.remote, f"refs/tags/{args.tag}")
        except Exception:
            git(args.repo_path, "tag", "-d", args.tag)  # leave no local tag behind on refusal
            raise
        owner = args.repo.split("/", 1)[0]
        gh = github_api.GitHub(github_api.resolve_token(owner))
        gh.dispatch_workflow(args.repo, args.workflow, args.tag, {"tag": args.tag})
    except (ValueError, RuntimeError, github_api.GitHubError) as ex:
        print(f"[release] refused: {ex}", file=sys.stderr)
        return 1
    print(f"[release] {args.repo} {args.tag}: tag pushed, {args.workflow} dispatched. "
          "The scheduled sync will add it to the feed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
