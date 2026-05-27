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
import { render, screen } from '@testing-library/react'
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
    globalThis.fetch = mockFetchOk(sample)

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

  it('shows an error message when the fetch fails', async () => {
    // A fetch that resolves but returns ok: false — what happens when the
    // backend returns 4xx/5xx (e.g. when FastAPI isn't reachable through
    // the Vite proxy, like the 502 we saw earlier).
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
      })
    )

    render(<Dashboard />)

    expect(await screen.findByText(/failed to load drifts/i)).toBeInTheDocument()
    expect(screen.getByText(/500/)).toBeInTheDocument()
  })
})