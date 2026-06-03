// App.jsx — top-level view switch.
//
// Two views, no router library: the fleet-health home (default) and the drift
// table. We key off window.location.hash so the home page's cards can deep-link
// (e.g. '#drifts?severity=critical') and the browser back button just works.

import { useState, useEffect } from 'react'
import Dashboard from './Dashboard'
import FleetHome from './FleetHome'
import { I18nProvider } from './i18n'

// Parse the hash into a view + optional drift-table filter.
//   ''                         → home
//   '#drifts'                  → drift table, no filter
//   '#drifts?severity=critical'→ drift table filtered to critical
//   '#drifts?device=core-sw-01'→ drift table filtered to one device
function parseHash(hash) {
  const raw = hash || ''
  if (raw.startsWith('#drifts')) {
    const q = raw.includes('?') ? raw.slice(raw.indexOf('?') + 1) : ''
    const params = new URLSearchParams(q)
    const filter = {}
    if (params.get('severity')) filter.severity = params.get('severity')
    if (params.get('device')) filter.device = params.get('device')
    return { view: 'drifts', filter: Object.keys(filter).length ? filter : null }
  }
  return { view: 'home', filter: null }
}

export default function App() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash))

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  return (
    <I18nProvider>
      {route.view === 'drifts'
        ? <Dashboard initialFilter={route.filter} />
        : <FleetHome />}
    </I18nProvider>
  )
}
