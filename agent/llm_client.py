"""Shared LLM client construction.

Both the Orchestrator (ReAct loop) and the activity classifier need the same
provider selection and env wiring. Duplicating it in two modules is how the
provider logic drifted before, so it lives in exactly one place here.

Public API
----------
    build_llm_client() -> (provider, model, client, extra_body)
        Raises RuntimeError on misconfiguration (unknown provider, missing
        model name) — the same behaviour the Orchestrator documents.

    complete_json(prompt, *, temperature=0.0, max_tokens=2048) -> dict | None
        One-shot completion, expected to return JSON. NEVER raises: any
        network, auth, provider or parse failure returns None so callers can
        degrade instead of breaking a user-facing turn.
"""
import json
import os
import re
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

REASONING_EFFORT = "low"     # only sent for gpt-oss models

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def build_llm_client():
    """Return (provider, model, client, extra_body) for the configured provider.

    Providers: groq | nvidia | local (Ollama). Mirrors Orchestrator's
    selection so the two can never disagree about which model is in use.
    """
    provider = os.environ.get("LLM_PROVIDER", "groq").lower()

    if provider == "groq":
        model = os.environ.get("GROQ_MODEL")
        if not model:
            raise RuntimeError(
                "GROQ_MODEL is not set. Add to .env, e.g.\n"
                "  GROQ_MODEL=openai/gpt-oss-120b"
            )
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        extra: dict = (
            {"reasoning_effort": REASONING_EFFORT} if "gpt-oss" in model else {}
        )

    elif provider == "nvidia":
        model = os.environ.get("NVIDIA_MODEL")
        if not model:
            raise RuntimeError(
                "NVIDIA_MODEL is not set. Add to .env, e.g.\n"
                "  NVIDIA_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b"
            )
        from openai import OpenAI
        client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.environ["NVIDIA_API_KEY"],
        )
        extra = {"reasoning_effort": REASONING_EFFORT, "frequency_penalty": 0.5}

    elif provider == "local":
        model = os.environ.get("LOCAL_MODEL", "qwen2.5:7b-instruct-q4_K_M")
        from openai import OpenAI
        client = OpenAI(
            base_url=os.environ.get("LOCAL_BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama",       # Ollama ignores this; the SDK needs non-empty
        )
        extra = {}

    else:
        raise RuntimeError(
            f"unknown LLM_PROVIDER: {provider!r} (expected: groq | nvidia | local)"
        )

    return provider, model, client, extra


def _extract_json(text: str) -> Optional[dict]:
    """Pull one JSON object out of a model response, fences and prose included."""
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except ValueError:
        return None


def complete_json(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> Optional[dict]:
    """One-shot JSON completion. Returns a dict, or None on ANY failure."""
    try:
        _provider, model, client, extra = build_llm_client()
    except Exception as e:                     # noqa: BLE001 - degrade, never raise
        print(f"  [llm] client unavailable: {type(e).__name__}: {e}", flush=True)
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if extra:
        kwargs["extra_body"] = extra

    try:
        resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
    except Exception as e:                     # noqa: BLE001 - degrade, never raise
        print(f"  [llm] completion failed: {type(e).__name__}: {e}", flush=True)
        return None

    return _extract_json(content)
