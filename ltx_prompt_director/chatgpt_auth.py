"""ChatGPT-plan OAuth for local open-source Prompt Director installations.

Uses OpenAI's published dynamic-agent OIDC + PKCE flow, not Codex auth files,
ChatGPT private endpoints, or an OpenAI API key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import requests

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
USAGE_URL = "https://chatgpt.com/#settings/Usage"


class ChatGPTAuthError(RuntimeError):
    """An actionable, sanitized sign-in or account error."""


def _default_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return root / "PromptDirector" / "chatgpt-oauth.json"


def _save_private(path: Path, obj: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".chatgpt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


class ChatGPTAuth:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else _default_path()
        self._lock = threading.RLock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError) as error:
            raise ChatGPTAuthError("Cannot read saved ChatGPT sign-in state. Check your local profile.") from error
        if not isinstance(self.data, dict):
            raise ChatGPTAuthError("Invalid ChatGPT account storage.")
        self.data.setdefault("host_id", "urn:uuid:" + str(uuid.uuid4()))
        self.data.setdefault("profiles", {})
        self.data.setdefault("active", "")
        if not str(self.data["host_id"]).startswith("urn:uuid:"):
            raise ChatGPTAuthError("Invalid saved ChatGPT host identifier.")
        self._save()

    def _save(self) -> None:
        _save_private(self.path, self.data)

    def profiles(self) -> list[dict]:
        """Safe account descriptions only; never hand tokens to the UI."""
        with self._lock:
            return [
                {"client_id": cid, "email": record.get("email") or "ChatGPT account",
                 "connected": bool(record.get("refresh_token")),
                 "enabled": "chatgpt.tokens.use.direct" in record.get("scopes", [])}
                for cid, record in self.data["profiles"].items()
            ]

    def active_profile(self) -> dict | None:
        with self._lock:
            cid = self.data.get("active", "")
            return next((p for p in self.profiles() if p["client_id"] == cid), None)

    def select(self, client_id: str) -> None:
        with self._lock:
            if client_id not in self.data["profiles"]:
                raise ChatGPTAuthError("ChatGPT account not found.")
            self.data["active"] = client_id
            self._save()

    @staticmethod
    def _post_token(form: dict) -> dict:
        response = requests.post(TOKEN_URL, data=form, timeout=30)
        if not response.ok:
            raise ChatGPTAuthError(
                f"ChatGPT authorization failed (HTTP {response.status_code}). "
                "Please reconnect the account from Settings."
            )
        result = response.json()
        if not result.get("access_token"):
            raise ChatGPTAuthError("ChatGPT did not provide an access token.")
        return result

    @staticmethod
    def _verify_id_token(id_token: str, client_id: str, nonce: str) -> dict:
        try:
            discovery = requests.get(ISSUER + "/.well-known/openid-configuration", timeout=15)
            discovery.raise_for_status()
            config = discovery.json()
            if config.get("issuer") != ISSUER:
                raise ChatGPTAuthError("Unexpected ChatGPT identity issuer.")
            key = jwt.PyJWKClient(config["jwks_uri"]).get_signing_key_from_jwt(id_token)
            identity = jwt.decode(
                id_token, key.key, algorithms=["RS256"], audience=client_id,
                issuer=ISSUER, options={"require": ["exp", "iss", "aud", "sub"]},
            )
            if not secrets.compare_digest(str(identity.get("nonce", "")), nonce):
                raise ChatGPTAuthError("ChatGPT sign-in nonce verification failed.")
            return identity
        except ChatGPTAuthError:
            raise
        except Exception as error:
            raise ChatGPTAuthError("Unable to verify the ChatGPT account identity.") from error

    def sign_in(self, client_id: str | None = None, timeout: float = 180) -> dict:
        """Run interactive browser sign-in. Call on a worker thread, never on the Qt UI thread."""
        with self._lock:
            previous = dict(self.data["profiles"].get(client_id or "", {}))
            host_id = self.data["host_id"]
        if client_id and not previous:
            raise ChatGPTAuthError("Unknown saved ChatGPT account. Choose Add account.")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        callback: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Never log OAuth codes or tokens.

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path != "/auth/callback":
                    self.send_error(404)
                    return
                params = parse_qs(parsed.query)
                if not secrets.compare_digest(params.get("state", [""])[0], state):
                    self.send_error(400, "Invalid authorization state")
                    callback["error"] = "Invalid ChatGPT sign-in state."
                else:
                    callback.update({key: values[0] for key, values in params.items() if values})
                self.send_response(200 if "error" not in callback else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body><h3>Return to LTX Director</h3><p>Sign-in callback received. You may close this tab.</p></body></html>")

        server = HTTPServer(("127.0.0.1", 0), Handler)
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        params = {
            "client_id": client_id or "dynamic_agent_client",
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": redirect,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if not client_id:
            params["agent_name_hint"] = "LTX Director"
        else:
            if previous.get("id_token"):
                params["id_token_hint"] = previous["id_token"]
            if previous.get("email"):
                params["login_hint"] = previous["email"]
        try:
            if not webbrowser.open(AUTHORIZE_URL + "?" + urlencode(params)):
                raise ChatGPTAuthError("Could not open the web browser for ChatGPT sign-in.")
            deadline = time.monotonic() + timeout
            while not callback and time.monotonic() < deadline:
                server.timeout = min(3.0, max(0.1, deadline - time.monotonic()))
                server.handle_request()
        finally:
            server.server_close()
        if not callback:
            raise ChatGPTAuthError("ChatGPT sign-in timed out. Try connecting again.")
        if callback.get("error"):
            raise ChatGPTAuthError("ChatGPT sign-in was declined or cancelled.")
        code = callback.get("code")
        issued_id = callback.get("client_id", client_id)
        if not code or not issued_id or issued_id == "dynamic_agent_client":
            raise ChatGPTAuthError("ChatGPT sign-in did not return a valid authorization code and client ID.")
        if client_id and issued_id != client_id:
            raise ChatGPTAuthError("ChatGPT sign-in returned a different registered client.")
        token = self._post_token({
            "grant_type": "authorization_code",
            "client_id": issued_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect,
            "resource": RESOURCE,
        })
        id_token = token.get("id_token")
        if not id_token:
            raise ChatGPTAuthError("ChatGPT did not return an identity token.")
        identity = self._verify_id_token(id_token, issued_id, nonce)
        if previous and (previous.get("subject") != identity["sub"] or previous.get("issuer") != identity["iss"]):
            raise ChatGPTAuthError("ChatGPT account changed during authorization; saved credentials were not replaced.")
        scopes = str(token.get("scope") or "").split()
        if "chatgpt.tokens.use.direct" not in scopes:
            raise ChatGPTAuthError("ChatGPT plan usage was not authorized. Reconnect and grant plan usage.")
        record = {
            "client_id": issued_id, "issuer": identity["iss"], "subject": identity["sub"],
            "email": identity.get("email") or "", "name": identity.get("name") or "",
            "id_token": id_token, "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token") or previous.get("refresh_token", ""),
            "scopes": scopes, "saved_at": time.time(),
            "expires_in": int(token.get("expires_in", 3600)),
        }
        with self._lock:
            self.data["profiles"][issued_id] = record
            self.data["active"] = issued_id
            self._save()
        return self.active_profile() or {}

    def access_token(self) -> str:
        with self._lock:
            cid = self.data.get("active")
            profile = self.data["profiles"].get(cid, {})
            if not profile or "chatgpt.tokens.use.direct" not in profile.get("scopes", []):
                raise ChatGPTAuthError("Connect ChatGPT and enable plan usage in Application Settings.")
            if time.time() < float(profile.get("saved_at", 0)) + int(profile.get("expires_in", 0)) - 120:
                return profile["access_token"]
            if not profile.get("refresh_token"):
                raise ChatGPTAuthError("ChatGPT session expired. Reconnect in Application Settings.")
            token = self._post_token({
                "grant_type": "refresh_token", "client_id": cid,
                "refresh_token": profile["refresh_token"], "resource": RESOURCE,
            })
            profile.update({
                "access_token": token["access_token"],
                "refresh_token": token.get("refresh_token") or profile["refresh_token"],
                "scopes": str(token.get("scope") or " ".join(profile["scopes"])).split(),
                "saved_at": time.time(),
                "expires_in": int(token.get("expires_in", 3600)),
            })
            if token.get("id_token"):
                profile["id_token"] = token["id_token"]
            self._save()
            if "chatgpt.tokens.use.direct" not in profile["scopes"]:
                raise ChatGPTAuthError("ChatGPT plan permission was revoked. Reconnect in Settings.")
            return profile["access_token"]

    def list_models(self) -> list[tuple[str, str]]:
        response = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": "Bearer " + self.access_token()}, timeout=30,
        )
        response.raise_for_status()
        models = response.json().get("models", [])
        return [
            (entry["slug"], entry.get("display_name") or entry["slug"])
            for entry in models
            if isinstance(entry, dict) and entry.get("visibility") == "list" and entry.get("slug")
        ]

    def sign_out(self, client_id: str | None = None) -> bool:
        """Remove local tokens even if remote revocation fails. Return revocation status."""
        with self._lock:
            cid = client_id or self.data.get("active", "")
            profile = self.data["profiles"].get(cid)
            if not profile:
                return True
            token = profile.get("refresh_token")
        revoked = not token
        if token:
            try:
                discovery = requests.get(ISSUER + "/.well-known/openid-configuration", timeout=10)
                discovery.raise_for_status()
                endpoint = discovery.json()["revocation_endpoint"]
                response = requests.post(endpoint, data={
                    "token": token, "token_type_hint": "refresh_token", "client_id": cid,
                }, timeout=10)
                revoked = response.status_code == 200
            except (ValueError, KeyError, requests.RequestException):
                revoked = False
        with self._lock:
            profile = self.data["profiles"].get(cid)
            if profile:
                for key in ("access_token", "refresh_token", "id_token"):
                    profile.pop(key, None)
                self._save()
        return revoked
