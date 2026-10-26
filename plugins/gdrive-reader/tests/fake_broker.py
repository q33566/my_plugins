"""Fake for the backend Worker's /token and /refresh endpoints (Google behind it)."""
import base64
import hashlib
import json

import httpx


class FakeBroker:
    def __init__(self):
        self.codes = {}  # code -> code_challenge the auth request carried
        self.refresh_tokens = {"rt-1"}
        self.issued = 0
        self.calls = []
        self.refresh_returns_new_refresh_token = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.calls.append((request.url.path, body))
        if request.url.path == "/token":
            challenge = self.codes.pop(body.get("code"), None)
            verifier = body.get("code_verifier", "")
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if challenge is None or challenge != expected:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code or verifier"})
            self.issued += 1
            return httpx.Response(200, json={"access_token": f"at-{self.issued}", "expires_in": 3599,
                                             "refresh_token": "rt-1", "scope": "https://www.googleapis.com/auth/drive.readonly",
                                             "token_type": "Bearer"})
        if request.url.path == "/refresh":
            if body.get("refresh_token") not in self.refresh_tokens:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            self.issued += 1
            out = {"access_token": f"at-{self.issued}", "expires_in": 3599, "token_type": "Bearer"}
            if self.refresh_returns_new_refresh_token:
                out["refresh_token"] = "rt-2"
                self.refresh_tokens.add("rt-2")
            return httpx.Response(200, json=out)
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self.handler)
