// Dashboard.jsx — the drift events page.
//
// Calls GET /drifts on mount, shows a loading state while the request is in
// flight, an error state if it fails, a table of events when it succeeds.
// Vite proxies /drifts to FastAPI in dev (see vite.config.js).

import { useState, useEffect, useCallback } from 'react'
import './Dashboard.css'

export default function Dashboard() {
  // Three pieces of state, one per possible UI condition:
  //   drifts  — the list of events once they've loaded (null = not loaded yet)
  //   error   — a string describing what went wrong, or null if nothing did
  //   loading — true while the fetch is in flight
  const [drifts, setDrifts] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  // The fetch is wrapped in useCallback so it has a stable identity across
  // renders and can be reused by both the initial useEffect and the Refresh
  // button without redefining it each time.
  const loadDrifts = useCallback(() => {
    setLoading(true)
    setError(null)

    return fetch('/drifts')
      .then((response) => {
        if (!response.ok) {
          throw new Error(`API returned ${response.status}`)
        }
        return response.json()
      })
      .then((data) => {
        setDrifts(data)
        setLoading(false)
      })
      .catch((err) => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  // useEffect runs after the component first appears on screen. The empty
  // array [] means "only run once on mount" — what we want for an initial fetch.
  useEffect(() => {
    loadDrifts()
  }, [loadDrifts])

  return (
    <div className="dashboard">
      <header className="dashboard__header">
        <div className="dashboard__title-group">
          <h1 className="dashboard__title">netdrift</h1>
          <span className="dashboard__subtitle">
            drift events
            {drifts && (
              <> · <span className="dashboard__count">{drifts.length}</span></>
            )}
          </span>
        </div>
        <button
          type="button"
          className="dashboard__refresh"
          onClick={loadDrifts}
          disabled={loading}
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      {loading && !drifts && (
        <p className="dashboard__state">Loading…</p>
      )}

      {error && (
        <p className="dashboard__state dashboard__state--error">
          Failed to load drifts: {error}
        </p>
      )}

      {!loading && !error && drifts && drifts.length === 0 && (
        <p className="dashboard__state">No drift events yet.</p>
      )}

      {drifts && drifts.length > 0 && (
        <table className="drift-table">
          <thead>
            <tr>
              <th>Device</th>
              <th>Object</th>
              <th>Field</th>
              <th>Intent</th>
              <th>Reality</th>
              <th>Kind</th>
              <th>Severity</th>
              <th>Detected at</th>
            </tr>
          </thead>
          <tbody>
            {drifts.map((d) => (
              <tr key={d.id} className={`sev-${d.severity}`}>
                <td>{d.device}</td>
                <td>{d.object}</td>
                <td>{d.field}</td>
                <td className="col-intent">{formatValue(d.intent)}</td>
                <td className="col-reality">{formatValue(d.reality)}</td>
                <td>{d.drift_kind}</td>
                <td className="col-severity">{d.severity}</td>
                <td className="col-detected">{d.detected_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// Helper: intent/reality can be strings, numbers, bools, lists, or null.
// Stringify so the table cell always shows something readable.
function formatValue(v) {
  if (v === null || v === undefined) return '—'
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}