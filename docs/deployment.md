# Deployment & Hardening

This guide covers running netdrift against a real network. The development lab
setup lives in [Getting Started](lab.md); this page is about doing it safely.

netdrift reads from NetBox/Nautobot and your devices, and — when remediation is
enabled — writes configuration back to those devices. Treat it like any tool with
write access to production infrastructure. Read the
[security policy](https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/blob/main/SECURITY.md)
before exposing it.

## The go-live security gate

**The HTTP API ships without authentication before v3.5.** Until in-app auth
lands, choose one of:

- **Read-only / demo deployment** — serve only the detection dashboard, with no
  device-mutating surface reachable. This is the safe public posture.
- **Trusted-network deployment** — run the full stack, including the
  remediation/apply endpoints, only on a network you control, behind your own
  authentication layer (a reverse proxy with auth, a VPN, or an ingress with
  basic-auth / an OAuth2 proxy).

Do **not** expose the API — in particular the `POST .../remediate/apply` and
`auto-apply` routes — directly to an untrusted network before v3.5.

## Secrets

netdrift needs four secrets. None of them belongs in git.

| Secret | What it is | Compose | Helm |
|--------|-----------|---------|------|
| `devices.yml` | Per-device hostnames + credentials | bind-mounted read-only; gitignored | `devicesYaml` value → Kubernetes Secret |
| `NETBOX_TOKEN` | NetBox/Nautobot API token (read) | `.env` | `netbox.token` value |
| `POSTGRES_PASSWORD` | Database password | `.env` (required) | `postgresql.auth.password` (required) |
| `WEBHOOK_URL` | Outbound notification endpoint (may embed a token) | `.env` | values / Secret |

`devices.yml` holds device credentials and is gitignored — never commit it. Copy
`devices.example.yml` to create it.

## Database

- **Set a strong `POSTGRES_PASSWORD`.** There is no default. `docker compose up`
  aborts if it is unset, and `helm install` fails to render without
  `postgresql.auth.password` — so a deployment can never silently ship a known
  credential. Verify: `helm template` with no password set returns an error.
- **The database is bound to localhost** in the compose file
  (`127.0.0.1:5432:5432`), so it is not reachable off-host even on an
  internet-facing machine. Keep it that way unless you have a specific reason and
  a firewall in front.
- **Plan for growth.** Drift history accumulates at roughly 2–4 GB/year at 10
  devices on a 5-minute poll. The Helm PVC default (5 Gi) is sized for weeks, not
  months — raise `postgresql.primary.persistence.size` for a long-lived instance,
  and apply the v3.5 retention policy before promoting a public box.

## Network exposure

- Serve the dashboard same-origin with the API where possible, so no permissive
  CORS is needed. Do not set a wildcard CORS origin while the API is
  unauthenticated.
- Terminate TLS at your ingress/proxy for any deployment reachable beyond
  localhost. Credentials and tokens added later must not transit in clear.

## Webhooks (SSRF guard)

Outbound webhooks validate their destination and reject private/loopback
addresses by default. Only set `WEBHOOK_ALLOW_PRIVATE=true` if you are
deliberately pointing at an on-prem receiver inside your network, and you trust
the source of `WEBHOOK_URL`.

## Auto-remediation safety

Pushing config to a device is **off by default** and gated at three levels:

1. `AUTO_REMEDIATION_ENABLED` — global switch; must be `true` for any apply.
2. Per-issue `auto_apply_enabled` — only issues you have explicitly enabled apply.
3. Per-device pause — `PATCH /devices/{name}/auto-apply` stops applies on one
   device without a restart.

Management interfaces and operational-symptom fields are on a hard do-not-apply
list, and every fix can be dry-run before it is committed. Leave the global
switch off until you have watched dry-runs you trust.

## Containers

The image runs as an unprivileged `netdrift` user, not root. If you add your own
`securityContext` in Kubernetes, keep `runAsNonRoot: true`.

## Helm quickstart

```bash
helm dependency update ./helm/netdrift   # fetches the Postgres subchart
helm install netdrift ./helm/netdrift \
  --set postgresql.auth.password=<strong-password> \
  --set netbox.url=https://netbox.example.com \
  --set netbox.token=<read-token> \
  --set-file devicesYaml=./devices.yml
```

The chart installs the API, scheduler, a migration job, and the Postgres
subchart. Ingress is disabled by default; enable it only with TLS and an edge
auth layer per the go-live gate above.

## Pre-launch hardening checklist

- [ ] `SECURITY.md` disclosure channel is live.
- [ ] No default credentials anywhere; `POSTGRES_PASSWORD` set to a strong value.
- [ ] Database reachable only from localhost (or firewalled).
- [ ] API not exposed to an untrusted network without an auth layer in front.
- [ ] CORS pinned (same-origin preferred; never wildcard while unauthenticated).
- [ ] TLS terminated for anything beyond localhost.
- [ ] `WEBHOOK_ALLOW_PRIVATE` left `false` unless intentionally needed.
- [ ] `AUTO_REMEDIATION_ENABLED` off until dry-runs are trusted.
- [ ] Containers running as non-root.
- [ ] PVC sized for your retention horizon.
