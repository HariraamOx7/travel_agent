import { useState, useRef, useEffect } from 'react'

const QUICK_PROMPTS = [
  'Plan it',
  'Make it more relaxed',
  'What will this trip cost?',
  'Swap the hotel for something cheaper',
]

export default function ChatPane({ messages, onSend, busy }) {
  const [text, setText] = useState('')
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  const submit = (e) => {
    e.preventDefault()
    if (!text.trim() || busy) return
    onSend(text.trim())
    setText('')
  }

  return (
    <div className="panel flex flex-col overflow-hidden h-full min-h-0">
      <div className="px-4 py-3 border-b border-border flex items-center gap-3 shrink-0">
        <span className="inline-grid place-items-center w-8 h-8 rounded-full bg-gradient-to-br from-accent to-amber-500 text-black text-sm shadow-glow">
          🤖
        </span>
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-tight">Trip assistant</div>
          <div className="text-[11px] text-muted">Ask for changes anytime</div>
        </div>
        <span className="ml-auto flex items-center gap-1.5 text-[11px] text-emerald-400">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          online
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0">
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center gap-4 fade-up py-8">
            <span className="text-3xl">🗺️</span>
            <div>
              <div className="text-sm font-medium text-white">Start planning</div>
              <div className="text-xs text-muted mt-1 max-w-xs">
                Tell me the destination and dates — I'll build the itinerary as we chat.
              </div>
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              {QUICK_PROMPTS.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => onSend(q)}
                  disabled={busy}
                  className="pill hover:text-accent hover:border-accent/50 transition disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => {
          const isError = m.role === 'assistant' && /^⚠️/.test(m.content || '')
          const isUser = m.role === 'user'
          return (
            <div key={i} className={`fade-up ${isUser ? 'flex justify-end' : 'flex gap-2.5'}`}>
              {!isUser && (
                <span className="shrink-0 self-end w-7 h-7 grid place-items-center rounded-full bg-surface border border-border text-[11px]">
                  🤖
                </span>
              )}
              <div
                className={`max-w-[86%] p-3 text-sm ${
                  isUser
                    ? 'bg-accent/15 border border-accent/30 rounded-2xl rounded-br-md'
                    : isError
                      ? 'bg-red-500/10 border border-red-500/30 rounded-2xl rounded-bl-md text-red-200'
                      : 'bg-bg border border-border rounded-2xl rounded-bl-md'
                }`}
              >
                <div className="flex items-center justify-between gap-3 mb-1 text-[10px] text-muted">
                  <span>{isUser ? 'You' : 'Assistant'}</span>
                  {m.routing && (
                    <span
                      className={`px-1.5 py-0.5 rounded font-mono ${
                        m.routing.target === 'nlp'
                          ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/25'
                          : 'bg-purple-500/15 text-purple-400 border border-purple-500/25'
                      }`}
                    >
                      {m.routing.target === 'nlp' ? '⚡ NLP' : '🧠 LLM'}
                      {m.routing.latency_ms ? ` · ${m.routing.latency_ms}ms` : ''}
                    </span>
                  )}
                </div>
                <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>
              </div>
            </div>
          )
        })}
        {busy && (
          <div className="flex items-center gap-2 fade-up">
            <span className="inline-flex items-center gap-1 bg-bg border border-border rounded-2xl px-3 py-3">
              <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent" style={{ animationDelay: '0ms' }} />
              <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent" style={{ animationDelay: '150ms' }} />
              <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent" style={{ animationDelay: '300ms' }} />
            </span>
            <span className="text-xs text-muted">Thinking…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={submit} className="border-t border-border p-3 flex gap-2 shrink-0">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask for changes…"
          aria-label="Message the trip assistant"
          className="flex-1 bg-bg border border-border rounded-full px-4 py-2.5 text-sm placeholder:text-muted focus:border-accent/60 transition disabled:opacity-60"
          disabled={busy}
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="bg-accent text-black font-semibold px-5 rounded-full hover:brightness-110 active:scale-95 transition disabled:opacity-40 disabled:active:scale-100"
        >
          Send
        </button>
      </form>
    </div>
  )
}