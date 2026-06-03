// FleetHome.jsx — the fleet-health overview / "front page" (platform vision §4.1).
//
// The home view the design council approved instead of a multi-app shell: a
// signal-aggregating page that shows the fleet at a glance and deep-links into
// the drift table for detail. It reads the same public endpoints the dashboard
// uses (/devices, /drifts, /alert-rules) and hands them to buildFleetSummary
// (the summary contract in fleet.js) — all the aggregation logic lives there,
// unit-tested away from React; this component is just rendering + fetching.

import { useState, useEffect } from 'react'
import { buildFleetSummary } from './fleet'
import './FleetHome.css'

// Render the newest-drift timestamp in the viewer's locale, falling back to the
// raw string if it is not a parseable date.
function formatActivity(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export default function FleetHome() {
  const [data, setData] = useState(null)   // { drifts, devices, alertRules }
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetch('/drifts').then((r) => r.json()),
      fetch('/devices').then((r) => r.json()),
      fetch('/alert-rules').then((r) => r.json()),
    ])
      .then(([drifts, devices, alertRules]) => {
        if (!cancelled) setData({ drifts, devices, alertRules })
      })
      .catch((err) => { if (!cancelled) setError(err.message) })
    return () => { cancelled = true }
  }, [])

  if (error) {
    return <p className="fleet-error" role="alert">Failed to load fleet health: {error}</p>
  }
  if (!data) {
    return <p className="fleet-loading">Loading fleet health…</p>
  }

  const { cards, lastActivity } = buildFleetSummary(data)

  return (
    <section className="fleet-home" aria-label="Fleet health summary">
      <header className="fleet-home__header">
        <h1>Fleet health</h1>
        <p className="fleet-activity">
          {lastActivity
            ? <>Last activity: <time dateTime={lastActivity}>{formatActivity(lastActivity)}</time></>
            : 'No drift detected yet.'}
        </p>
      </header>

      <div className="fleet-grid">
        {cards.map((c) => (
          <a
            key={c.key}
            href={c.href}
            className={`fleet-card fleet-card--${c.severity} fleet-card--status-${c.status}`}
            aria-label={`${c.label}: ${c.count}, status ${c.status}`}
          >
            <span className="fleet-card__count">{c.count}</span>
            <span className="fleet-card__label">{c.label}</span>
          </a>
        ))}
      </div>
    </section>
  )
}
