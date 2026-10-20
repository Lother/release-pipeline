"""Exchange a GitHub App private key for a short-lived installation token.

Why an App instead of a personal access token:
- Runs and commits show up as `<app>[bot]`, not as a person.
- Installation tokens expire after an hour; nothing long-lived sits in each repo.
- One App installed on the owner replaces one PAT per repository.

Configuration (environment):
    RP_APP_ID   numeric App ID
    RP_APP_KEY  either a path to the .pem file (local use) or the PEM content
                itself (CI secret). The two shapes are told apart by the
                leading "-----BEGIN", not by guessing whether it looks like a path.

Signing uses `cryptography` (RS256); everything else is the standard library.
"""
import base64
import calendar
import json
import os
import time

import github_api

TTL_MARGIN_S = 300
_cache = {}


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(app_id, key_pem, now=None):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(key_pem, password=None)
    now = int(now or time.time())
    # iat 60 s in the past absorbs clock skew; GitHub caps exp at 10 minutes.
    payload = {"iat": now - 60, "exp": now + 540, "iss": str(app_id)}
    signing_input = f"{_b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())}.{_b64(json.dumps(payload).encode())}"
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


def load_key_pem(value):
    """Private key bytes from either PEM content or a file path; None if unusable."""
    if value.startswith("-----BEGIN"):
        return value.encode("utf-8")
    if not os.path.isfile(value):
        print(f"[app_token] key file not found: {value}")
        return None
    with open(value, "rb") as fh:
        return fh.read()


def installation_token(owner):
    """Installation token for the App's installation on `owner`, or None.

    Every None is announced: falling back to a personal identity must be visible.
    """
    app_id = os.environ.get("RP_APP_ID", "").strip()
    key_value = os.environ.get("RP_APP_KEY", "").strip()
    if not app_id or not key_value:
        print("[app_token] WARNING RP_APP_ID / RP_APP_KEY not set; falling back to GITHUB_TOKEN")
        return None
    cached = _cache.get(owner)
    if cached and cached[1] - TTL_MARGIN_S > time.time():
        return cached[0]
    key_pem = load_key_pem(key_value)
    if key_pem is None:
        print("[app_token] WARNING no usable App key; falling back to GITHUB_TOKEN")
        return None
    try:
        jwt = github_api.GitHub(make_jwt(app_id, key_pem))
        try:
            inst = jwt.get(f"/orgs/{owner}/installation")
        except github_api.GitHubError as ex:
            if ex.status != 404:
                raise
            inst = jwt.get(f"/users/{owner}/installation")
        created = jwt.post(f"/app/installations/{inst['id']}/access_tokens")
        # expires_at is UTC; calendar.timegm (not time.mktime) keeps it UTC.
        expires = calendar.timegm(time.strptime(created["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
        _cache[owner] = (created["token"], expires)
        return created["token"]
    except Exception as ex:  # visible fallback, never a silent one
        print(f"[app_token] WARNING could not obtain installation token: {ex}")
        return None
