import { useState, useRef, useEffect } from 'react'

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
    <div className="flex flex-col bg-surface border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-2 border-b border-border text-xs uppercase tracking-wider text-muted">
        Chat
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-muted text-sm">
            Ask anything — 'plan it', 'make it relaxed', 'what will it cost?'
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={
              m.role === 'user'
                ? 'bg-accent/10 border border-accent/30 rounded-lg p-3 ml-8'
                : 'bg-bg border border-border rounded-lg p-3 mr-8'
            }
          >
            <div className="text-xs text-muted mb-1">
              {m.role === 'user' ? 'You' : 'Agent'}
            </div>
            <div className="whitespace-pre-wrap text-sm">{m.content}</div>
          </div>
        ))}
        {busy && (
          <div className="text-muted text-sm italic">Thinking…</div>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={submit} className="border-t border-border p-3 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask for changes…"
          className="flex-1 bg-bg border border-border rounded px-3 py-2 text-sm"
          disabled={busy}
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="bg-accent text-black font-semibold px-4 rounded disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  )
}