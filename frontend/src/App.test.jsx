// Tests for App — hash-based routing between the fleet-health home (default)
// and the drift table. No router library: we read window.location.hash and
// re-render on 'hashchange'.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from './App'

// Both views fetch on mount; a routed stub keeps every endpoint quiet.
function mockFetch() {
  return vi.fn((url) => {
    let body = []
    if (url === '/drifts/history') body = []
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  })
}

describe('App routing', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    globalThis.fetch = mockFetch()
    window.location.hash = ''
  })
  afterEach(() => {
    vi.restoreAllMocks()
    window.location.hash = ''
  })

  it('renders the fleet-health home by default', async () => {
    window.location.hash = ''
    render(<App />)
    expect(await screen.findByRole('heading', { name: /fleet health/i })).toBeInTheDocument()
  })

  it('renders the drift table when the hash is #drifts', async () => {
    window.location.hash = '#drifts'
    render(<App />)
    // The empty drift table renders its "no drift events" message.
    expect(await screen.findByText(/no drift events/i)).toBeInTheDocument()
  })
})
