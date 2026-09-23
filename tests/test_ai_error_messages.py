import unittest

import requests

from ltx_prompt_director.ai import AIResponseFormatError, provider_error_message
from ltx_prompt_director.ui import MagicWorker


class AIErrorMessageTests(unittest.TestCase):
    @staticmethod
    def http_error(status: int, payload: dict) -> requests.HTTPError:
        response = requests.Response()
        response.status_code = status
        response.url = "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent"
        response._content = __import__("json").dumps(payload).encode("utf-8")
        response.headers["content-type"] = "application/json"
        return requests.HTTPError(response=response)

    def test_503_explains_provider_overload_in_plain_language(self):
        error = self.http_error(503, {
            "error": {
                "status": "UNAVAILABLE",
                "message": "This model is currently experiencing high demand.",
            }
        })
        message = provider_error_message(error)
        self.assertIn("temporarily overloaded", message)
        self.assertIn("provider-capacity issue", message)
        self.assertIn("not a problem with your timeline or prompt", message)
        self.assertIn("currently experiencing high demand", message)
        self.assertIn("Google response (HTTP 503)", message)

    def test_other_gemini_http_errors_include_provider_detail(self):
        error = self.http_error(404, {
            "error": {
                "status": "NOT_FOUND",
                "message": "The selected model is no longer available.",
            }
        })
        message = provider_error_message(error)
        self.assertIn("HTTP 404", message)
        self.assertIn("no longer available", message)
        self.assertNotIn("Full Google response", message)

    def test_validation_errors_are_actually_retried_until_success(self):
        calls = []
        finished = []
        failed = []

        def operation():
            calls.append(len(calls) + 1)
            if len(calls) < 3:
                raise AIResponseFormatError("The prompt was too short. The operation will retry.")
            return "detailed prompt"

        worker = MagicWorker(operation, (), retries=2, retry_cooldown=0)
        worker.signals.finished.connect(finished.append)
        worker.signals.failed.connect(failed.append)
        worker.run()

        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(finished, ["detailed prompt"])
        self.assertEqual(failed, [])

    def test_exhausted_validation_error_reports_attempts_without_false_retry_claim(self):
        calls = []
        failed = []

        def operation():
            calls.append(len(calls) + 1)
            raise AIResponseFormatError("The prompt was too short. The operation will retry.")

        worker = MagicWorker(operation, (), retries=1, retry_cooldown=0)
        worker.signals.failed.connect(failed.append)
        worker.run()

        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(failed), 1)
        self.assertNotIn("will retry", failed[0])
        self.assertIn("Stopped after 2 attempts", failed[0])


if __name__ == "__main__":
    unittest.main()
