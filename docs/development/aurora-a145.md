# Aurora 1.13.0a145

OpenAI and Gemini streaming responses decode SSE bytes as UTF-8 regardless of the HTTP client encoding default. This prevents garbled punctuation and international text in generated prompts.

A focused regression using real Requests response decoding passes for both providers, including time ranges, curly quotes, accented text, Japanese and emoji.
