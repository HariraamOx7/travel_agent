import { useState } from 'react'
import MapTab from './MapTab'
import ItineraryTab from './ItineraryTab'
import IdeasTab from './IdeasTab'
import StayTab from './StayTab'

const TABS = [
  { id: 'map', label: '🗺️ Map' },
  { id: 'itinerary', label: '📅 Itinerary' },
  { id: 'ideas', label: '💡 Ideas' },
  { id: 'stay', label: '🏨 Stay' },
  { id: 'costs', label: '💰 Costs' },
  { id: 'trace', label: '⚡ Trace' },
]

export default function TripPane({ sessionId, state, trace = [], onStateRefresh }) {
  const [activeTab, setActiveTab] = useState('map')

  const budget = state?.recommendations?.budget
  const lineItems = budget?.line_items_inr || {}

  return (
    <div className="flex flex-col bg-surface border border-border rounded-xl overflow-hidden h-full">
      {/* Tab Navigation Header */}
      <div className="flex border-b border-border bg-bg/50 px-2 pt-2 gap-1 overflow-x-auto scrollbar-none">
        {TABS.map((tab) => {
          const isActive = activeTab === tab.id
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2 text-xs font-medium rounded-t-lg transition whitespace-nowrap ${
                isActive
                  ? 'bg-surface text-accent border-t-2 border-accent font-semibold'
                  : 'text-muted hover:text-white hover:bg-surface/50'
              }`}
            >
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* Tab Body */}
      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === 'map' && <MapTab sessionId={sessionId} />}

        {activeTab === 'itinerary' && <ItineraryTab state={state} />}

        {activeTab === 'ideas' && (
          <IdeasTab
            sessionId={sessionId}
            state={state}
            onStateRefresh={onStateRefresh}
          />
        )}

        {activeTab === 'stay' && <StayTab state={state} />}

        {activeTab === 'costs' && (
          <div className="space-y-4">
            {!budget ? (
              <div className="text-muted text-sm p-4 text-center border border-dashed border-border rounded-xl">
                No cost estimate computed yet. Ask the agent in the chat:
                <div className="text-white font-medium mt-1">"What will this trip cost?"</div>
              </div>
            ) : (
              <div className="space-y-3 bg-bg border border-border rounded-xl p-4">
                <div className="font-semibold text-sm pb-2 border-b border-border">
                  Estimated Breakdown
                </div>
                <div className="space-y-2">
                  {Object.entries(lineItems).map(([item, cost]) => (
                    <div
                      key={item}
                      className="flex justify-between items-center text-sm py-1 border-b border-border/40"
                    >
                      <span className="text-muted capitalize">
                        {item.replace(/_/g, ' ')}
                      </span>
                      <span className="font-medium text-white">
                        ₹{Number(cost).toLocaleString('en-IN')}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="flex justify-between items-center text-base font-bold pt-2 border-t border-border text-accent">
                  <span>Total</span>
                  <span>₹{Number(budget.total_inr || 0).toLocaleString('en-IN')}</span>
                </div>
                {budget.verdict && (
                  <div className="mt-3 p-2.5 rounded bg-accent/10 border border-accent/20 text-accent text-xs">
                    {budget.verdict}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {activeTab === 'trace' && (
          <div className="space-y-3">
            {trace.length === 0 ? (
              <div className="text-muted text-sm p-4 text-center border border-dashed border-border rounded-xl">
                No agent trace logs yet. Send a message to see router decisions and tool executions.
              </div>
            ) : (
              <div className="space-y-3 text-xs">
                {trace.map((item, idx) => {
                  const type = item.type || (item.tool ? 'tool_call' : 'action')

                  if (type === 'router_decision') {
                    const isNlp = item.target === 'nlp'
                    return (
                      <div
                        key={idx}
                        className="bg-bg border border-border/80 rounded-xl p-3.5 space-y-2.5 shadow-sm"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 font-semibold">
                            <span className="text-accent text-sm">⚡</span>
                            <span className="text-white">In-Between Router Decision</span>
                          </div>
                          <span
                            className={`px-2 py-0.5 rounded-full font-mono font-bold text-[11px] ${
                              isNlp
                                ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                                : 'bg-purple-500/20 text-purple-400 border border-purple-500/30'
                            }`}
                          >
                            Target: {item.target?.toUpperCase()}
                          </span>
                        </div>

                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] font-mono bg-surface/50 p-2 rounded-lg border border-border/40">
                          <div>
                            <span className="text-muted block">Confidence</span>
                            <span className="font-bold text-white">
                              {Math.round((item.confidence || 0) * 100)}%
                            </span>
                          </div>
                          <div>
                            <span className="text-muted block">Latency</span>
                            <span className="font-bold text-accent">
                              {item.latency_ms ? `${item.latency_ms} ms` : '< 25 ms'}
                            </span>
                          </div>
                          <div>
                            <span className="text-muted block">NLP Score</span>
                            <span className="text-white/80">{item.nlp_score ?? '-'}</span>
                          </div>
                          <div>
                            <span className="text-muted block">LLM Score</span>
                            <span className="text-white/80">{item.llm_score ?? '-'}</span>
                          </div>
                        </div>

                        {item.suggested_tool && (
                          <div className="text-[11px]">
                            <span className="text-muted">Suggested Tool: </span>
                            <span className="font-mono text-accent font-semibold">{item.suggested_tool}</span>
                          </div>
                        )}

                        {item.reasons && item.reasons.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 pt-1">
                            {item.reasons.map((r, ri) => (
                              <span
                                key={ri}
                                className="px-2 py-0.5 bg-border/40 text-muted rounded text-[10px] font-mono"
                              >
                                {r}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  }

                  if (type === 'nlp_tool_router') {
                    return (
                      <div
                        key={idx}
                        className="bg-bg border border-emerald-500/30 rounded-xl p-3.5 space-y-2 shadow-sm"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 font-semibold text-emerald-400">
                            <span>🛠️</span>
                            <span>NLP Tool Called: {item.tool_called}</span>
                          </div>
                          <span className="text-[10px] text-muted font-mono">
                            {item.intent} ({item.source})
                          </span>
                        </div>
                        {item.tool_result && (
                          <div className="bg-surface/50 p-2.5 rounded-lg border border-border/40 font-mono text-[11px] overflow-x-auto text-white/80 max-h-48 overflow-y-auto">
                            <pre>{JSON.stringify(item.tool_result, null, 2)}</pre>
                          </div>
                        )}
                      </div>
                    )
                  }

                  if (type === 'nlu_slots') {
                    return (
                      <div
                        key={idx}
                        className="bg-bg border border-border rounded-xl p-3.5 space-y-2 shadow-sm"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 font-semibold text-accent">
                            <span>🧩</span>
                            <span>NLU Entity Extraction</span>
                          </div>
                          <span className="text-[10px] text-muted font-mono">
                            intent: {item.intent}
                          </span>
                        </div>
                        {item.slots && Object.keys(item.slots).length > 0 && (
                          <div className="flex flex-wrap gap-1.5">
                            {Object.entries(item.slots).map(([k, v]) => (
                              <span
                                key={k}
                                className="px-2 py-1 bg-surface border border-border rounded text-[11px] font-mono"
                              >
                                <span className="text-muted">{k}:</span>{' '}
                                <span className="text-white font-semibold">
                                  {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                                </span>
                              </span>
                            ))}
                          </div>
                        )}
                        {item.applied && item.applied.length > 0 && (
                          <div className="text-[11px] text-muted font-mono">
                            State updated: {item.applied.join(', ')}
                          </div>
                        )}
                      </div>
                    )
                  }

                  if (type === 'activity_classification') {
                    const usedLlm = item.source === 'llm'
                    return (
                      <div
                        key={idx}
                        className={`bg-bg border rounded-xl p-3.5 space-y-2 shadow-sm ${
                          usedLlm ? 'border-accent/30' : 'border-amber-500/30'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 font-semibold">
                            <span>{usedLlm ? '🧗' : '⚠️'}</span>
                            <span className={usedLlm ? 'text-accent' : 'text-amber-300'}>
                              Activity Classification
                            </span>
                          </div>
                          <span
                            className={`px-2 py-0.5 rounded-full font-mono font-bold text-[11px] ${
                              usedLlm
                                ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                                : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                            }`}
                          >
                            {usedLlm ? 'LLM' : 'DEFAULT'}
                          </span>
                        </div>
                        {item.model && (
                          <div className="text-[11px] text-muted font-mono">
                            Model: {item.model}
                          </div>
                        )}
                        <div className="text-[11px] text-muted">{item.note}</div>
                        {item.unverified_days && item.unverified_days.length > 0 && (
                          <div className="text-[11px] text-amber-300">
                            Unverified days: {item.unverified_days.join(', ')}
                          </div>
                        )}
                      </div>
                    )
                  }

                  if (type === 'cache_hit') {
                    return (
                      <div
                        key={idx}
                        className="bg-bg border border-cyan-500/30 rounded-xl p-3 space-y-1 shadow-sm font-mono text-[11px]"
                      >
                        <div className="flex items-center gap-2 font-bold text-cyan-400">
                          <span>💾</span>
                          <span>Response Cache Hit (&lt; 1 ms)</span>
                        </div>
                        <div className="text-muted truncate">Query: {item.text}</div>
                      </div>
                    )
                  }

                  // Default tool_call (from LLM ReAct or generic actions)
                  return (
                    <div
                      key={idx}
                      className="bg-bg border border-border rounded-xl p-3 space-y-1.5 font-mono text-[11px] overflow-x-auto"
                    >
                      <div className="flex items-center gap-2 font-bold text-accent">
                        <span>[{item.round ?? idx}]</span>
                        <span>{item.tool || item.step || item.type || 'Action'}</span>
                      </div>
                      {item.args && (
                        <div className="text-muted">
                          Args: {typeof item.args === 'string' ? item.args : JSON.stringify(item.args)}
                        </div>
                      )}
                      {item.result && (
                        <div className="text-white/80 whitespace-pre-wrap max-h-40 overflow-y-auto">
                          Result: {typeof item.result === 'string' ? item.result : JSON.stringify(item.result, null, 2)}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
