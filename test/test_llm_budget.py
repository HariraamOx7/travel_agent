"""Request-size guards for the LLM chat path.

Groq's on-demand tier rejects any single request over ~8000 TPM with a
413 "Request too large". Three regressions are pinned here:

  1. `_fit_history` must keep every request under budget WITHOUT mutating
     the stored conversation (the chat UI shows the full history).
  2. Dropping old turns must never leave a dangling `role="tool"` message
     without its parent assistant tool_calls entry — the API 400s on that.
  3. `_chat_with_retry` must treat Groq's APIStatusError like OpenAI's
     (the SDKs do not share a base class) and, on 413, halve the prompt
     budget and retry immediately instead of falling into fail-fast.
"""

import time
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from agent.orchestrator import (
    Orchestrator,
    PROMPT_TOKEN_BUDGET,
)
from agent.state import TripState


def _msg(role, text, **extra):
    return {"role": role, "content": text, **extra}


def _est(messages):
    return sum(Orchestrator._estimate_tokens(m) for m in messages)


def _fat_history(n_turns=30, turn_chars=800):
    msgs = [_msg("system", "sys")]
    for i in range(n_turns):
        msgs.append(_msg("user", f"question {i}"))
        msgs.append(_msg("assistant", f"answer {i} " + "x" * turn_chars))
    return msgs


# ── _fit_history ───────────────────────────────────────────────────────

def test_fit_stays_under_budget():
    history = _fat_history()
    assert _est(history) > PROMPT_TOKEN_BUDGET
    fitted = Orchestrator._fit_history(history, PROMPT_TOKEN_BUDGET)
    # +1 message of slack for rounding in the chars/4 heuristic
    assert _est(fitted) <= PROMPT_TOKEN_BUDGET + 1
    assert fitted[0]["role"] == "system"


def test_fit_keeps_newest_turn():
    history = _fat_history()
    history.append(_msg("user", "the question I am asking right now"))
    fitted = Orchestrator._fit_history(history, PROMPT_TOKEN_BUDGET)
    assert fitted[-1]["content"] == "the question I am asking right now"


def test_fit_drops_oldest_first():
    history = _fat_history()
    fitted = Orchestrator._fit_history(history, PROMPT_TOKEN_BUDGET)
    kept = [str(m.get("content") or "") for m in fitted]
    assert not any(c.startswith("answer 0 ") for c in kept)   # oldest gone
    assert fitted[-1]["role"] == "assistant"                  # newest kept


def test_fit_never_mutates_input():
    history = _fat_history()
    snapshot = [dict(m) for m in history]
    Orchestrator._fit_history(history, PROMPT_TOKEN_BUDGET)
    assert history == snapshot


def test_fit_removes_orphaned_tool_messages():
    # A tool result whose parent assistant(tool_calls) got dropped must not
    # open the list — OpenAI-compatible APIs reject that sequence.
    history = [
        _msg("system", "sys"),
        _msg("assistant", "old plan " + "y" * 4000),
        _msg("assistant", "", tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "build_itinerary", "arguments": "{}"},
        }]),
        _msg("tool", "z" * 4000, tool_call_id="call_1"),
        _msg("user", "current question"),
    ]
    fitted = Orchestrator._fit_history(history, 600)
    assert fitted[0]["role"] == "system"
    assert fitted[-1]["content"] == "current question"
    assert all(m["role"] != "tool" or m.get("tool_call_id") for m in fitted)
    # No tool message may appear without a preceding tool_calls entry.
    for i, m in enumerate(fitted):
        if m["role"] == "tool" and i > 0:
            assert fitted[i - 1].get("tool_calls"), (
                f"orphaned tool message at index {i}"
            )


def test_fit_truncates_giant_tool_result_but_keeps_question():
    # Round 2 of the ReAct loop: history is small but ONE tool result is
    # huge. Stage-2 truncation must shrink it and never touch the user turn.
    history = [
        _msg("system", "sys"),
        _msg("user", "plan the days"),
        _msg("assistant", "", tool_calls=[{
            "id": "call_9", "type": "function",
            "function": {"name": "build_itinerary", "arguments": "{}"},
        }]),
        _msg("tool", "w" * 40_000, tool_call_id="call_9"),
    ]
    fitted = Orchestrator._fit_history(history, 3000)
    assert _est(fitted) <= 3000 + 1
    assert fitted[1]["content"] == "plan the days"
    assert fitted[-1]["role"] == "tool"          # observation survives (trimmed)
    assert len(fitted[-1]["content"]) < 40_000


def test_fit_handles_tiny_budget_without_crashing():
    fitted = Orchestrator._fit_history(_fat_history(), 300)
    assert fitted and fitted[0]["role"] == "system"
    assert _est(fitted) <= 300 + 1


def test_fit_empty_and_system_only():
    assert Orchestrator._fit_history([], 1000) == []
    only = [_msg("system", "s")]
    assert Orchestrator._fit_history(only, 1000) == only


# ── _chat_with_retry: 413 trim-and-retry ───────────────────────────────

def _request_too_large():
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(413, request=request)
    return APIStatusError(
        "Request too large for model `openai/gpt-oss-120b` ... "
        "please reduce your message size and try again.",
        response=response,
        body=None,
    )


def _ok_response():
    return SimpleNamespace(
        finish_reason="stop",
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="fine", tool_calls=None),
        )],
    )


@pytest.fixture
def orch():
    return Orchestrator(TripState())


def test_413_halves_budget_and_retries_immediately(orch, monkeypatch):
    seen_sizes = []
    calls = {"n": 0}

    def flaky_create(**kwargs):
        calls["n"] += 1
        seen_sizes.append(_est(kwargs["messages"]))
        if calls["n"] == 1:
            raise _request_too_large()
        return _ok_response()

    monkeypatch.setattr(
        orch.client.chat.completions, "create", flaky_create, raising=False
    )
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    # Build a history that starts above budget so the two payload sizes differ.
    orch.history.extend(_fat_history()[1:])

    resp = orch._chat_with_retry()
    assert resp.choices[0].message.content == "fine"
    assert calls["n"] == 2
    assert seen_sizes[1] < seen_sizes[0]      # second attempt is smaller
    assert sleeps == []                       # 413 must NOT sleep


def test_413_gives_up_after_repeated_failures(orch, monkeypatch):
    def always_413(**kwargs):
        raise _request_too_large()

    monkeypatch.setattr(
        orch.client.chat.completions, "create", always_413, raising=False
    )
    monkeypatch.setattr(time, "sleep", lambda s: None)

    with pytest.raises(APIStatusError):
        orch._chat_with_retry()


def test_fitted_messages_are_what_gets_sent(orch, monkeypatch):
    """The payload must be budget-fitted, never raw self.history."""
    orch.history.extend(_fat_history()[1:])
    orch.history.append(_msg("user", "compare monsoon versus winter in detail"))

    captured = {}

    def create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _ok_response()

    monkeypatch.setattr(
        orch.client.chat.completions, "create", create, raising=False
    )
    orch._chat_with_retry()

    sent = captured["messages"]
    assert sent[0]["role"] == "system"
    assert _est(sent) <= PROMPT_TOKEN_BUDGET + 1
    assert sent[-1]["content"].startswith("compare monsoon")
    # …while the stored history keeps everything.
    assert len(orch.history) > len(sent)
