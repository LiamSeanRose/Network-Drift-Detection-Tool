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

// A small helper: build a fake `fetch` that responds with the given JSON.
function mockFetchOk(body) {
  return vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    })
  )
}

// A fetch stub that routes /drifts and /drifts/history to separate payloads.
function mockFetchRouted(drifts, history) {
  return vi.fn((url) => {
    const body = url === '/drifts/history' ? history : drifts
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