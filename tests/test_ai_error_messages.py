import unittest

import requests

from ltx_prompt_director.ai import provider_error_message


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


if __name__ == "__main__":
    unittest.main()
