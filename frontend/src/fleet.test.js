// Tests for the fleet-health summary contract (platform vision §4.1).
//
// buildFleetSummary is a pure function — the frontend analogue of differ.py:
// given the read-only payloads the dashboard already fetches, it returns the
// per-app "summary contract" the design council asked for. Each card carries
// { key, label, count, status, severity, href } so the home page (and, later,
// a multi-app shell) can render it without knowing how it was computed.

import { describe, it, expect } from 'vitest'
import { buildFleetSummary } from './fleet'

// A small, realistic drift set: two devices, mixed severities.
const DRIFTS = [
  { device: 'core-sw-01', severity: 'critical', detected_at: '2026-06-03T12:00:00+00:00' },
  { device: 'core-sw-01', severity: 'warning', detected_at: '2026-06-03T11:00:00+00:00' },
  { device: 'core-sw-02', severity: 'info', detected_at: '2026-06-03T10:00:00+00:00' },
]
const DEVICES = [
  { name: 'core-sw-01' }, { name: 'core-sw-02' }, { name: 'edge-01' },
]
const ALERT_RULES = [
  { id: 1, enabled: true }, { id: 2, enabled: false },
]

function card(summary, key) {
  return summary.cards.find((c) => c.key === key)
}

describe('buildFleetSummary', () => {
  it('returns a card for every contract slot', () => {
    const s = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: ALERT_RULES })
    const keys = s.cards.map((c) => c.key)
    expect(keys).toEqual(
      expect.arrayContaining(['devices', 'drifting', 'critical', 'warning', 'info', 'sla'])
    )
    // Every card honours the summary contract shape.
    for (const c of s.cards) {
      expect(c).toMatchObject({
        key: expect.any(String),
        label: expect.any(String),
        count: expect.any(Number),
        status: expect.any(String),
        severity: expect.any(String),
        href: expect.any(String),
      })
    }
  })

  it('counts monitored devices from the inventory, not from drift', () => {
    const s = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: [] })
    expect(card(s, 'devices').count).toBe(3)
  })

  it('counts distinct devices that have drift', () => {
    const s = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: [] })
    // core-sw-01 (twice) and core-sw-02 → 2 distinct; edge-01 is clean.
    expect(card(s, 'drifting').count).toBe(2)
    expect(card(s, 'drifting').status).toBe('warning')
  })

  it('splits open drift counts by severity', () => {
    const s = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: [] })
    expect(card(s, 'critical').count).toBe(1)
    expect(card(s, 'warning').count).toBe(1)
    expect(card(s, 'info').count).toBe(1)
  })

  it('marks the critical card critical when criticals exist, ok otherwise', () => {
    const hot = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: [] })
    expect(card(hot, 'critical').status).toBe('critical')

    const calm = buildFleetSummary({
      drifts: [{ device: 'd', severity: 'info', detected_at: '2026-06-03T10:00:00+00:00' }],
      devices: DEVICES, alertRules: [],
    })
    expect(card(calm, 'critical').status).toBe('ok')
  })

  it('counts only enabled alert rules for SLA coverage', () => {
    const s = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: ALERT_RULES })
    expect(card(s, 'sla').count).toBe(1)
    expect(card(s, 'sla').status).toBe('ok')
  })

  it('flags SLA as none when no rules are configured', () => {
    const s = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: [] })
    expect(card(s, 'sla').status).toBe('none')
  })

  it('derives lastActivity from the newest drift detected_at', () => {
    const s = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: [] })
    expect(s.lastActivity).toBe('2026-06-03T12:00:00+00:00')
  })

  it('severity cards deep-link to the filtered drift table', () => {
    const s = buildFleetSummary({ drifts: DRIFTS, devices: DEVICES, alertRules: [] })
    expect(card(s, 'critical').href).toBe('#drifts?severity=critical')
    expect(card(s, 'warning').href).toBe('#drifts?severity=warning')
    expect(card(s, 'info').href).toBe('#drifts?severity=info')
    expect(card(s, 'drifting').href).toBe('#drifts')
  })

  it('is null-safe with no data at all', () => {
    const s = buildFleetSummary({})
    expect(card(s, 'devices').count).toBe(0)
    expect(card(s, 'critical').count).toBe(0)
    expect(card(s, 'drifting').status).toBe('ok')
    expect(s.lastActivity).toBeNull()
  })
})
