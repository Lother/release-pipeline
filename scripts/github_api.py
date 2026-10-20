"""Minimal GitHub REST client on top of urllib (no third-party dependencies)."""
import json
import os
import urllib.error
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


class GitHubError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class GitHub:
    def __init__(self, token, user_agent="release-pipeline"):
        if not token:
            raise ValueError("GitHub token is empty")
        self.token = token
        self.user_agent = user_agent

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            API + path, data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": self.user_agent,
                **({"Content-Type": "application/json"} if data else {}),
            })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as ex:
            detail = " ".join(ex.read().decode(errors="replace").split())[:200]
            raise GitHubError(ex.code, detail) from None

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None):
        return self.request("POST", path, body)

    def latest_release(self, repo):
        """Latest published release of owner/name, or None if the repo has none yet."""
        try:
            return self.get(f"/repos/{repo}/releases/latest")
        except GitHubError as ex:
            if ex.status == 404:
                return None
            raise

    def dispatch_workflow(self, repo, workflow, ref, inputs=None):
        self.post(f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
                  {"ref": ref, "inputs": inputs or {}})


def resolve_token(owner=None):
    """GitHub App installation token when configured, else GITHUB_TOKEN / GH_TOKEN.

    Falling back to a personal identity is always printed, never silent.
    """
    if owner:
        import app_token
        token = app_token.installation_token(owner)
        if token:
            return token
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("no GitHub credentials: set RP_APP_ID/RP_APP_KEY or GITHUB_TOKEN")
    return token
