"""Setup check: python -m farmhand.doctor [--doc]

Verifies the API key, the configured model (and lists alternatives), the farm app,
and optionally document reading on a sample receipt (one extra LLM call).
"""
from __future__ import annotations

import sys

import httpx

from .config import Settings, load_profile
from .llm import GeminiLLM, LLMError, make_llm
from .tools import api_tools


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings, ok = Settings(), True
    profile = load_profile(settings.profile)
    print(f"profile: {profile.name}  model: {settings.model}")

    base = profile.data["api"]["base_url"]
    try:
        tools = api_tools(base, profile.data["api"]["openapi_path"], "doctor")
        print(f"✓ farm app at {base}: {len(tools)} API tools discovered")
    except (httpx.HTTPError, RuntimeError) as e:
        ok = False
        print(f"✗ farm app not reachable at {base} ({e}). Start it: python run.py")

    print(f"provider: {settings.provider}")
    if settings.provider == "gemini" and not settings.gemini_api_key:
        print("✗ GEMINI_API_KEY missing: copy .env.example to .env and add your key")
        sys.exit(1)
    try:
        settings.min_seconds_between_calls = 0
        llm = make_llm(settings)
        if isinstance(llm, GeminiLLM):
            models = [m.name.removeprefix("models/") for m in llm.client.models.list()
                      if "generateContent" in (m.supported_actions or [])]
        else:
            models = [m["id"] for m in llm.http.get("/models").json().get("data", [])]
        print(f"✓ API key works. Models available: {', '.join(m for m in models if 'flash' in m or settings.provider != 'gemini')[:600]}")
        for missing in [m for m in llm.models if m not in models]:
            print(f"! configured model {missing!r} is not available to this key; edit FARMHAND_MODEL in .env")
        reply = llm.decide("Reply with JSON only.", 'Return {"ok": true, "sum": 2400/100 + 3}')
        print(f"✓ JSON decision call works: {reply}")
        if "--doc" in sys.argv:
            receipt = profile.files_root / "receipts" / "IMG_20261011_scan.pdf"
            print("✓ document reading:\n" + llm.read_document(receipt.read_bytes(), "application/pdf")[:600])
        print(f"  usage: {llm.usage}")
    except (LLMError, httpx.HTTPError) as e:
        ok = False
        print(f"✗ LLM check failed: {e}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
