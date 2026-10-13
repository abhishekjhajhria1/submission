"""LLM clients: JSON decisions, document reading, rate limiting, retries, token accounting.

The rest of the agent only uses `decide()` and `read_document()`. Two backends:
  - GeminiLLM (default): native SDK; reads PDFs/images natively
  - OpenAICompatLLM: any OpenAI-compatible /chat/completions API (Groq, OpenRouter, OpenAI, Ollama...);
    PDFs fall back to local text-layer extraction
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import zlib
from typing import Callable

import httpx

RETRYABLE = {429, 500, 502, 503, 504}
DOC_PROMPT = ("Transcribe this document faithfully as plain text. Keep every number, date, name, "
              "bill/invoice number and line item exactly as printed. Do not summarise.")


class LLMError(RuntimeError):
    pass


class _Retryable(Exception):
    def __init__(self, message: str, wait: float | None = None):
        super().__init__(message)
        self.wait = wait


class LLM:
    """Shared logic: JSON repair loop, pacing, backoff, usage. Subclasses implement _call()."""

    def __init__(self, model: str, min_interval: float):
        self.model = model
        self.min_interval = min_interval
        self._last_call = 0.0
        self._lock = threading.Lock()
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.on_wait: Callable[[str], None] = lambda msg: None  # surfaced in the UI

    def decide(self, system: str, prompt: str, validate: Callable[[dict], None] | None = None) -> dict:
        """Ask for one JSON object. Malformed or invalid replies are sent back with the error (max 2 repairs)."""
        messages = [("user", prompt)]
        for attempt in range(3):
            text = self._generate(system, messages, json_mode=True)
            try:
                data = _parse_json(text)
                if validate:
                    validate(data)
                return data
            except ValueError as e:
                if attempt == 2:
                    raise LLMError(f"Model kept returning an invalid decision: {e}") from e
                messages += [("assistant", text),
                             ("user", f"That reply was invalid: {e}\nReply again with ONLY the corrected JSON object.")]
        raise AssertionError("unreachable")

    def read_document(self, data: bytes, mime_type: str) -> str:
        raise NotImplementedError

    def _call(self, system: str | None, messages: list, json_mode: bool, attachment=None) -> tuple[str, int, int]:
        """Return (text, input_tokens, output_tokens). Raise _Retryable for transient errors."""
        raise NotImplementedError

    def _generate(self, system: str | None, messages: list, json_mode: bool, attachment=None) -> str:
        delay = 5.0
        for attempt in range(6):
            self._pace()
            try:
                text, tokens_in, tokens_out = self._call(system, messages, json_mode, attachment)
            except _Retryable as e:
                if attempt == 5:
                    raise LLMError(str(e)) from e
                wait = e.wait or delay
                self.on_wait(f"{e}; waiting {wait:.0f}s before retrying")
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue
            self.usage["calls"] += 1
            self.usage["input_tokens"] += tokens_in
            self.usage["output_tokens"] += tokens_out
            if not text:
                raise LLMError("Empty response from model")
            return text
        raise AssertionError("unreachable")

    def _pace(self) -> None:
        with self._lock:
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


class GeminiLLM(LLM):
    def __init__(self, api_key: str, model: str, min_interval: float = 6.0):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set. Put it in .env (see .env.example).")
        super().__init__(model, min_interval)
        from google import genai
        self.client = genai.Client(api_key=api_key)

    def read_document(self, data: bytes, mime_type: str) -> str:
        return self._generate(None, [("user", DOC_PROMPT)], json_mode=False, attachment=(data, mime_type))

    def _call(self, system, messages, json_mode, attachment=None):
        from google.genai import errors, types
        contents = []
        for i, (role, text) in enumerate(messages):
            parts = [types.Part.from_text(text=text)]
            if attachment and i == 0:
                parts.insert(0, types.Part.from_bytes(data=attachment[0], mime_type=attachment[1]))
            contents.append(types.Content(role="model" if role == "assistant" else "user", parts=parts))
        # Gemini 3 models are tuned for their default temperature; lower values can cause looping.
        config = types.GenerateContentConfig(system_instruction=system,
                                             response_mime_type="application/json" if json_mode else None)
        try:
            resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
        except errors.APIError as e:
            if e.code in RETRYABLE:
                raise _Retryable(f"Gemini API returned {e.code}", _retry_after(str(e.details or e.message))) from e
            raise LLMError(f"Gemini API error {e.code}: {e.message}") from e
        except httpx.HTTPError as e:
            raise _Retryable(f"Network error ({type(e).__name__})") from e
        meta = resp.usage_metadata
        tokens_in = (meta.prompt_token_count or 0) if meta else 0
        tokens_out = ((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)) if meta else 0
        return resp.text or "", tokens_in, tokens_out


class OpenAICompatLLM(LLM):
    def __init__(self, api_key: str, base_url: str, model: str, min_interval: float = 6.0):
        if not base_url:
            raise LLMError("OPENAI_BASE_URL is not set (e.g. https://api.groq.com/openai/v1)")
        super().__init__(model, min_interval)
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=120,
                                 headers={"Authorization": f"Bearer {api_key}"} if api_key else {})

    def read_document(self, data: bytes, mime_type: str) -> str:
        if mime_type == "application/pdf":
            text = pdf_text(data)
            if text.strip():
                return text
            raise LLMError("This PDF has no text layer; a vision-capable provider (Gemini) is needed to read it")
        return self._generate(None, [("user", DOC_PROMPT)], json_mode=False, attachment=(data, mime_type))

    def _call(self, system, messages, json_mode, attachment=None):
        payload_msgs = [{"role": "system", "content": system}] if system else []
        for i, (role, text) in enumerate(messages):
            content = text
            if attachment and i == 0:
                url = f"data:{attachment[1]};base64,{base64.b64encode(attachment[0]).decode()}"
                content = [{"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": text}]
            payload_msgs.append({"role": role, "content": content})
        payload = {"model": self.model, "messages": payload_msgs, "temperature": 0.2}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = self.http.post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise _Retryable(f"Network error ({type(e).__name__})") from e
        if r.status_code in RETRYABLE:
            wait = float(r.headers["retry-after"]) + 1 if r.headers.get("retry-after", "").replace(".", "").isdigit() \
                else _retry_after(r.text)
            raise _Retryable(f"LLM API returned {r.status_code}", wait)
        if r.status_code >= 400:
            raise LLMError(f"LLM API error {r.status_code}: {r.text[:300]}")
        body = r.json()
        usage = body.get("usage") or {}
        return (body["choices"][0]["message"].get("content") or "", usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0))


def make_llm(settings) -> LLM:
    if settings.provider == "openai":
        return OpenAICompatLLM(settings.openai_api_key, settings.openai_base_url, settings.model,
                               settings.min_seconds_between_calls)
    return GeminiLLM(settings.gemini_api_key, settings.model, settings.min_seconds_between_calls)


# --------------------------------------------------------------------------- helpers

def _parse_json(text: str) -> dict:
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e})") from e
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _retry_after(text: str) -> float | None:
    match = re.search(r"(?:retryDelay|retry in|try again in)[\"': ]+([\d.]+)\s*s", text, re.I)
    return min(float(match.group(1)) + 1, 90) if match else None


def pdf_text(data: bytes) -> str:
    """Basic text-layer extraction for simple PDFs (Tj/TJ operators, optionally Flate-compressed)."""
    lines = []
    for stream in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            stream = zlib.decompress(stream)
        except zlib.error:
            pass
        for tj, tj_array in re.findall(rb"\(((?:\\.|[^\\)])*)\)\s*Tj|\[(.*?)\]\s*TJ", stream, re.S):
            parts = [tj] if tj else re.findall(rb"\(((?:\\.|[^\\)])*)\)", tj_array)
            text = b"".join(parts)
            text = re.sub(rb"\\([()\\])", rb"\1", text)
            lines.append(text.decode("latin-1"))
    return "\n".join(lines)
