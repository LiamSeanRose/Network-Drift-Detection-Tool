// Dashboard.jsx — drift events page with history panel.
//
// Fetches /drifts (event table) and /drifts/history (trend chart) in parallel
// on mount and on each Refresh. The history panel renders above the table when
// the API returns at least one bucket; it is hidden when history is empty.

import { useState, useEffect, useCallback, Fragment } from 'react'
import './Dashboard.css'

export default function Dashboard() {
  const [drifts, setDrifts] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const [history, setHistory] = useState(null)
  const [historyError, setHistoryError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)

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

  const loadHistory = useCallback(() => {
    setHistoryError(null)

    return fetch('/drifts/history')
      .then((response) => {
        if (!response.ok) {
          throw new Error(`API returned ${response.status}`)
        }
        return response.json()
      })
      .then((data) => setHistory(data))
      .catch((err) => setHistoryError(err.message))
  }, [])

  useEffect(() => {
    loadDrifts()
    loadHistory()
  }, [loadDrifts, loadHistory])

  const handleRefresh = useCallback(() => {
    loadDrifts()
    loadHistory()
  }, [loadDrifts, loadHistory])

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
          onClick={handleRefresh}
          disabled={loading}
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      {history && history.length > 0 && (
        <HistoryPanel history={history} />
      )}

      {historyError && (
        <p className="dashboard__state dashboard__state--error">
          History unavailable: {historyError}
        </p>
      )}

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
              <th></th>
            </tr>
          </thead>
          <tbody>
            {drifts.map((d) => {
              const causes = d.causes || []
              const isExpanded = expandedId === d.id
              return (
                <Fragment key={d.id}>
                  <tr
                    className={`sev-${d.severity} expandable`}
                    onClick={() => setExpandedId(isExpanded ? null : d.id)}
                  >
                    <td>{d.device}</td>
                    <td>{d.object}</td>
                    <td>{d.field}</td>
                    <td className="col-intent">{formatValue(d.intent)}</td>
                    <td className="col-reality">{formatValue(d.reality)}</td>
                    <td>{d.drift_kind}</td>
                    <td className="col-severity">{d.severity}</td>
                    <td className="col-detected">{d.detected_at}</td>
                    <td className="col-expand">
                      {causes.length > 0 ? (isExpanded ? '▾' : '▸') : ''}
                    </td>
                  </tr>
                  {isExpanded && causes.length > 0 && (
                    <tr className="causes-row">
                      <td colSpan={9}>
                        <ul className="causes-list">
                          {causes.map((c, i) => (
                            <li key={i}>{c}</li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

// HistoryPanel — one sparkline row per device over the last 24 hours.
//
// `history` is the array from GET /drifts/history:
//   [{detected_at, device, count, critical, warning, info}, ...]
//
// Bars within each device row are scaled relative to the highest count seen
// across *all* devices so that the severity of one device can be compared
// visually against another.
function HistoryPanel({ history }) {
  // Group buckets by device, preserving arrival order (already oldest-first).
  const deviceMap = {}
  for (const entry of history) {
    if (!deviceMap[entry.device]) deviceMap[entry.device] = []
    deviceMap[entry.device].push(entry)
  }

  const devices = Object.keys(deviceMap).sort()
  const maxCount = Math.max(...history.map((h) => h.count), 1)

  return (
    <section className="history-panel">
      <h2 className="history-panel__heading">drift history · last 24 h</h2>
      <div className="history-panel__devices">
        {devices.map((device) => (
          <DeviceHistory
            key={device}
            device={device}
            buckets={deviceMap[device]}
            maxCount={maxCount}
          />
        ))}
      </div>
    </section>
  )
}

// DeviceHistory — device name + bar sparkline + current count.
function DeviceHistory({ device, buckets, maxCount }) {
  const latest = buckets[buckets.length - 1]
  const currentCount = latest ? latest.count : 0

  return (
    <div className="device-history">
      <span className="device-history__name">{device}</span>
      <div
        className="device-history__bars"
        role="img"
        aria-label={`drift history for ${device}`}
      >
        {buckets.map((b) => (
          <div
            key={b.detected_at}
            className={`history-bar history-bar--${worstSeverity(b)}`}
            style={{ height: `${Math.max((b.count / maxCount) * 100, 4)}%` }}
            title={`${b.detected_at}: ${b.count} drift(s)`}
          />
        ))}
      </div>
      <span className="device-history__count">{currentCount}</span>
    </div>
  )
}

// Return the worst severity present in a history bucket.
function worstSeverity(bucket) {
  if (bucket.critical > 0) return 'critical'
  if (bucket.warning > 0) return 'warning'
  return 'info'
}

// Helper: intent/reality can be strings, numbers, bools, lists, or null.
function formatValue(v) {
  if (v === null || v === undefined) return '—'
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}
