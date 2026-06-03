// help.js — the deterministic in-app help content: a finite glossary and the
// severity legend. The goal (from the ease-of-use council) is that an operator
// can use netdrift without leaving the app to research terms. This explains the
// TOOL and its vocabulary; it does not try to teach networking itself.

export const SEVERITY_LEGEND = [
  { level: 'critical', meaning: 'A live outage or a high-impact mismatch — e.g. an interface or tunnel that intent says should be up but is down.' },
  { level: 'warning', meaning: 'A real but lower-impact difference — e.g. an operational state or config value that no longer matches NetBox.' },
  { level: 'info', meaning: 'A benign or cosmetic difference — often something present on the device but not documented in NetBox.' },
]

// ~15 terms — the jargon that appears on the dashboard. Keep this tight; it is
// the "no external research" floor, not an encyclopedia.
export const GLOSSARY = [
  { term: 'intent', definition: 'The state your network is supposed to be in, as documented in NetBox (or Nautobot).' },
  { term: 'reality', definition: 'The state a device actually reports right now, read live from the device.' },
  { term: 'drift', definition: 'A difference between intent and reality — what netdrift detects and lists.' },
  { term: 'drift kind', definition: 'value_mismatch (intent and reality differ), missing_in_reality (in NetBox but not on the device), or missing_in_intent (on the device but not in NetBox).' },
  { term: 'fingerprint', definition: 'A drift’s identity (object type, field, kind) with variable parts stripped, so the same problem on any device matches one known fix.' },
  { term: 'known fix', definition: 'A cause and fix you recorded for a drift pattern; it then surfaces automatically whenever that pattern reappears.' },
  { term: 'acknowledgement', definition: 'Marking a drift as intentional so it stops raising alerts, without deleting it.' },
  { term: 'maintenance window', definition: 'A planned time range during which drift on a device is expected, so alerting and auto-apply are suppressed.' },
  { term: 'SLA alert', definition: 'A notification fired when a drift of a given severity stays unresolved longer than a rule’s time window.' },
  { term: 'auto-apply', definition: 'Optionally pushing a recorded fix back to the device automatically. Off by default and heavily gated.' },
  { term: 'BGP neighbor', definition: 'A peering relationship between routers running BGP. Drift here often means a session is down or mis-configured.' },
  { term: 'OSPF adjacency', definition: 'A neighbor relationship in the OSPF routing protocol. Drift means an expected adjacency is not in the expected state.' },
  { term: 'VLAN', definition: 'A virtual LAN — a logical network segment. Drift is usually a missing, extra, or renamed VLAN.' },
  { term: 'tunnel', definition: 'An overlay link (GRE/VTI/IPsec) carried over the underlying network. Drift means it is down or its config differs.' },
  { term: 'running-config', definition: 'The full live configuration on the device, compared against the intended configuration.' },
]
