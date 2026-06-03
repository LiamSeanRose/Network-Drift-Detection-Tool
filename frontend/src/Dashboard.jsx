// Dashboard.jsx — drift events page with history panel and v2.5 remediation UI.

import { useState, useEffect, useCallback, useMemo, useRef, Fragment } from 'react'
import { apiFetch, getApiKey, setApiKey } from './api'
import { GLOSSARY, SEVERITY_LEGEND } from './help'
import './Dashboard.css'

const HELP_BANNER_KEY = 'netdrift_help_banner_dismissed'

export default function Dashboard({ initialFilter = null }) {
  const [drifts, setDrifts] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const [history, setHistory] = useState(null)
  const [historyError, setHistoryError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [recordingFor, setRecordingFor] = useState(null)

  // v3.5 — API key for mutating requests (record fix, dry-run, apply, …).
  // Read-only views work without it; writes need a key minted via
  // `driftcheck create-api-key`. Stored in localStorage by api.setApiKey.
  const [apiKey, setApiKeyState] = useState(getApiKey)

  // v3.5 — SLA alert rules
  const [alertRules, setAlertRules] = useState(null)

  // v3.5 — devices + per-device auto-apply pause state
  const [devices, setDevices] = useState(null)

  // Inventory-accuracy headline: share of the inventory whose live reality
  // matches its intended state. Self-guarding — the banner renders nothing
  // until a well-formed payload arrives.
  const [accuracy, setAccuracy] = useState(null)

  // maintenance windows — planned change windows that suppress alerting
  const [maintenanceWindows, setMaintenanceWindows] = useState(null)

  // Surfaced when a write fails. Mutating requests need an API key; without one
  // they 401, and the handlers used to swallow that — every button looked dead.
  const [actionError, setActionError] = useState(null)

  // In-app help: the glossary modal and the dismissible intent-vs-reality banner.
  const [helpOpen, setHelpOpen] = useState(false)
  const [bannerDismissed, setBannerDismissed] = useState(
    () => localStorage.getItem(HELP_BANNER_KEY) === '1')

  const dismissBanner = useCallback(() => {
    setBannerDismissed(true)
    localStorage.setItem(HELP_BANNER_KEY, '1')
  }, [])

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

  const loadAlertRules = useCallback(() => {
    return apiFetch('/alert-rules')
      .then((r) => { if (!r.ok) throw new Error(`API returned ${r.status}`); return r.json() })
      .then((data) => setAlertRules(data))
      .catch(() => setAlertRules([]))
  }, [])

  const loadDevices = useCallback(() => {
    return apiFetch('/devices')
      .then((r) => { if (!r.ok) throw new Error(`API returned ${r.status}`); return r.json() })
      .then((data) => setDevices(data))
      .catch(() => setDevices([]))
  }, [])

  const loadMaintenanceWindows = useCallback(() => {
    return apiFetch('/maintenance-windows')
      .then((r) => { if (!r.ok) throw new Error(`API returned ${r.status}`); return r.json() })
      .then((data) => setMaintenanceWindows(data))
      .catch(() => setMaintenanceWindows([]))
  }, [])

  const loadAccuracy = useCallback(() => {
    return fetch('/inventory-accuracy')
      .then((r) => { if (!r.ok) throw new Error(`API returned ${r.status}`); return r.json() })
      .then((data) => setAccuracy(data))
      .catch(() => setAccuracy(null))
  }, [])

  // Initial load on mount. The loaders set a loading flag synchronously, which
  // the fetch-on-mount pattern requires; that is intentional here.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadDrifts(); loadHistory(); loadAlertRules(); loadDevices(); loadMaintenanceWindows(); loadAccuracy() },
    [loadDrifts, loadHistory, loadAlertRules, loadDevices, loadMaintenanceWindows, loadAccuracy])

  const handleRefresh = useCallback(
    () => { loadDrifts(); loadHistory(); loadAlertRules(); loadDevices(); loadMaintenanceWindows(); loadAccuracy() },
    [loadDrifts, loadHistory, loadAlertRules, loadDevices, loadMaintenanceWindows, loadAccuracy])

  // Run a mutating request and surface failures instead of swallowing them. A
  // 401 means no/invalid API key — the single most common cause of a write
  // appearing to do nothing — so it gets a specific, actionable message.
  const mutate = useCallback((request, onOk) => {
    return request
      .then((r) => {
        if (r.ok) { setActionError(null); return onOk && onOk() }
        if (r.status === 401) {
          setActionError(
            'API key required or invalid. Paste a key (from `driftcheck ' +
            'create-api-key`) into the field at the top right, then retry.')
        } else {
          setActionError(`Request failed (HTTP ${r.status}).`)
        }
      })
      .catch(() => setActionError('Could not reach the API.'))
  }, [])

  const handleToggleDevice = useCallback((device) => {
    return mutate(apiFetch(`/devices/${device.name}/auto-apply`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused: !device.auto_remediation_paused }),
    }), loadDevices)
  }, [mutate, loadDevices])

  const handleAddRule = useCallback((device, severity, windowMinutes, objectType) => {
    return mutate(apiFetch('/alert-rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device: device || null, severity, window_minutes: Number(windowMinutes),
        object_type: objectType || null,
      }),
    }), loadAlertRules)
  }, [mutate, loadAlertRules])

  const handleDeleteRule = useCallback((id) => {
    return mutate(apiFetch(`/alert-rules/${id}`, { method: 'DELETE' }), loadAlertRules)
  }, [mutate, loadAlertRules])

  const handleAddWindow = useCallback((device, startsAt, endsAt, reason) => {
    return mutate(apiFetch('/maintenance-windows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device: device || null, starts_at: startsAt, ends_at: endsAt,
        reason: reason || null,
      }),
    }), loadMaintenanceWindows)
  }, [mutate, loadMaintenanceWindows])

  const handleDeleteWindow = useCallback((id) => {
    return mutate(apiFetch(`/maintenance-windows/${id}`, { method: 'DELETE' }),
      loadMaintenanceWindows)
  }, [mutate, loadMaintenanceWindows])

  const handleSubmitFix = useCallback((drift, cause, fix) => {
    return mutate(apiFetch('/known-issues', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ object: drift.object, field: drift.field, drift_kind: drift.drift_kind, cause, fix }),
    }), () => { setRecordingFor(null); loadDrifts() })
  }, [mutate, loadDrifts])

  // v2.5 — initiate a dry run for a drift event
  const handleDryRun = useCallback((drift) => {
    const issueId = drift.known_fix?.id
    if (!issueId) return
    setDryRunFor({ drift, issueId })
    setDryRunResult(null)
    setDryRunError(null)
    setDryRunLoading(true)
    apiFetch(`/known-issues/${issueId}/remediate/dry-run`, {
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
    apiFetch(`/known-issues/${issueId}/remediate/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ drift_event_id: drift.id }),
    })
      .then((r) => { if (!r.ok) return r.json().then((e) => { throw new Error(e.detail || `API returned ${r.status}`) }); return r.json() })
      .then(() => { setDryRunFor(null); setDryRunResult(null); setApplyLoading(false); loadDrifts() })
      .catch((err) => { setApplyError(err.message); setApplyLoading(false) })
  }, [dryRunFor, loadDrifts])

  // v3.5 — toggle a drift acknowledgement (POST to acknowledge, DELETE to clear)
  const handleToggleAck = useCallback((d) => {
    const req = d.acknowledged
      ? apiFetch(`/drifts/${d.id}/acknowledge`, { method: 'DELETE' })
      : apiFetch(`/drifts/${d.id}/acknowledge`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ acknowledged_until: null }),
        })
    return mutate(req, loadDrifts)
  }, [mutate, loadDrifts])

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

  // When the fleet-health home deep-links here with a severity/device filter,
  // narrow the already-fetched list client-side — no extra API call. With no
  // filter (the standalone drift console) visibleDrifts is just drifts, so all
  // existing behaviour is unchanged.
  const visibleDrifts = useMemo(() => {
    if (!drifts || !initialFilter) return drifts
    return drifts.filter((d) => {
      if (initialFilter.severity && d.severity !== initialFilter.severity) return false
      if (initialFilter.device && d.device !== initialFilter.device) return false
      return true
    })
  }, [drifts, initialFilter])

  const filterLabel = initialFilter
    ? [initialFilter.severity, initialFilter.device].filter(Boolean).join(' · ')
    : null

  return (
    <div className="dashboard">
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <header className="dashboard__header">
        <div className="dashboard__title-group">
          <span className="brand__glyph" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="#0b0f17" strokeWidth="2.4"
              strokeLinecap="round" strokeLinejoin="round">
              <circle cx="5" cy="6" r="2" />
              <circle cx="19" cy="6" r="2" />
              <circle cx="12" cy="18" r="2" />
              <path d="M7 6h10M6 8l5 8M18 8l-5 8" />
            </svg>
          </span>
          <h1 className="dashboard__title">netdrift</h1>
          <span className="dashboard__subtitle">
            drift console
            {visibleDrifts && <> · <span className="dashboard__count">{visibleDrifts.length}</span> events</>}
          </span>
        </div>
        <div className="dashboard__actions">
          <a href="#" className="dashboard__overview-link">← Overview</a>
          <input
            type="password"
            className="dashboard__apikey"
            placeholder="API key"
            aria-label="API key"
            value={apiKey}
            onChange={(e) => { setApiKeyState(e.target.value); setApiKey(e.target.value) }}
          />
          <button type="button" className="dashboard__refresh" onClick={handleRefresh} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          <button type="button" className="dashboard__help-btn" onClick={() => setHelpOpen(true)}>
            Help
          </button>
        </div>
      </header>

      <main id="main-content">
      {!bannerDismissed && (
        <div className="intent-banner">
          <p className="intent-banner__text">
            <strong>What am I looking at?</strong> netdrift compares your{' '}
            <em>intent</em> (what NetBox says the network should be) against{' '}
            <em>reality</em> (what each device reports live). Every row below is a{' '}
            <em>drift</em> — a difference between the two.{' '}
            <button type="button" className="intent-banner__link" onClick={() => setHelpOpen(true)}>
              Open the glossary
            </button>{' '}
            for any term.
          </p>
          <button
            type="button"
            className="intent-banner__dismiss"
            aria-label="dismiss help banner"
            onClick={dismissBanner}
          >
            ×
          </button>
        </div>
      )}

      {helpOpen && <HelpPanel onClose={() => setHelpOpen(false)} />}

      {actionError && (
        <div className="dashboard__action-error" role="alert">
          <span>{actionError}</span>
          <button
            type="button"
            className="dashboard__action-error-dismiss"
            aria-label="dismiss error"
            onClick={() => setActionError(null)}
          >
            ×
          </button>
        </div>
      )}

      <AccuracyBanner accuracy={accuracy} />

      {visibleDrifts && visibleDrifts.length > 0 && (
        <StatsBar drifts={visibleDrifts} devices={devices} windows={maintenanceWindows} />
      )}

      {filterLabel && (
        <p className="dashboard__filter-note">
          Showing <strong>{filterLabel}</strong> drift only.{' '}
          <a href="#drifts">Clear filter</a>
        </p>
      )}

      <h2 className="section-title">configuration</h2>
      <div className="panel-grid">
        <AlertRulesPanel rules={alertRules} onAdd={handleAddRule} onDelete={handleDeleteRule} />
        <MaintenanceWindowsPanel windows={maintenanceWindows} onAdd={handleAddWindow} onDelete={handleDeleteWindow} />
        <DevicesPanel devices={devices} onToggle={handleToggleDevice} />
      </div>

      {history && history.length > 0 && <HistoryPanel history={history} />}
      {historyError && <p className="dashboard__state dashboard__state--error">History unavailable: {historyError}</p>}
      {loading && !drifts && <p className="dashboard__state">Loading…</p>}
      {error && <p className="dashboard__state dashboard__state--error">Failed to load drifts: {error}</p>}
      {!loading && !error && visibleDrifts && visibleDrifts.length === 0 && <p className="dashboard__state">No drift events yet.</p>}

      {visibleDrifts && visibleDrifts.length > 0 && (
        <>
        <h2 className="section-title">drift events</h2>
        <div className="table-card">
        <table className="drift-table">
          <thead>
            <tr>
              <th scope="col">Device</th><th scope="col">Object</th><th scope="col">Field</th>
              <th scope="col">Intent</th><th scope="col">Reality</th><th scope="col">Kind</th>
              <th scope="col">Severity</th><th scope="col">Detected at</th>
              <th scope="col" aria-label="Details" />
            </tr>
          </thead>
          <tbody>
            {visibleDrifts.map((d) => {
              const causes = d.causes || []
              const isExpanded = expandedId === d.id
              const hasExecutableFix = d.known_fix?.remediation?.kind != null
              return (
                <Fragment key={d.id}>
                  <tr
                    className={`sev-${d.severity} expandable${isExpanded ? ' is-open' : ''}${d.acknowledged ? ' acknowledged' : ''}`}
                    onClick={() => setExpandedId(isExpanded ? null : d.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setExpandedId(isExpanded ? null : d.id)
                      }
                    }}
                    tabIndex={0}
                    role="button"
                    aria-expanded={isExpanded}
                    aria-label={`${d.device} ${d.object} ${d.field} — ${isExpanded ? 'collapse' : 'expand'} details`}
                  >
                    <td>{d.device}</td>
                    <td className="col-object" title={d.object}>
                      {d.object}
                      {d.triage === 'new' && <span className="triage-badge">new</span>}
                    </td>
                    <td>{d.field}</td>
                    <td className="col-intent">{formatValue(d.intent)}</td>
                    <td className="col-reality">{formatValue(d.reality)}</td>
                    <td>{d.drift_kind}</td>
                    <td className="col-severity">
                      <span className={`sev-pill sev-pill--${d.severity}`}>{d.severity}</span>
                    </td>
                    <td className="col-detected">{formatTimestamp(d.detected_at)}</td>
                    <td className="col-expand">
                      <button
                        type="button"
                        className="expand-btn"
                        aria-expanded={isExpanded}
                        aria-label={`${isExpanded ? 'Hide' : 'Show'} details for ${d.device} ${d.object}`}
                        onClick={(e) => { e.stopPropagation(); setExpandedId(isExpanded ? null : d.id) }}
                      >
                        <span className="chevron" aria-hidden="true">▸</span>
                      </button>
                    </td>
                  </tr>

                  {isExpanded && (
                    <tr className="causes-row">
                      <td colSpan={9}>
                        <div className="ack-controls">
                          <button
                            type="button"
                            className="ack-btn"
                            onClick={(e) => { e.stopPropagation(); handleToggleAck(d) }}
                          >
                            {d.acknowledged ? 'Unacknowledge' : 'Acknowledge'}
                          </button>
                          {d.acknowledged && <span className="ack-note">acknowledged — alerts suppressed</span>}
                        </div>
                        {d.first_seen && (
                          <p className="drift-meta">
                            <span className={`triage-tag triage-tag--${d.triage}`}>{d.triage}</span>
                            first seen {formatTimestamp(d.first_seen)}
                          </p>
                        )}
                        <TriageBlock driftId={d.id} />
                        {d.known_fix && (
                          <div className="known-fix">
                            <span className="known-fix__label">known fix</span>
                            <MatchConfidenceBadge confidence={d.match_confidence} />
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

                        {d.explanation && (
                          <div className="ai-explanation">
                            <span className="ai-explanation__label">
                              AI explanation{d.explanation.source === 'deterministic' ? ' (offline)' : ''}
                            </span>
                            <p className="ai-explanation__text">{d.explanation.text}</p>
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
        </div>
        </>
      )}

      </main>

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

// Current wall-clock in ms. Wrapped so call sites read the time at render to
// decide which maintenance windows are active *now*; kept out of the component
// body so it is an intentional, isolated impurity rather than inline noise.
function nowMs() {
  return Date.now()
}

// Elements that can hold keyboard focus — used to find where to send focus when
// a dialog opens and to wrap Tab around inside it.
const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

// ModalShell — the accessible plumbing shared by every modal: dialog semantics
// (role + aria-modal + a label), focus moved inside on open and restored to the
// trigger on close, Escape-to-close, and a Tab focus-trap. Each modal supplies
// its own labelled title via `labelledBy`; behaviour and chrome live here so the
// three modals do not each re-implement (and skip) accessibility.
function ModalShell({ labelledBy, onClose, className = '', children }) {
  const dialogRef = useRef(null)

  useEffect(() => {
    const node = dialogRef.current
    const previouslyFocused = document.activeElement

    // Move focus to the first focusable control (or the dialog itself).
    const focusables = node.querySelectorAll(FOCUSABLE_SELECTOR)
    ;(focusables[0] || node).focus()

    function onKeyDown(e) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== 'Tab') return
      const items = node.querySelectorAll(FOCUSABLE_SELECTOR)
      if (items.length === 0) { e.preventDefault(); return }
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    node.addEventListener('keydown', onKeyDown)
    return () => {
      node.removeEventListener('keydown', onKeyDown)
      // Restore focus to whatever opened the dialog.
      if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
        previouslyFocused.focus()
      }
    }
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className={`modal ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}

// HelpPanel — the in-app glossary + severity legend. A finite reference so an
// operator does not have to leave the app to look up a term.
function HelpPanel({ onClose }) {
  return (
    <ModalShell labelledBy="help-modal-title" onClose={onClose} className="modal--wide help-panel">
      <h2 id="help-modal-title" className="modal__title">Help &amp; glossary</h2>

        <h3 className="help-panel__subhead">Severity</h3>
        <ul className="help-panel__legend">
          {SEVERITY_LEGEND.map((s) => (
            <li key={s.level} className="help-panel__legend-item">
              <span className={`sev-pill sev-pill--${s.level}`}>{s.level}</span>
              <span className="help-panel__legend-text">{s.meaning}</span>
            </li>
          ))}
        </ul>

        <h3 className="help-panel__subhead">Glossary</h3>
        <dl className="help-panel__glossary">
          {GLOSSARY.map((g) => (
            <Fragment key={g.term}>
              <dt className="help-panel__term">{g.term}</dt>
              <dd className="help-panel__def">{g.definition}</dd>
            </Fragment>
          ))}
        </dl>

        <div className="modal__actions">
          <button type="button" className="modal__cancel" onClick={onClose}>Close</button>
        </div>
    </ModalShell>
  )
}

// MatchConfidenceBadge — how a known fix was matched to this drift (v5.0).
// 1.0 (or absent, the exact-only default) is an exact match; anything lower is a
// fuzzy match, and below 0.7 it carries a warning so a weak match is not trusted
// blindly.
function MatchConfidenceBadge({ confidence }) {
  if (confidence == null || confidence >= 1) {
    return <span className="match-badge match-badge--exact">exact match</span>
  }
  const pct = Math.round(confidence * 100)
  const weak = confidence < 0.7
  return (
    <span className={`match-badge match-badge--fuzzy${weak ? ' match-badge--weak' : ''}`}>
      {weak && <span aria-hidden="true">⚠ </span>}
      fuzzy match · {pct}%
    </span>
  )
}

// TriageBlock — AI4 anomaly triage. Fetches an incident-vs-noise assessment for
// one drift when its row expands. Read-only and self-contained; shows the
// deterministic assessment by default and an LLM one when a provider is set.
function TriageBlock({ driftId }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(`/drifts/${driftId}/triage`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled && d) setData(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [driftId])

  if (!data) return null
  return (
    <div className="triage-block">
      <span className="triage-block__label">
        anomaly triage{data.source === 'deterministic' ? ' (offline)' : ''}
      </span>
      <p className="triage-block__text">
        <span className={`assessment assessment--${data.assessment}`}>{data.assessment}</span>
        {data.rationale}
      </p>
    </div>
  )
}

// RecordFixModal — overlay form for recording a cause and fix for a drift pattern.
function RecordFixModal({ drift, onSubmit, onCancel }) {
  const [cause, setCause] = useState('')
  const [fix, setFix] = useState('')
  const [suggested, setSuggested] = useState(false)
  const pattern = `${drift.object.split(':')[0]} · ${drift.field} · ${drift.drift_kind}`

  // AI3: pre-fill the form with a suggested cause/fix the user can edit. Only
  // fills fields the user has not already typed into.
  useEffect(() => {
    let cancelled = false
    apiFetch(`/drifts/${drift.id}/suggested-fix`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return
        setCause((c) => c || data.cause || '')
        setFix((f) => f || data.fix || '')
        setSuggested(true)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [drift.id])

  return (
    <ModalShell labelledBy="record-fix-title" onClose={onCancel}>
        <h2 id="record-fix-title" className="modal__title">Record fix</h2>
        <p className="modal__pattern">{pattern}</p>
        {suggested && <p className="modal__hint">AI-suggested — review and edit before saving.</p>}
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
    </ModalShell>
  )
}

// DryRunModal — shows the candidate diff and lets the user confirm or cancel.
function DryRunModal({ drift, loading, result, error, applyLoading, applyError, onApply, onCancel }) {
  const pattern = `${drift.object.split(':')[0]} · ${drift.field} · ${drift.drift_kind}`
  return (
    <ModalShell labelledBy="dry-run-title" onClose={onCancel} className="modal--wide">
        <h2 id="dry-run-title" className="modal__title">Dry run — {pattern}</h2>

        {loading && <p className="modal__state">Running dry-run on device…</p>}
        {error && <p className="modal__state modal__state--error">Dry-run failed: {error}</p>}

        {result && (
          <>
            {result.summary && (
              <div className="ai-explanation">
                <span className="ai-explanation__label">
                  AI summary{result.summary.source === 'deterministic' ? ' (offline)' : ''}
                </span>
                <p className="ai-explanation__text">{result.summary.text}</p>
              </div>
            )}
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
    </ModalShell>
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
            <th scope="col">When</th><th scope="col">Platform</th>
            <th scope="col">Result</th><th scope="col">By</th>
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

// AlertRulesPanel — list SLA alert rules and add/delete them (v3.5).
function AlertRulesPanel({ rules, onAdd, onDelete }) {
  const [device, setDevice] = useState('')
  const [severity, setSeverity] = useState('critical')
  const [windowMinutes, setWindowMinutes] = useState(10)
  const [objectType, setObjectType] = useState('')

  const submit = (e) => {
    e.preventDefault()
    if (!windowMinutes || Number(windowMinutes) <= 0) return
    onAdd(device.trim(), severity, windowMinutes, objectType)
    setDevice('')
  }

  return (
    <section className="alert-rules">
      <h3 className="alert-rules__heading">
        alert rules
        {rules && rules.length > 0 && <span className="count-badge" aria-hidden="true">{rules.length}</span>}
      </h3>

      {rules && rules.length > 0 && (
        <ul className="alert-rules__list">
          {rules.map((r) => (
            <li key={r.id} className="alert-rules__item">
              <span className="alert-rules__rule">
                {r.device || 'all devices'} · {r.severity} · {r.window_minutes} min
                {r.object_type ? ` · ${r.object_type}` : ''}
                {r.enabled ? '' : ' · disabled'}
              </span>
              <button
                type="button"
                className="alert-rules__delete"
                onClick={() => onDelete(r.id)}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}

      <form className="alert-rules__form" onSubmit={submit}>
        <input
          className="alert-rules__device"
          placeholder="device (blank = all)"
          aria-label="device"
          value={device}
          onChange={(e) => setDevice(e.target.value)}
        />
        <select aria-label="severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="critical">critical</option>
          <option value="warning">warning</option>
          <option value="info">info</option>
        </select>
        <select aria-label="object type" value={objectType} onChange={(e) => setObjectType(e.target.value)}>
          <option value="">any type</option>
          <option value="interface">interface</option>
          <option value="vlan">vlan</option>
          <option value="bgp_neighbor">bgp_neighbor</option>
          <option value="ospf_adjacency">ospf_adjacency</option>
          <option value="tunnel">tunnel</option>
        </select>
        <input
          type="number"
          min="1"
          aria-label="window minutes"
          value={windowMinutes}
          onChange={(e) => setWindowMinutes(e.target.value)}
        />
        <button type="submit" className="alert-rules__add">Add rule</button>
      </form>
    </section>
  )
}

// MaintenanceWindowsPanel — open/list/cancel planned change windows that suppress
// SLA alerting and auto-apply while active. Reuses the alert-rules styles.
function MaintenanceWindowsPanel({ windows, onAdd, onDelete }) {
  const [device, setDevice] = useState('')
  const [startsAt, setStartsAt] = useState('')
  const [endsAt, setEndsAt] = useState('')
  const [reason, setReason] = useState('')

  const submit = (e) => {
    e.preventDefault()
    if (!startsAt || !endsAt) return
    // datetime-local is in the browser's local time; toISOString converts to UTC.
    onAdd(device.trim(), new Date(startsAt).toISOString(), new Date(endsAt).toISOString(), reason.trim())
    setDevice(''); setStartsAt(''); setEndsAt(''); setReason('')
  }

  const now = nowMs()
  return (
    <section className="alert-rules">
      <h3 className="alert-rules__heading">
        maintenance windows
        {windows && windows.length > 0 && <span className="count-badge" aria-hidden="true">{windows.length}</span>}
      </h3>

      {windows && windows.length > 0 && (
        <ul className="alert-rules__list">
          {windows.map((w) => {
            const active = new Date(w.starts_at).getTime() <= now && now < new Date(w.ends_at).getTime()
            return (
              <li key={w.id} className="alert-rules__item">
                <span className="alert-rules__rule">
                  {w.device || 'all devices'} · {new Date(w.starts_at).toLocaleString()} → {new Date(w.ends_at).toLocaleString()}
                  {active ? ' · active' : ''}{w.reason ? ` · ${w.reason}` : ''}
                </span>
                <button
                  type="button"
                  className="alert-rules__delete"
                  onClick={() => onDelete(w.id)}
                >
                  Cancel
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <form className="alert-rules__form" onSubmit={submit}>
        <input
          className="alert-rules__device"
          placeholder="device (blank = all)"
          aria-label="maintenance device"
          value={device}
          onChange={(e) => setDevice(e.target.value)}
        />
        <input
          type="datetime-local"
          aria-label="starts at"
          value={startsAt}
          onChange={(e) => setStartsAt(e.target.value)}
        />
        <input
          type="datetime-local"
          aria-label="ends at"
          value={endsAt}
          onChange={(e) => setEndsAt(e.target.value)}
        />
        <input
          className="alert-rules__device"
          placeholder="reason (optional)"
          aria-label="maintenance reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <button type="submit" className="alert-rules__add">Open window</button>
      </form>
    </section>
  )
}

// Liveness status for a device, from the reachability probe. Tri-state: the
// probe may not have run yet (reachable null/undefined). Text + a dot, never
// colour alone, so the status survives a colour-blind or greyscale view.
function reachabilityLabel(d) {
  if (d.reachable === true) return { cls: 'up', text: 'reachable' }
  if (d.reachable === false) return { cls: 'down', text: 'unreachable' }
  return { cls: 'unknown', text: 'not probed' }
}

function ReachabilityBadge({ device }) {
  const r = reachabilityLabel(device)
  const lastSeen = device.last_reachable_at
    ? `last seen ${formatTimestamp(device.last_reachable_at)}`
    : undefined
  return (
    <span className={`devices__reach devices__reach--${r.cls}`} title={lastSeen}>
      <span className="devices__reach-dot" aria-hidden="true" />
      {r.text}
    </span>
  )
}

// Warranty / end-of-life badge. Only renders when something needs attention
// (expiring or expired); a device with plenty of runway or no dates stays quiet
// so the panel isn't cluttered. Text + dot, never colour alone.
function LifecycleBadge({ device }) {
  const items = [
    { label: 'warranty', status: device.warranty_status, days: device.warranty_days_left },
    { label: 'EoL', status: device.eol_status, days: device.eol_days_left },
  ].filter((i) => i.status === 'expiring' || i.status === 'expired')
  if (items.length === 0) return null
  return (
    <>
      {items.map((i) => (
        <span key={i.label} className={`devices__lifecycle devices__lifecycle--${i.status}`}>
          <span className="devices__lifecycle-dot" aria-hidden="true" />
          {i.status === 'expired'
            ? `${i.label} expired`
            : `${i.label} expires in ${i.days}d`}
        </span>
      ))}
    </>
  )
}

// DevicesPanel — list inventory devices with reachability, lifecycle, and a
// per-device auto-apply toggle (v3.5).
function DevicesPanel({ devices, onToggle }) {
  if (!devices || devices.length === 0) return null
  return (
    <section className="devices">
      <h3 className="devices__heading">
        devices
        <span className="count-badge" aria-hidden="true">{devices.length}</span>
      </h3>
      <ul className="devices__list">
        {devices.map((d) => (
          <li key={d.name} className="devices__item">
            <span className="devices__name">{d.name}</span>
            <ReachabilityBadge device={d} />
            <LifecycleBadge device={d} />
            <span className={`devices__state${d.auto_remediation_paused ? ' devices__state--paused' : ''}`}>
              {d.auto_remediation_paused ? 'auto-apply paused' : 'auto-apply active'}
            </span>
            <button
              type="button"
              className="devices__toggle"
              onClick={() => onToggle(d)}
            >
              {d.auto_remediation_paused ? 'Resume' : 'Pause'}
            </button>
          </li>
        ))}
      </ul>
    </section>
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
            role="img"
            aria-label={`${b.detected_at}: ${b.count} drift(s), worst severity ${worstSeverity(b)}`}
            title={`${b.detected_at}: ${b.count} drift(s) · ${worstSeverity(b)}`}
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

// Render an ISO timestamp as a compact, readable local time. Falls back to the
// raw string if it does not parse, so unexpected formats never blank the cell.
function formatTimestamp(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

// AccuracyBanner — the headline "is your inventory true?" metric. One number a
// director can read at a glance: the share of inventory devices whose live
// reality matches their intended state (NetBox/Nautobot). Self-guarding: renders
// nothing until a well-formed /inventory-accuracy payload arrives, so an empty
// inventory or a failed fetch simply shows no banner rather than a broken card.
function AccuracyBanner({ accuracy }) {
  if (!accuracy || typeof accuracy.accuracy_pct !== 'number') return null
  const pct = accuracy.accuracy_pct
  const tone = pct >= 95 ? 'good' : pct >= 80 ? 'warn' : 'bad'
  const window = accuracy.window_minutes
  return (
    <section className="accuracy" aria-label="Inventory accuracy">
      <div className={`accuracy__score accuracy__score--${tone}`}>
        <span className="accuracy__pct">{pct}%</span>
        <span className="accuracy__caption">
          inventory verified accurate
          {window ? <span className="accuracy__window"> · last {window} min</span> : null}
        </span>
      </div>
      <dl className="accuracy__breakdown">
        <div className="accuracy__stat">
          <dt>Devices</dt><dd>{accuracy.total_devices}</dd>
        </div>
        <div className="accuracy__stat accuracy__stat--good">
          <dt>Accurate</dt><dd>{accuracy.accurate}</dd>
        </div>
        <div className="accuracy__stat accuracy__stat--drifted">
          <dt>Drifted</dt><dd>{accuracy.drifted}</dd>
        </div>
        <div className="accuracy__stat accuracy__stat--stale">
          <dt>Not seen</dt><dd>{accuracy.stale}</dd>
        </div>
      </dl>
    </section>
  )
}

// StatsBar — at-a-glance summary cards across the top of the console. Derived
// entirely from data already loaded, so it adds no requests.
function StatsBar({ drifts, devices, windows }) {
  const counts = { critical: 0, warning: 0, info: 0 }
  for (const d of drifts) {
    if (counts[d.severity] !== undefined) counts[d.severity] += 1
  }
  const now = nowMs()
  const activeWindows = (windows || []).filter(
    (w) => new Date(w.starts_at).getTime() <= now && now < new Date(w.ends_at).getTime()
  ).length
  const pausedDevices = (devices || []).filter((d) => d.auto_remediation_paused).length

  const cards = [
    { key: 'total', label: 'Total drift', value: drifts.length, mod: '' },
    { key: 'critical', label: 'Critical', value: counts.critical, mod: 'stat--critical' },
    { key: 'warning', label: 'Warning', value: counts.warning, mod: 'stat--warning' },
    { key: 'info', label: 'Info', value: counts.info, mod: 'stat--info' },
    {
      key: 'devices', label: 'Devices', value: (devices || []).length,
      mod: pausedDevices ? 'stat--warning' : 'stat--healthy',
      sub: pausedDevices ? `${pausedDevices} paused` : 'all active',
    },
    {
      key: 'windows', label: 'Maint. windows', value: activeWindows,
      mod: activeWindows ? 'stat--info' : '',
      sub: activeWindows ? 'active now' : 'none active',
    },
  ]

  return (
    <div className="statbar">
      {cards.map((c) => (
        <div key={c.key} className={`stat ${c.mod}`}>
          <span className="stat__label">{c.label}</span>
          <span className="stat__value">{c.value}</span>
          {c.sub && <span className="stat__sub">{c.sub}</span>}
        </div>
      ))}
    </div>
  )
}
