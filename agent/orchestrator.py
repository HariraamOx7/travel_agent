import json
import os
import time
from typing import Optional

from agent import nlu
from agent.cache import ResponseCache
from agent.router import ToolCallRouter
from agent.tool_router import ToolRouter
from openai import OpenAI, APIStatusError, APIConnectionError
from groq import Groq

from agent import tools
from agent.prompts import SYSTEM_PROMPT
from agent.schemas import TOOL_DECLARATIONS
from agent.state import TripState
from dotenv import load_dotenv

load_dotenv()

TEMPERATURE = 0.2
REASONING_EFFORT = "low"     # only sent for gpt-oss models
MAX_TOKENS = 8192            # reasoning tokens count toward this
MAX_TOOL_ROUNDS = 6

# Models that are NOT chat models (audio, classifiers, TTS, etc.).
# We filter these out of the "available models" warning message.
_NON_CHAT_HINTS = ("whisper", "orpheus", "guard", "safeguard", "tts", "embed")


class Orchestrator:
    """Manual ReAct loop over OpenAI-compatible endpoints.

    Providers:
      - groq    : https://api.groq.com/openai/v1   (Groq SDK)
      - nvidia  : https://integrate.api.nvidia.com/v1 (OpenAI SDK)
      - local   : http://localhost:11434/v1        (Ollama, OpenAI SDK)
    """

    def __init__(
        self,
        state: TripState,
        api_key: str | None = None,
        history: Optional[list[dict]] = None,
        trace: Optional[list[dict]] = None,
        client_messages: Optional[list[dict]] = None,
    ):
        self.provider = os.environ.get("LLM_PROVIDER", "groq").lower()

        if self.provider == "groq":
            self.model = os.environ.get("GROQ_MODEL")
            if not self.model:
                raise RuntimeError(
                    "GROQ_MODEL is not set. Add to .env, e.g.\n"
                    "  GROQ_MODEL=openai/gpt-oss-120b"
                )
            self.client = Groq(api_key=api_key or os.environ["GROQ_API_KEY"])
            # gpt-oss models are reasoning models — they accept reasoning_effort.
            # Everything else (Llama, Qwen, etc.) rejects it.
            self._extra_body: dict = (
                {"reasoning_effort": REASONING_EFFORT}
                if "gpt-oss" in self.model
                else {}
            )

        elif self.provider == "nvidia":
            self.model = os.environ.get("NVIDIA_MODEL")
            if not self.model:
                raise RuntimeError(
                    "NVIDIA_MODEL is not set. Add to .env, e.g.\n"
                    "  NVIDIA_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b"
                )
            self.client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=api_key or os.environ["NVIDIA_API_KEY"],
            )
            self._extra_body = {
                "reasoning_effort": REASONING_EFFORT,
                "frequency_penalty": 0.5,
            }

        elif self.provider == "local":
            self.model = os.environ.get("LOCAL_MODEL", "qwen2.5:7b-instruct-q4_K_M")
            self.client = OpenAI(
                base_url=os.environ.get("LOCAL_BASE_URL", "http://localhost:11434/v1"),
                api_key="ollama",       # Ollama ignores this, SDK needs non-empty
            )
            self._extra_body = {}

        else:
            raise RuntimeError(
                f"unknown LLM_PROVIDER: {self.provider!r} "
                f"(expected: groq | nvidia | local)"
            )

        print(f"[model] {self.provider}:{self.model}")

        # --- Startup catalog check (Groq only) -----------------------------
        # Groq rotates models frequently. Warn (don't crash) if the configured
        # model isn't in the live catalog — this is exactly the failure that
        # produces "model does not exist or you do not have access to it".
        if self.provider == "groq":
            try:
                available = [m.id for m in self.client.models.list().data]
                if self.model not in available:
                    chat_models = sorted(
                        m for m in available
                        if not any(h in m for h in _NON_CHAT_HINTS)
                    )
                    print(
                        f"[warn] '{self.model}' is NOT in your Groq catalog.\n"
                        f"[warn] Available chat models:\n"
                        + "\n".join(f"  - {m}" for m in chat_models)
                    )
            except Exception as e:
                print(f"[warn] could not list Groq models: {e}")

        self.state = state
        self.tool_impls = {
            "extract_trip_slots": tools.extract_trip_slots,
            "search_destination_candidates": tools.search_destination_candidates,
            "confirm_destination": tools.confirm_destination,
            "get_weather": tools.get_weather,
            "build_itinerary": tools.build_itinerary,
            "recommend_transport": tools.recommend_transport,
            "get_recommendations": tools.get_recommendations,
            "estimate_budget": tools.estimate_budget,
        }
        self.cache = ResponseCache(maxsize=100)
        self.router = ToolCallRouter()
        self.tool_router = ToolRouter(self.tool_impls)
        self.history: list[dict] = history or [{"role": "system", "content": ""}]
        self.trace: list[dict] = trace or []
        self.client_messages: list[dict] = client_messages or []
        if not history and self.client_messages:
            for m in self.client_messages:
                if m.get("role") in ("user", "assistant") and m.get("content"):
                    self.history.append({"role": m["role"], "content": m["content"]})
        elif not self.client_messages and len(self.history) > 1:
            for m in self.history:
                if m.get("role") in ("user", "assistant") and m.get("content"):
                    self.client_messages.append({"role": m["role"], "content": m["content"]})

    # ------------------------------------------------------------------ #
    # Prompt construction
    # ------------------------------------------------------------------ #

    def _messages(self) -> list[dict]:
        """State is re-injected fresh on EVERY call — the agent's memory."""
        self.history[0] = {
            "role": "system",
            "content": (
                f"{SYSTEM_PROMPT}\n\n"
                f"CURRENT TRIP STATE (memory):\n"
                f"{self.state.summary_for_prompt()}"
            ),
        }
        return self.history

    # ------------------------------------------------------------------ #
    # LLM call with retry / backoff
    # ------------------------------------------------------------------ #

    def _chat_with_retry(self):
        for attempt in range(4):
            try:
                print("[llm] thinking...", flush=True)

                kwargs: dict = dict(
                    model=self.model,
                    messages=self._messages(),
                    tools=TOOL_DECLARATIONS,
                    temperature=TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )

                # Groq prefers `max_completion_tokens` over `max_tokens` on
                # reasoning models; both are accepted on gpt-oss. If you get a
                # 400 about max_tokens, flip the key below.
                if self.provider == "groq" and "gpt-oss" in self.model:
                    kwargs.pop("max_tokens", None)
                    kwargs["max_completion_tokens"] = MAX_TOKENS

                if self._extra_body:
                    kwargs["extra_body"] = self._extra_body

                return self.client.chat.completions.create(**kwargs)

            except APIStatusError as e:
                code = getattr(e, "status_code", None)

                if code == 429:
                    # Groq sends Retry-After; NVIDIA doesn't.
                    retry_after = 0
                    try:
                        retry_after = int(e.response.headers.get("retry-after", "0"))
                    except Exception:
                        pass
                    wait = retry_after or (
                        20 * (attempt + 1) if self.provider == "nvidia"
                        else 5 * (attempt + 1)
                    )
                    print(
                        f"\n[429 rate limit] waiting {wait}s "
                        f"(attempt {attempt + 1}/4)...",
                        flush=True,
                    )
                    time.sleep(wait)
                    continue

                if code and code >= 500:
                    wait = 10 * (attempt + 1)
                    print(f"\n[{code} upstream busy] waiting {wait}s...", flush=True)
                    time.sleep(wait)
                    continue

                # 401 / 404 / 400 — fail fast with context
                raise

            except APIConnectionError:
                wait = 10 * (attempt + 1)
                print(f"\n[connection error] waiting {wait}s...", flush=True)
                time.sleep(wait)

        raise RuntimeError("LLM unavailable after retries")

    # ------------------------------------------------------------------ #
    # Main ReAct loop
    # ------------------------------------------------------------------ #

    def chat(self, user_text: str) -> str:
        # --- 1. State-aware response cache check ------------------------
        cached = self.cache.get(user_text, self.state)
        if cached is not None:
            self.trace.append({"type": "cache_hit", "text": user_text})
            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": cached})
            self.client_messages.append({"role": "user", "content": user_text})
            self.client_messages.append({
                "role": "assistant",
                "content": cached,
                "routing": {"target": "nlp", "confidence": 1.0, "latency_ms": 0.2, "reasons": ["cache:hit"]},
            })
            return cached

        # --- 2. NLU pre-processing (deterministic, no LLM) --------------
        result = nlu.understand(user_text, state=self.state)

        # Apply any NER-extracted slots immediately. The LLM will still see
        # the updated state via summary_for_prompt() if it needs to reason.
        if result.has_slots:
            applied = tools.extract_trip_slots(result.slots, self.state)
            self.trace.append({
                "type": "nlu_slots",
                "intent": result.intent,
                "source": result.source,
                "confidence": round(result.confidence, 3),
                "slots": result.slots,
                "applied": applied.get("applied", []),
            })

        self.history.append({"role": "user", "content": user_text})
        self.client_messages.append({"role": "user", "content": user_text})

        # --- 3. In-Between Model: Semantic & Hybrid Routing Decision ---
        decision = self.router.decide(user_text, result, self.state)
        self.trace.append({
            "type": "router_decision",
            "target": decision.target,
            "confidence": decision.confidence,
            "nlp_score": decision.nlp_score,
            "llm_score": decision.llm_score,
            "reasons": decision.reasons,
            "latency_ms": decision.latency_ms,
            "suggested_tool": decision.suggested_tool,
        })

        if decision.target == "nlp":
            handled, reply, tool_called, tool_res = self.tool_router.route_and_execute(
                user_text, result, self.state
            )
            if handled and reply is not None:
                self.trace.append({
                    "type": "nlp_tool_router",
                    "engine": "nlp",
                    "intent": result.intent,
                    "source": result.source,
                    "confidence": round(result.confidence, 3),
                    "tool_called": tool_called,
                    "tool_result": tool_res,
                })
                activity_entry = self._activity_trace(tool_called, tool_res)
                if activity_entry:
                    self.trace.append(activity_entry)
                self.history.append({"role": "assistant", "content": reply})
                self.client_messages.append({
                    "role": "assistant",
                    "content": reply,
                    "routing": decision.to_dict() if decision else None,
                })
                self.cache.set(user_text, self.state, reply)
                return reply
            else:
                self.trace.append({
                    "type": "router_escalation",
                    "reason": "NLP engine could not handle turn, escalating to LLM",
                })

        # --- 4. LLM Tool Engine (ReAct loop) ----------------------------
        for _ in range(MAX_TOOL_ROUNDS):
            resp = self._chat_with_retry()
            choice = resp.choices[0]
            msg = choice.message

            if choice.finish_reason == "length":
                print(
                    "[warn] completion truncated — raise MAX_TOKENS "
                    "or lower reasoning_effort",
                    flush=True,
                )

            # Build the assistant entry we push back into history.
            entry: dict = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": t.id,
                        "type": "function",
                        "function": {
                            "name": t.function.name,
                            "arguments": t.function.arguments,
                        },
                    }
                    for t in msg.tool_calls
                ]
            self.history.append(entry)

            # No tool calls → this is the final natural-language answer.
            if not msg.tool_calls:
                final = msg.content or ""
                self.trace.append({"type": "final_answer", "text": final})
                self.client_messages.append({
                    "role": "assistant",
                    "content": final,
                    "routing": decision.to_dict() if decision else {"target": "llm"},
                })
                self.cache.set(user_text, self.state, final)
                return final

            # ACT + OBSERVE — one role="tool" message per tool_call_id.
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                    result = {
                        "error": (
                            f"malformed JSON args: "
                            f"{tc.function.arguments[:200]}"
                        )
                    }
                else:
                    impl = self.tool_impls.get(name)
                    result = (
                        impl(args, self.state)
                        if impl
                        else {"error": f"unknown tool: {name}"}
                    )

                self.trace.append(
                    {
                        "type": "tool_call",
                        "tool": name,
                        "args": args,
                        "result": result,
                    }
                )
                activity_entry = self._activity_trace(name, result)
                if activity_entry:
                    self.trace.append(activity_entry)
                self.history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        return "(stopped: max tool-call rounds reached — inspect trace)"

    # ------------------------------------------------------------------ #
    # Short-circuit helper (NLU-driven, no LLM call)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _activity_trace(tool_name: Optional[str], result) -> Optional[dict]:
        """Trace entry for the activity classifier, when a tool ran it.

        The classifier decides what each stop's activity is and how long it
        takes, so it is worth surfacing next to the router decision rather
        than hiding inside a tool result. Returns None when the classifier
        was not consulted (i.e. any tool other than build_itinerary).
        """
        if tool_name != "build_itinerary" or not isinstance(result, dict):
            return None
        source = result.get("activity_source")
        if source is None:
            return None
        return {
            "type": "activity_classification",
            "source": source,
            "model": os.environ.get("GROQ_MODEL") if source == "llm" else None,
            "stops": len((result.get("schedule") or [{}])[0].get("stops") or [])
            if result.get("schedule") else 0,
            "unverified_days": (
                (result.get("validation") or {}).get("unverified_days") or []
            ),
            "note": ("LLM classified each stop's activity and effort"
                     if source == "llm"
                     else "classifier unavailable — conservative defaults"),
        }

    def _try_shortcut(self, text: str, result: "nlu.NLUResult") -> Optional[str]:
        """Backward-compatible helper delegated to ToolRouter."""
        handled, reply, _, _ = self.tool_router.route_and_execute(text, result, self.state)
        return reply if handled else None

    # ------------------------------------------------------------------ #
    # Debug helper
    # ------------------------------------------------------------------ #

    @property
    def pretty_trace(self) -> str:
        return json.dumps(self.trace, indent=2, default=str)