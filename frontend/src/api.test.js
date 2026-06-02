// Tests for api.js — the API-key store and authenticated fetch wrapper.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { apiFetch, getApiKey, setApiKey } from './api'

describe('api', () => {
  beforeEach(() => {
    localStorage.clear()
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200 }))
  })
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('stores and reads the API key', () => {
    expect(getApiKey()).toBe('')
    setApiKey('sk-netdrift-abc')
    expect(getApiKey()).toBe('sk-netdrift-abc')
  })

  it('clears the key when set to empty', () => {
    setApiKey('sk-netdrift-abc')
    setApiKey('')
    expect(getApiKey()).toBe('')
  })

  it('attaches X-API-Key on a POST when a key is set', () => {
    setApiKey('sk-netdrift-xyz')
    apiFetch('/alert-rules', { method: 'POST' })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/alert-rules',
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-API-Key': 'sk-netdrift-xyz' }),
      }),
    )
  })

  it('does not attach X-API-Key on a GET', () => {
    setApiKey('sk-netdrift-xyz')
    apiFetch('/drifts')
    const [, opts] = globalThis.fetch.mock.calls[0]
    expect(opts.headers['X-API-Key']).toBeUndefined()
  })

  it('omits the header on a POST when no key is set', () => {
    apiFetch('/alert-rules', { method: 'POST' })
    const [, opts] = globalThis.fetch.mock.calls[0]
    expect(opts.headers['X-API-Key']).toBeUndefined()
  })

  it('preserves caller-supplied headers', () => {
    setApiKey('sk-netdrift-xyz')
    apiFetch('/known-issues', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
    const [, opts] = globalThis.fetch.mock.calls[0]
    expect(opts.headers['Content-Type']).toBe('application/json')
    expect(opts.headers['X-API-Key']).toBe('sk-netdrift-xyz')
  })
})
