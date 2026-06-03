// Tests for the Dashboard component.
//
// React testing follows the same red→green→refactor rhythm you use in pytest.
// We render the component into a fake DOM, then ask "is the right text on screen?"
//
// Networking is faked: we replace the global `fetch` with a stub that returns
// whatever data the test wants. The component should not know or care that
// fetch isn't real — that's the testable-solo design principle from v0.2,
// applied to the frontend.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Dashboard from './Dashboard'

// A fetch stub that routes /drifts, /drifts/history, and /alert-rules to
// separate payloads. /alert-rules defaults to empty so the panel renders quietly.
function mockFetchRouted(drifts, history, alertRules = [], devices = [], maintenanceWindows = [], accuracy = null) {
  return vi.fn((url) => {
    let body = drifts
    if (url === '/drifts/history') body = history
    else if (url.startsWith('/inventory-accuracy')) body = accuracy
    else if (url.startsWith('/alert-rules')) body = alertRules
    else if (url.startsWith('/maintenance-windows')) body = maintenanceWindows
    else if (url.startsWith('/devices')) body = devices
    else if (url.includes('/suggested-fix')) body = { cause: 'Suggested cause', fix: 'Suggested fix', source: 'deterministic' }
    else if (url.includes('/triage')) body = { assessment: 'noise', rationale: 'Stable low-severity drift.', source: 'deterministic', triage: 'chronic' }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    })
  })
}

describe('Dashboard', () => {
  beforeEach(() => {
    // Reset any fetch stub before each test so tests don't leak into each other.
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows a drift event from the API', async () => {
    const sample = [
      {
        id: 1,
        device: 'core-sw-01',
        object: 'interface:Ethernet1',
        field: 'description',
        intent: 'Uplink to dist-01',
        reality: 'old description',
        drift_kind: 'value_mismatch',
        severity: 'info',
        detected_at: '2026-05-26T12:00:00+00:00',
      },
    ]
    // Empty history so the history panel does not render and 'core-sw-01'
    // only appears once (in the drift table).
    globalThis.fetch = mockFetchRouted(sample, [])

    render(<Dashboard />)

    // `findBy*` queries wait until the element appears (up to a timeout),
    // which is what we need because the fetch resolves asynchronously.
    expect(await screen.findByText('core-sw-01')).toBeInTheDocument()
    expect(screen.getByText('interface:Ethernet1')).toBeInTheDocument()
    expect(screen.getByText('description')).toBeInTheDocument()
  })

  it('shows the inventory-accuracy headline when the metric is returned', async () => {
    const accuracy = {
      window_minutes: 60, total_devices: 4, accurate: 3, drifted: 1, stale: 0,
      accuracy_pct: 75, generated_at: '2026-06-03T12:00:00+00:00', devices: [],
    }
    globalThis.fetch = mockFetchRouted([], [], [], [], [], accuracy)

    render(<Dashboard />)

    expect(await screen.findByText('75%')).toBeInTheDocument()
    expect(screen.getByText(/inventory verified accurate/i)).toBeInTheDocument()
    // The breakdown surfaces the drifted/stale counts.
    expect(screen.getByRole('region', { name: /inventory accuracy/i })).toBeInTheDocument()
  })

  it('shows no accuracy banner for an empty inventory (null percentage)', async () => {
    const accuracy = {
      window_minutes: 60, total_devices: 0, accurate: 0, drifted: 0, stale: 0,
      accuracy_pct: null, generated_at: '2026-06-03T12:00:00+00:00', devices: [],
    }
    globalThis.fetch = mockFetchRouted([], [], [], [], [], accuracy)

    render(<Dashboard />)
    await screen.findByText(/no drift events/i)
    expect(screen.queryByRole('region', { name: /inventory accuracy/i })).not.toBeInTheDocument()
  })

  it('shows a loading message before the fetch resolves', () => {
    // A fetch that never resolves — the promise stays pending forever.
    // This freezes the component in its initial "loading" state so we can
    // assert on what it shows.
    globalThis.fetch = vi.fn(() => new Promise(() => {}))

    render(<Dashboard />)

    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows the history panel when history data is returned', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-28T10:00:00+00:00',
      },
    ]
    const history = [
      {
        detected_at: '2026-05-28T10:00:00+00:00',
        device: 'core-sw-01',
        count: 3,
        critical: 1,
        warning: 1,
        info: 1,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, history)

    render(<Dashboard />)

    // The history panel heading should appear once data loads.
    expect(await screen.findByText(/drift history/i)).toBeInTheDocument()
    // The device name appears in the history panel.
    expect(screen.getAllByText('core-sw-01').length).toBeGreaterThan(0)
  })

  it('does not show the history panel when history is empty', async () => {
    globalThis.fetch = mockFetchRouted([], [])

    render(<Dashboard />)

    await screen.findByText(/no drift events/i)
    expect(screen.queryByText(/drift history/i)).not.toBeInTheDocument()
  })

  it('shows causes when a row is clicked', async () => {
    const drifts = [
      {
        id: 1,
        device: 'core-sw-01',
        object: 'interface:Ethernet1',
        field: 'enabled',
        intent: true,
        reality: false,
        drift_kind: 'value_mismatch',
        severity: 'critical',
        detected_at: '2026-05-26T12:00:00+00:00',
        causes: ['Interface was manually shut on the device without updating NetBox.'],
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])

    render(<Dashboard />)

    await screen.findByText('core-sw-01')
    expect(screen.queryByText(/manually shut/i)).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('core-sw-01'))

    expect(screen.getByText(/manually shut/i)).toBeInTheDocument()
  })

  it('shows the AI explanation in the expanded row when present', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00',
        causes: ['Interface was manually shut.'],
        explanation: { text: 'Ethernet1 went down at 14:32 alongside three other ports — likely a bulk shut.', source: 'llm' },
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    expect(screen.queryByText(/bulk shut/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('core-sw-01'))
    expect(screen.getByText(/bulk shut/i)).toBeInTheDocument()
    expect(screen.getByText(/AI explanation/i)).toBeInTheDocument()
  })

  it('shows no AI explanation block when explanation is null', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00',
        causes: ['Interface was manually shut.'],
        explanation: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    expect(screen.queryByText(/AI explanation/i)).not.toBeInTheDocument()
  })

  it('shows known fix in expanded row when known_fix is present', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00',
        causes: [],
        known_fix: { cause: 'Link went down unexpectedly', fix: 'Run no shutdown' },
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    expect(screen.getByText(/link went down/i)).toBeInTheDocument()
    expect(screen.getByText(/no shutdown/i)).toBeInTheDocument()
  })

  it('shows an exact-match badge on a known fix with confidence 1.0', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00', causes: [],
        known_fix: { id: 7, cause: 'shut', fix: 'no shutdown' },
        match_confidence: 1.0,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    expect(screen.getByText(/exact match/i)).toBeInTheDocument()
  })

  it('shows a fuzzy-match warning badge when confidence is below 0.7', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00', causes: [],
        known_fix: { id: 7, cause: 'shut', fix: 'no shutdown' },
        match_confidence: 0.6,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    const badge = screen.getByText(/fuzzy match · 60%/i)
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveClass('match-badge--weak')
  })

  it('shows record fix button when known_fix is null', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00',
        causes: ['Interface was manually shut on the device without updating NetBox.'],
        known_fix: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    expect(screen.getByRole('button', { name: /record fix/i })).toBeInTheDocument()
  })

  it('opens record fix modal when button is clicked', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00',
        causes: ['Interface was manually shut on the device without updating NetBox.'],
        known_fix: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    fireEvent.click(screen.getByRole('button', { name: /record fix/i }))

    expect(screen.getByRole('heading', { name: /record fix/i })).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/what caused this drift/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/how was it resolved/i)).toBeInTheDocument()
  })

  it('pre-fills the record fix form with an AI suggestion', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00',
        causes: [], known_fix: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    fireEvent.click(screen.getByRole('button', { name: /record fix/i }))

    // The suggestion is fetched and pre-filled into the editable fields.
    expect(await screen.findByText(/AI-suggested/i)).toBeInTheDocument()
    expect(screen.getByDisplayValue('Suggested cause')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Suggested fix')).toBeInTheDocument()
  })

  it('lists alert rules from the API', async () => {
    const rules = [
      { id: 1, device: 'core-sw-01', severity: 'critical', window_minutes: 10, enabled: true },
    ]
    globalThis.fetch = mockFetchRouted([], [], rules)

    render(<Dashboard />)

    expect(await screen.findByRole('heading', { name: /alert rules/i })).toBeInTheDocument()
    expect(screen.getByText(/core-sw-01/)).toBeInTheDocument()
    expect(screen.getByText(/10 min/i)).toBeInTheDocument()
  })

  it('shows the add-rule form controls', async () => {
    globalThis.fetch = mockFetchRouted([], [], [])

    render(<Dashboard />)

    await screen.findByRole('heading', { name: /alert rules/i })
    expect(screen.getByRole('button', { name: /add rule/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/window/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/object type/i)).toBeInTheDocument()
  })

  it('shows an object-type-scoped rule with its type', async () => {
    const rules = [
      { id: 1, device: 'core-sw-01', severity: 'critical', window_minutes: 10,
        enabled: true, object_type: 'interface' },
    ]
    globalThis.fetch = mockFetchRouted([], [], rules)

    render(<Dashboard />)

    await screen.findByRole('heading', { name: /alert rules/i })
    // Scope to the rule line so the form's <option>interface</option> isn't matched.
    expect(screen.getByText(/10 min · interface/)).toBeInTheDocument()
  })

  it('lists maintenance windows from the API', async () => {
    const windows = [
      { id: 1, device: 'core-sw-01', starts_at: '2026-06-02T01:00:00+00:00',
        ends_at: '2026-06-02T03:00:00+00:00', reason: 'core upgrade' },
    ]
    globalThis.fetch = mockFetchRouted([], [], [], [], windows)

    render(<Dashboard />)

    expect(await screen.findByRole('heading', { name: /maintenance windows/i })).toBeInTheDocument()
    expect(screen.getByText(/core upgrade/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /open window/i })).toBeInTheDocument()
  })

  it('lists devices with an auto-apply toggle', async () => {
    const devices = [{ name: 'core-sw-01', auto_remediation_paused: false }]
    globalThis.fetch = mockFetchRouted([], [], [], devices)

    render(<Dashboard />)

    expect(await screen.findByRole('heading', { name: /^devices$/i })).toBeInTheDocument()
    expect(screen.getByText('core-sw-01')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^pause$/i })).toBeInTheDocument()
  })

  it('shows a reachable device status', async () => {
    const devices = [{
      name: 'core-sw-01', auto_remediation_paused: false,
      reachable: true, last_reachable_at: '2026-06-03T12:00:00+00:00',
    }]
    globalThis.fetch = mockFetchRouted([], [], [], devices)

    render(<Dashboard />)
    await screen.findByRole('heading', { name: /^devices$/i })
    expect(screen.getByText(/^reachable$/i)).toBeInTheDocument()
  })

  it('shows an unreachable device status', async () => {
    const devices = [{ name: 'core-sw-01', auto_remediation_paused: false, reachable: false }]
    globalThis.fetch = mockFetchRouted([], [], [], devices)

    render(<Dashboard />)
    await screen.findByRole('heading', { name: /^devices$/i })
    expect(screen.getByText(/^unreachable$/i)).toBeInTheDocument()
  })

  it('shows "not probed" when reachability is unknown', async () => {
    const devices = [{ name: 'core-sw-01', auto_remediation_paused: false }]
    globalThis.fetch = mockFetchRouted([], [], [], devices)

    render(<Dashboard />)
    await screen.findByRole('heading', { name: /^devices$/i })
    expect(screen.getByText(/not probed/i)).toBeInTheDocument()
  })

  it('shows resume for a paused device', async () => {
    const devices = [{ name: 'core-sw-01', auto_remediation_paused: true }]
    globalThis.fetch = mockFetchRouted([], [], [], devices)

    render(<Dashboard />)

    await screen.findByRole('heading', { name: /^devices$/i })
    expect(screen.getByRole('button', { name: /^resume$/i })).toBeInTheDocument()
  })

  it('surfaces an API-key error when a write is rejected with 401', async () => {
    const devices = [{ name: 'core-sw-01', auto_remediation_paused: false }]
    // GETs succeed; the mutating PATCH is rejected as unauthorized (no key).
    globalThis.fetch = vi.fn((url, options = {}) => {
      const method = (options.method || 'GET').toUpperCase()
      if (method !== 'GET') {
        return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) })
      }
      let body = []
      if (url.startsWith('/devices')) body = devices
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    })

    render(<Dashboard />)
    await screen.findByRole('heading', { name: /^devices$/i })
    fireEvent.click(screen.getByRole('button', { name: /^pause$/i }))

    // The failure must be visible, not swallowed — and point at the API key.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/api key/i)
  })

  it('shows a "new" badge on a freshly-appeared drift', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-06-03T12:00:00+00:00',
        first_seen: '2026-06-03T11:55:00+00:00', triage: 'new',
        causes: [], known_fix: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')
    expect(screen.getByText(/^new$/i)).toBeInTheDocument()
  })

  it('shows no "new" badge on a chronic drift', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-06-03T12:00:00+00:00',
        first_seen: '2026-05-01T00:00:00+00:00', triage: 'chronic',
        causes: [], known_fix: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')
    // The collapsed row carries no "new" badge (chronic drift).
    expect(screen.queryByText(/^new$/i)).not.toBeInTheDocument()
  })

  it('shows the AI anomaly-triage assessment in the expanded row', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-06-03T12:00:00+00:00', causes: [], known_fix: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    // The assessment + rationale are fetched on expand and shown.
    expect(await screen.findByText(/anomaly triage/i)).toBeInTheDocument()
    expect(screen.getByText(/stable low-severity drift/i)).toBeInTheDocument()
  })

  it('opens the help glossary from the Help button', async () => {
    localStorage.clear()
    globalThis.fetch = mockFetchRouted([], [])
    render(<Dashboard />)
    await screen.findByText(/no drift events/i)

    fireEvent.click(screen.getByRole('button', { name: /^help$/i }))
    const dialog = screen.getByRole('dialog', { name: /help/i })
    expect(dialog).toBeInTheDocument()
    // A glossary definition unique to the panel (not the banner).
    expect(screen.getByText(/a difference between intent and reality/i)).toBeInTheDocument()
  })

  it('shows the intent-vs-reality banner and dismisses it', async () => {
    localStorage.clear()
    globalThis.fetch = mockFetchRouted([], [])
    render(<Dashboard />)
    await screen.findByText(/no drift events/i)

    expect(screen.getByText(/what am i looking at/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /dismiss help banner/i }))
    expect(screen.queryByText(/what am i looking at/i)).not.toBeInTheDocument()
    expect(localStorage.getItem('netdrift_help_banner_dismissed')).toBe('1')
  })

  it('shows an Acknowledge button on an unacknowledged drift', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00', causes: [], known_fix: null,
        acknowledged: false,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    expect(screen.getByRole('button', { name: /^acknowledge$/i })).toBeInTheDocument()
  })

  it('shows Unacknowledge on an acknowledged drift', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00', causes: [], known_fix: null,
        acknowledged: true,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    fireEvent.click(screen.getByText('core-sw-01'))
    expect(screen.getByRole('button', { name: /unacknowledge/i })).toBeInTheDocument()
  })

  it('exposes each drift row via a keyboard-focusable expand button', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-26T12:00:00+00:00',
        causes: ['Interface was manually shut on the device without updating NetBox.'],
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')

    // The expand affordance is a real <button> (focusable, Enter/Space works),
    // named for its row, and reports collapsed state via aria-expanded.
    const toggle = screen.getByRole('button', { name: /details for core-sw-01/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    // Activating it expands the row and flips the state for assistive tech.
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/manually shut/i)).toBeInTheDocument()
  })

  it('renders the record fix modal as an accessible dialog', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00', causes: [], known_fix: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')
    fireEvent.click(screen.getByText('core-sw-01'))
    fireEvent.click(screen.getByRole('button', { name: /record fix/i }))

    // It is a labelled modal dialog, and focus has moved inside it.
    const dialog = screen.getByRole('dialog', { name: /record fix/i })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('closes the record fix modal when Escape is pressed', async () => {
    const drifts = [
      {
        id: 1, device: 'core-sw-01', object: 'interface:Ethernet1',
        field: 'enabled', intent: true, reality: false,
        drift_kind: 'value_mismatch', severity: 'critical',
        detected_at: '2026-05-31T12:00:00+00:00', causes: [], known_fix: null,
      },
    ]
    globalThis.fetch = mockFetchRouted(drifts, [])
    render(<Dashboard />)
    await screen.findByText('core-sw-01')
    fireEvent.click(screen.getByText('core-sw-01'))
    fireEvent.click(screen.getByRole('button', { name: /record fix/i }))

    const dialog = screen.getByRole('dialog', { name: /record fix/i })
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: /record fix/i })).not.toBeInTheDocument()
  })

  it('shows an error message when the fetch fails', async () => {
    // /drifts returns 500; /drifts/history returns empty so only one "500"
    // is in the document (avoiding a `getByText` ambiguity).
    globalThis.fetch = vi.fn((url) => {
      if (url === '/drifts/history') {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })
      }
      return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })
    })

    render(<Dashboard />)

    expect(await screen.findByText(/failed to load drifts/i)).toBeInTheDocument()
    expect(screen.getByText(/500/)).toBeInTheDocument()
  })
})