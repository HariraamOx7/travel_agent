"""Smoke test: confirm the configured LLM emits a well-formed tool call."""
import os, json, time
from dotenv import load_dotenv
load_dotenv(override=True)

from groq import Groq
from agent.schemas import TOOL_DECLARATIONS

MODEL = os.environ["GROQ_MODEL"]          # <-- never hardcode
print(f"[test] provider=groq model={MODEL}")

client = Groq(api_key=os.environ["GROQ_API_KEY"])

t0 = time.time()
resp = client.chat.completions.create(
    model=MODEL,
    temperature=0.2,
    max_completion_tokens=2048,           # gpt-oss: use this, not max_tokens
    messages=[
        {"role": "system", "content":
            "Extract trip info by calling extract_trip_slots. Never invent values."},
        {"role": "user", "content":
            "I want to go somewhere hilly from Chennai, 15/9/26 to 20/9/26, under 40k INR"},
    ],
    tools=TOOL_DECLARATIONS,
    tool_choice="auto",
    reasoning_effort="low",               # gpt-oss only; remove for other models
)
print(f"[test] latency: {time.time()-t0:.1f}s")

choice = resp.choices[0]
msg = choice.message
print(f"[test] finish_reason: {choice.finish_reason}")

if not msg.tool_calls:
    print("[FAIL] prose instead of tool call:")
    print((msg.content or "")[:500])
else:
    for tc in msg.tool_calls:
        print(f"[tool] {tc.function.name}")
        try:
            print(json.dumps(json.loads(tc.function.arguments), indent=2))
        except json.JSONDecodeError:
            print("[FAIL] malformed JSON:", repr(tc.function.arguments)[:200])