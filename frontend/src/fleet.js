// fleet.js — the fleet-health "summary contract" (platform vision §4.1).
//
// buildFleetSummary is pure (no I/O), like differ.py: hand it the read-only
// payloads the dashboard already fetches and it returns the cards the home page
// renders. Keeping the shape — { key, label, count, status, severity, href } —
// as an explicit contract is the council's requirement: it makes the page
// "platform-ready" (a future multi-app shell can aggregate the same shape per
// app) without a rebuild, and it keeps all the aggregation logic unit-testable
// away from React.
//
// `status` is the health signal used for colour (ok | warning | critical |
// none); `severity` is the inherent class of the card for styling. They differ:
// a "Critical drift" card is always severity critical, but its status is only
// `critical` when criticals actually exist.

function distinct(values) {
  return new Set(values).size
}

export function buildFleetSummary({ drifts = [], devices = [], alertRules = [] } = {}) {
  const bySeverity = (level) => drifts.filter((d) => d.severity === level).length
  const critical = bySeverity('critical')
  const warning = bySeverity('warning')
  const info = bySeverity('info')

  const drifting = distinct(drifts.map((d) => d.device))
  const enabledRules = alertRules.filter((r) => r.enabled).length

  // Newest detected_at across all open drift — our proxy for "last activity"
  // until the scheduler records a real per-device poll time (Liam's side).
  const lastActivity = drifts.reduce(
    (latest, d) => (latest === null || d.detected_at > latest ? d.detected_at : latest),
    null,
  )

  const cards = [
    {
      key: 'devices',
      label: 'Devices monitored',
      count: devices.length,
      status: devices.length > 0 ? 'ok' : 'none',
      severity: 'info',
      href: '#devices',
    },
    {
      key: 'drifting',
      label: 'Devices with drift',
      count: drifting,
      status: drifting > 0 ? 'warning' : 'ok',
      severity: 'warning',
      href: '#drifts',
    },
    {
      key: 'critical',
      label: 'Critical drift',
      count: critical,
      status: critical > 0 ? 'critical' : 'ok',
      severity: 'critical',
      href: '#drifts?severity=critical',
    },
    {
      key: 'warning',
      label: 'Warning drift',
      count: warning,
      status: warning > 0 ? 'warning' : 'ok',
      severity: 'warning',
      href: '#drifts?severity=warning',
    },
    {
      key: 'info',
      label: 'Info drift',
      count: info,
      status: 'ok',
      severity: 'info',
      href: '#drifts?severity=info',
    },
    {
      key: 'sla',
      label: 'SLA alert rules',
      count: enabledRules,
      status: enabledRules > 0 ? 'ok' : 'none',
      severity: 'info',
      href: '#alert-rules',
    },
  ]

  return { cards, lastActivity }
}
