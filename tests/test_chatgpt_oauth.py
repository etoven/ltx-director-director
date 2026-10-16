"""Offline regression coverage for ChatGPT plan authorization and Responses transport."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from ltx_prompt_director.chatgpt_auth import ChatGPTAuth, ChatGPTAuthError
from ltx_prompt_director import ai


class ChatGPTAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = ChatGPTAuth(Path(self.tmp.name) / "auth.json")

    def test_initial_signin_uses_pkce_stable_host_and_issued_client_id(self):
        browser_calls = []
        sent_forms = []

        def browser(url):
            url_parts = urlsplit(url)
            query = parse_qs(url_parts.query)
            browser_calls.append(query)
            self.assertEqual(query["client_id"], ["dynamic_agent_client"])
            self.assertEqual(query["agent_name_hint"], ["LTX Director"])
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertEqual(query["resource"], ["https://api.openai.com/v1"])
            self.assertIn("chatgpt.tokens.use.direct", query["scope"][0])
            self.assertEqual(query["ext_agent_host_id"], [self.auth.data["host_id"]])
            callback = query["redirect_uri"][0] + "?" + urlencode({
                "state": query["state"][0], "code": "example-auth-code",
                "client_id": "oaiapp_unit_test",
            })

            def visit():
                with urlopen(callback, timeout=5) as response:
                    self.assertEqual(response.status, 200)
            threading.Thread(target=visit, daemon=True).start()
            return True

        def post(form):
            sent_forms.append(form)
            return {
                "id_token": "signed-token",
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
                "expires_in": 3600,
            }

        with patch("ltx_prompt_director.chatgpt_auth.webbrowser.open", side_effect=browser), \
             patch.object(ChatGPTAuth, "_post_token", side_effect=post), \
             patch.object(ChatGPTAuth, "_verify_id_token",
                          return_value={"sub": "test-subject", "iss": "https://auth.openai.com",
                                        "email": "user@example.com"}):
            profile = self.auth.sign_in(timeout=8)

        self.assertEqual(profile["client_id"], "oaiapp_unit_test")
        self.assertEqual(profile["email"], "user@example.com")
        self.assertEqual(sent_forms[0]["client_id"], "oaiapp_unit_test")
        self.assertEqual(sent_forms[0]["code"], "example-auth-code")
        self.assertEqual(sent_forms[0]["redirect_uri"],
                         browser_calls[0]["redirect_uri"][0])
        self.assertEqual(self.auth.access_token(), "access-token")
        self.assertEqual(ChatGPTAuth(self.auth.path).data["host_id"], self.auth.data["host_id"])

    def test_refresh_rotates_tokens_and_discovers_latest_models(self):
        with self.auth._lock:
            self.auth.data["profiles"]["oaiapp_test"] = {
                "client_id": "oaiapp_test", "email": "me@test.test",
                "subject": "sub1", "issuer": "https://auth.openai.com",
                "access_token": "expired", "refresh_token": "old-refresh",
                "saved_at": 0, "expires_in": 60,
                "scopes": ["chatgpt.tokens.use.direct"],
            }
            self.auth.data["active"] = "oaiapp_test"
            self.auth._save()

        class Reply:
            def raise_for_status(self):
                pass

            def json(self):
                return {"models": [
                    {"slug": "gpt-newest", "display_name": "Newest", "visibility": "list"},
                    {"slug": "internal", "display_name": "Internal", "visibility": "hidden"},
                    {"slug": "gpt-older", "display_name": "Older", "visibility": "list"},
                ]}

        with patch.object(ChatGPTAuth, "_post_token", return_value={
                 "access_token": "new-access", "refresh_token": "new-refresh",
                 "scope": "chatgpt.tokens.use.direct", "expires_in": 3600,
             }) as post, \
             patch("ltx_prompt_director.chatgpt_auth.requests.get", return_value=Reply()) as get:
            self.assertEqual(self.auth.list_models(), [
                ("gpt-newest", "Newest"), ("gpt-older", "Older"),
            ])
        self.assertEqual(post.call_args.args[0]["refresh_token"], "old-refresh")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer new-access")
        self.assertEqual(ChatGPTAuth(self.auth.path).access_token(), "new-access")
        self.assertEqual(self.auth.data["profiles"]["oaiapp_test"]["refresh_token"], "new-refresh")

    def test_unsigned_token_is_not_accepted_as_identity(self):
        with patch("ltx_prompt_director.chatgpt_auth.requests.get") as get:
            get.return_value.json.return_value = {
                "issuer": "https://auth.openai.com", "jwks_uri": "https://auth.openai.com/keys"}
            with patch("ltx_prompt_director.chatgpt_auth.jwt.PyJWKClient") as key:
                key.return_value.get_signing_key_from_jwt.side_effect = ValueError("bad jwt")
                with self.assertRaises(ChatGPTAuthError):
                    ChatGPTAuth._verify_id_token("unsigned", "oaiapp_test", "nonce")

    def test_private_local_file_mode(self):
        if hasattr(__import__("os"), "stat") and __import__("os").name == "posix":
            self.assertEqual(self.auth.path.stat().st_mode & 0o777, 0o600)


class ChatGPTInferenceTests(unittest.TestCase):
    def test_streaming_request_requires_store_false_and_terminal_completion(self):
        request = {}

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def raise_for_status(self):
                pass

            def iter_lines(self, decode_unicode=True):
                for obj in [
                    {"type": "response.output_text.delta", "delta": '{"prompt":'},
                    {"type": "response.output_text.delta", "delta": '"done"}'},
                    {"type": "response.completed", "response": {"output": []}},
                ]:
                    yield ("data: " + json.dumps(obj)).encode().decode()

        def post(url, **kwargs):
            request.update(kwargs)
            self.assertEqual(url, "https://api.openai.com/v1/responses")
            return Reply()

        with patch.object(ai.requests, "post", side_effect=post):
            raw = ai._openai_raw([], "oauth-token", "Return JSON.", 10,
                                  model="gpt-test", chatgpt=True)
        self.assertEqual(raw, '{"prompt":"done"}')
        self.assertEqual(request["json"]["model"], "gpt-test")
        self.assertIs(request["json"]["store"], False)
        self.assertIs(request["json"]["stream"], True)
        self.assertEqual(request["headers"]["authorization"], "Bearer oauth-token")

    def test_incomplete_stream_is_never_reported_as_success(self):
        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def raise_for_status(self):
                pass

            def iter_lines(self, decode_unicode=True):
                yield 'data: {"type":"response.output_text.delta","delta":"partial"}'

        with patch.object(ai.requests, "post", return_value=Reply()):
            with self.assertRaises(ai.AIResponseFormatError):
                ai._openai_raw([], "token", "rules", 5, model="gpt-test", chatgpt=True)


if __name__ == "__main__":
    unittest.main()
