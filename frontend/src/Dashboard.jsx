// Dashboard.jsx — drift events page with history panel and v2.5 remediation UI.

import { useState, useEffect, useCallback, Fragment } from 'react'
import './Dashboard.css'

export default function Dashboard() {
  const [drifts, setDrifts] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const [history, setHistory] = useState(null)
  const [historyError, setHistoryError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [recordingFor, setRecordingFor] = useState(null)

  // v2.5 remediation state
  const [dryRunFor, setDryRunFor] = useState(null)       // {drift, issueId}
  const [dryRunResult, setDryRunResult] = useState(null)  // API response
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [dryRunError, setDryRunError] = useState(null)
  const [applyLoading, setApplyLoading] = useState(false)
  const [applyError, setApplyError] = useState(null)
  const [auditLogFor, setAuditLogFor] = useState(null)   // issueId
  const [auditLog, setAuditLog] = useState([])
  const [auditLogLoading, setAuditLogLoading] = useState(false)

  const loadDrifts = useCallback(() => {
    setLoading(true)
    setError(null)
    return fetch('/drifts')
      .then((r) => { if (!r.ok) throw new Error(`API returned ${r.status}`); return r.json() })
      .then((data) => { setDrifts(data); setLoading(false) })
      .catch((err) => { setError(err.message); setLoading(false) })
  }, [])

  const loadHistory = useCallback(() => {
    setHistoryError(null)
    return fetch('/drifts/history')
      .then((r) => { if (!r.ok) throw new Error(`API returned ${r.status}`); return r.json() })
      .then((data) => setHistory(data))
      .catch((err) => setHistoryError(err.message))
  }, [])

  useEffect(() => { loadDrifts(); loadHistory() }, [loadDrifts, loadHistory])

  const handleRefresh = useCallback(() => { loadDrifts(); loadHistory() }, [loadDrifts, loadHistory])

  const handleSubmitFix = useCallback((drift, cause, fix) => {
    fetch('/known-issues', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ object: drift.object, field: drift.field, drift_kind: drift.drift_kind, cause, fix }),
    })
      .then((r) => { if (!r.ok) throw new Error(`API returned ${r.status}`) })
      .then(() => { setRecordingFor(null); loadDrifts() })
      .catch(() => setRecordingFor(null))
  }, [loadDrifts])

  // v2.5 — initiate a dry run for a drift event
  const handleDryRun = useCallback((drift) => {
    const issueId = drift.known_fix?.id
    if (!issueId) return
    setDryRunFor({ drift, issueId })
    setDryRunResult(null)
    setDryRunError(null)
    setDryRunLoading(true)
    fetch(`/known-issues/${issueId}/remediate/dry-run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ drift_event_id: drift.id }),
    })
      .then((r) => { if (!r.ok) return r.json().then((e) => { throw new Error(e.detail || `API returned ${r.status}`) }); return r.json() })
      .then((data) => { setDryRunResult(data); setDryRunLoading(false) })
      .catch((err) => { setDryRunError(err.message); setDryRunLoading(false) })
  }, [])

  // v2.5 — confirm and apply the fix
  const handleApply = useCallback(() => {
    if (!dryRunFor) return
    const { drift, issueId } = dryRunFor
    setApplyLoading(true)
    setApplyError(null)
    fetch(`/known-issues/${issueId}/remediate/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ drift_event_id: drift.id }),
    })
      .then((r) => { if (!r.ok) return r.json().then((e) => { throw new Error(e.detail || `API returned ${r.status}`) }); return r.json() })
      .then(() => { setDryRunFor(null); setDryRunResult(null); setApplyLoading(false); loadDrifts() })
      .catch((err) => { setApplyError(err.message); setApplyLoading(false) })
  }, [dryRunFor, loadDrifts])

  // v2.5 — load audit log for a known issue
  const handleShowAuditLog = useCallback((issueId) => {
    if (auditLogFor === issueId) { setAuditLogFor(null); return }
    setAuditLogFor(issueId)
    setAuditLogLoading(true)
    fetch(`/known-issues/${issueId}/remediation-events`)
      .then((r) => { if (!r.ok) throw new Error(`API returned ${r.status}`); return r.json() })
      .then((data) => { setAuditLog(data); setAuditLogLoading(false) })
      .catch(() => { setAuditLog([]); setAuditLogLoading(false) })
  }, [auditLogFor])

  return (
    <div className="dashboard">
      <header className="dashboard__header">
        <div className="dashboard__title-group">
          <h1 className="dashboard__title">netdrift</h1>
          <span className="dashboard__subtitle">
            drift events
            {drifts && <> · <span className="dashboard__count">{drifts.length}</span></>}
          </span>
        </div>
        <button type="button" className="dashboard__refresh" onClick={handleRefresh} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      {history && history.length > 0 && <HistoryPanel history={history} />}
      {historyError && <p className="dashboard__state dashboard__state--error">History unavailable: {historyError}</p>}
      {loading && !drifts && <p className="dashboard__state">Loading…</p>}
      {error && <p className="dashboard__state dashboard__state--error">Failed to load drifts: {error}</p>}
      {!loading && !error && drifts && drifts.length === 0 && <p className="dashboard__state">No drift events yet.</p>}

      {drifts && drifts.length > 0 && (
        <table className="drift-table">
          <thead>
            <tr>
              <th>Device</th><th>Object</th><th>Field</th>
              <th>Intent</th><th>Reality</th><th>Kind</th>
              <th>Severity</th><th>Detected at</th><th></th>
            </tr>
          </thead>
          <tbody>
            {drifts.map((d) => {
              const causes = d.causes || []
              const hasContent = causes.length > 0 || !!d.known_fix
              const isExpanded = expandedId === d.id
              const hasExecutableFix = d.known_fix?.remediation?.kind != null
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
                    <td className="col-expand">{hasContent ? (isExpanded ? '▾' : '▸') : ''}</td>
                  </tr>

                  {isExpanded && hasContent && (
                    <tr className="causes-row">
                      <td colSpan={9}>
                        {d.known_fix && (
                          <div className="known-fix">
                            <span className="known-fix__label">known fix</span>
                            <p className="known-fix__text"><strong>Cause:</strong> {d.known_fix.cause}</p>
                            <p className="known-fix__text"><strong>Fix:</strong> {d.known_fix.fix}</p>

                            {/* v2.5 — remediation controls */}
                            <div className="remediation-controls">
                              {hasExecutableFix && (
                                <button
                                  type="button"
                                  className="dry-run-btn"
                                  onClick={(e) => { e.stopPropagation(); handleDryRun(d) }}
                                >
                                  Dry run
                                </button>
                              )}
                              <button
                                type="button"
                                className="audit-log-btn"
                                onClick={(e) => { e.stopPropagation(); handleShowAuditLog(d.known_fix.id) }}
                              >
                                {auditLogFor === d.known_fix.id ? 'Hide audit log' : 'Audit log'}
                              </button>
                              <span className="confirmed-count">
                                {d.known_fix.confirmed_count} confirmed fix{d.known_fix.confirmed_count !== 1 ? 'es' : ''}
                              </span>
                            </div>

                            {auditLogFor === d.known_fix.id && (
                              <RemediationAuditLog
                                events={auditLog}
                                loading={auditLogLoading}
                              />
                            )}
                          </div>
                        )}

                        {causes.length > 0 && (
                          <ul className="causes-list">
                            {causes.map((c, i) => <li key={i}>{c}</li>)}
                          </ul>
                        )}

                        {!d.known_fix && (
                          <button
                            type="button"
                            className="record-fix-btn"
                            onClick={(e) => { e.stopPropagation(); setRecordingFor(d) }}
                          >
                            Record fix
                          </button>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}

      {recordingFor && (
        <RecordFixModal
          drift={recordingFor}
          onSubmit={(cause, fix) => handleSubmitFix(recordingFor, cause, fix)}
          onCancel={() => setRecordingFor(null)}
        />
      )}

      {/* v2.5 — dry-run modal */}
      {dryRunFor && (
        <DryRunModal
          drift={dryRunFor.drift}
          loading={dryRunLoading}
          result={dryRunResult}
          error={dryRunError}
          applyLoading={applyLoading}
          applyError={applyError}
          onApply={handleApply}
          onCancel={() => { setDryRunFor(null); setDryRunResult(null); setApplyError(null) }}
        />
      )}
    </div>
  )
}

// RecordFixModal — overlay form for recording a cause and fix for a drift pattern.
function RecordFixModal({ drift, onSubmit, onCancel }) {
  const [cause, setCause] = useState('')
  const [fix, setFix] = useState('')
  const pattern = `${drift.object.split(':')[0]} · ${drift.field} · ${drift.drift_kind}`
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal__title">Record fix</h2>
        <p className="modal__pattern">{pattern}</p>
        <label className="modal__label">
          Cause
          <textarea className="modal__textarea" value={cause} onChange={(e) => setCause(e.target.value)}
            placeholder="What caused this drift?" rows={3} />
        </label>
        <label className="modal__label">
          Fix
          <textarea className="modal__textarea" value={fix} onChange={(e) => setFix(e.target.value)}
            placeholder="How was it resolved?" rows={3} />
        </label>
        <div className="modal__actions">
          <button type="button" className="modal__submit" onClick={() => onSubmit(cause, fix)} disabled={!cause.trim() || !fix.trim()}>
            Save
          </button>
          <button type="button" className="modal__cancel" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

// DryRunModal — shows the candidate diff and lets the user confirm or cancel.
function DryRunModal({ drift, loading, result, error, applyLoading, applyError, onApply, onCancel }) {
  const pattern = `${drift.object.split(':')[0]} · ${drift.field} · ${drift.drift_kind}`
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal__title">Dry run — {pattern}</h2>

        {loading && <p className="modal__state">Running dry-run on device…</p>}
        {error && <p className="modal__state modal__state--error">Dry-run failed: {error}</p>}

        {result && (
          <>
            <p className="modal__label">Commands that would be sent ({result.transport}):</p>
            <pre className="modal__code">{result.rendered_commands || '(none)'}</pre>
            <p className="modal__label">Candidate diff:</p>
            <pre className="modal__code modal__code--diff">{result.dry_run_diff || '(no diff — device may already match intent)'}</pre>
          </>
        )}

        {applyError && <p className="modal__state modal__state--error">Apply failed: {applyError}</p>}

        <div className="modal__actions">
          {result && (
            <button type="button" className="modal__submit" onClick={onApply} disabled={applyLoading}>
              {applyLoading ? 'Applying…' : 'Apply'}
            </button>
          )}
          <button type="button" className="modal__cancel" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

// RemediationAuditLog — table of past apply/dry-run events for a known issue.
function RemediationAuditLog({ events, loading }) {
  if (loading) return <p className="audit-log__state">Loading audit log…</p>
  if (!events.length) return <p className="audit-log__state">No remediation events recorded yet.</p>
  return (
    <div className="audit-log">
      <table className="audit-log__table">
        <thead>
          <tr>
            <th>When</th><th>Platform</th><th>Result</th><th>By</th>
          </tr>
        </thead>
        <tbody>
          {events.map((ev) => (
            <tr key={ev.id} className={`audit-row audit-row--${ev.result}`}>
              <td>{ev.applied_at}</td>
              <td>{ev.platform}</td>
              <td>{ev.result}</td>
              <td>{ev.applied_by}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// HistoryPanel — one sparkline row per device over the last 24 hours.
function HistoryPanel({ history }) {
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
          <DeviceHistory key={device} device={device} buckets={deviceMap[device]} maxCount={maxCount} />
        ))}
      </div>
    </section>
  )
}

function DeviceHistory({ device, buckets, maxCount }) {
  const latest = buckets[buckets.length - 1]
  const currentCount = latest ? latest.count : 0
  return (
    <div className="device-history">
      <span className="device-history__name">{device}</span>
      <div className="device-history__bars" role="img" aria-label={`drift history for ${device}`}>
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

function worstSeverity(bucket) {
  if (bucket.critical > 0) return 'critical'
  if (bucket.warning > 0) return 'warning'
  return 'info'
}

function formatValue(v) {
  if (v === null || v === undefined) return '—'
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}
