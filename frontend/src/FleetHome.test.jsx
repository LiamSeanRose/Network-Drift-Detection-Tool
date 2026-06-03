// Tests for FleetHome — the fleet-health overview / "front page" (vision §4.1).
//
// Same approach as Dashboard.test.jsx: render into jsdom, stub global fetch,
// route by URL. FleetHome only reads (no mutating calls), so a plain fetch stub
// covers it.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import FleetHome from './FleetHome'

function mockFetch(drifts = [], devices = [], alertRules = []) {
  return vi.fn((url) => {
    let body = drifts
    if (url.startsWith('/devices')) body = devices
    else if (url.startsWith('/alert-rules')) body = alertRules
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  })
}

const DRIFTS = [
  { device: 'core-sw-01', severity: 'critical', detected_at: '2026-06-03T12:00:00+00:00' },
  { device: 'core-sw-02', severity: 'info', detected_at: '2026-06-03T10:00:00+00:00' },
]
const DEVICES = [{ name: 'core-sw-01' }, { name: 'core-sw-02' }, { name: 'edge-01' }]

describe('FleetHome', () => {
  beforeEach(() => vi.restoreAllMocks())
  afterEach(() => vi.restoreAllMocks())

  it('shows a loading state before the fetches resolve', () => {
    globalThis.fetch = vi.fn(() => new Promise(() => {}))
    render(<FleetHome />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('renders the fleet-health heading', async () => {
    globalThis.fetch = mockFetch(DRIFTS, DEVICES, [])
    render(<FleetHome />)
    expect(await screen.findByRole('heading', { name: /fleet health/i })).toBeInTheDocument()
  })

  it('shows monitored-device count from the inventory', async () => {
    globalThis.fetch = mockFetch(DRIFTS, DEVICES, [])
    render(<FleetHome />)
    const card = await screen.findByRole('link', { name: /devices monitored/i })
    expect(within(card).getByText('3')).toBeInTheDocument()
  })

  it('shows the critical drift count and marks the card critical', async () => {
    globalThis.fetch = mockFetch(DRIFTS, DEVICES, [])
    render(<FleetHome />)
    const card = await screen.findByRole('link', { name: /critical drift/i })
    expect(within(card).getByText('1')).toBeInTheDocument()
    expect(card).toHaveClass('fleet-card--critical')
  })

  it('deep-links severity cards into the filtered drift table', async () => {
    globalThis.fetch = mockFetch(DRIFTS, DEVICES, [])
    render(<FleetHome />)
    const card = await screen.findByRole('link', { name: /critical drift/i })
    expect(card).toHaveAttribute('href', '#drifts?severity=critical')
  })

  it('shows the last-activity time when drift exists', async () => {
    globalThis.fetch = mockFetch(DRIFTS, DEVICES, [])
    render(<FleetHome />)
    await screen.findByRole('heading', { name: /fleet health/i })
    expect(screen.getByText(/last activity/i)).toBeInTheDocument()
  })

  it('renders cleanly with no devices and no drift', async () => {
    globalThis.fetch = mockFetch([], [], [])
    render(<FleetHome />)
    const card = await screen.findByRole('link', { name: /critical drift/i })
    expect(within(card).getByText('0')).toBeInTheDocument()
    expect(screen.getByText(/no drift detected/i)).toBeInTheDocument()
  })
})
