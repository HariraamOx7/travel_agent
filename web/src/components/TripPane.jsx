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

export default function TripPane({ sessionId, state, trace = [] }) {
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

        {activeTab === 'ideas' && <IdeasTab state={state} />}

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
                No agent trace logs yet. Actions and tool executions will appear here.
              </div>
            ) : (
              <div className="space-y-2 font-mono text-xs">
                {trace.map((item, idx) => (
                  <div
                    key={idx}
                    className="bg-bg border border-border rounded-lg p-3 space-y-1 overflow-x-auto"
                  >
                    <div className="flex items-center gap-2 font-bold text-accent">
                      <span>[{item.round ?? idx}]</span>
                      <span>{item.tool || item.step || 'Action'}</span>
                    </div>
                    {item.args && (
                      <div className="text-muted">
                        Args: {typeof item.args === 'string' ? item.args : JSON.stringify(item.args)}
                      </div>
                    )}
                    {item.result && (
                      <div className="text-white/80 whitespace-pre-wrap">
                        Result: {typeof item.result === 'string' ? item.result : JSON.stringify(item.result, null, 2)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
