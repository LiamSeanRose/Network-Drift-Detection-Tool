# `docs/lab.md` — Lab Environment Setup

How to build the development lab from scratch: a two-node Arista cEOS topology
plus a local NetBox, on one machine. This is the environment the drift tool is
developed and tested against.

The lab is **development scaffolding** — it is not part of the shipped product and
never runs on a user's network. A contributor follows this document once to get a
working environment; after that, `driftcheck` can be run against it.

> Where the README's quickstart is the condensed version, this document is the
> full detail — every prerequisite, the manual cEOS image step, and the gotchas.

---

## 1. What the lab is

- **Containerlab topology** (`lab/topology.yml`) — two Arista cEOS nodes,
  `core-sw-01` and `core-sw-02`, with one link between them. Each node boots a
  minimal startup-config from `lab/configs/`.
- **Local NetBox** — the intended-state source of truth, run via `netbox-docker`.
- `lab/seed_netbox.py` populates NetBox so its documented state mirrors the
  topology.

Once both are up and NetBox is seeded, intent (NetBox) and reality (the cEOS
nodes) match — so `driftcheck` reports no drift until you deliberately change
something.

---

## 2. Prerequisites

Everything runs on one machine. 16 GB RAM is comfortable; 8 GB works if you are
careful. Linux is easiest. On Windows, use WSL2; on macOS, run Containerlab inside
a Linux VM.

Install first:

- **Docker** — runs both the cEOS nodes and the NetBox stack.
- **Containerlab** — deploys the topology.
  Install with: `bash -c "$(curl -sL https://get.containerlab.dev)"`
- **Python 3.11+** — the drift tool requires it (see `pyproject.toml`).
- **git** — to clone the repo.

---

## 3. The Arista cEOS image (manual step)

cEOS cannot be pulled from a public registry. It must be downloaded by hand from
Arista and imported into Docker. This is the one step that cannot be scripted.

1. Create a free account at <https://www.arista.com> and sign in.
2. Go to **Support → Software Download** and download the **cEOS-lab** image.
   This lab is built and tested against **cEOS-lab 4.36.0F**, which downloads as
   `cEOS-lab-4.36.0F.tar.xz`.
3. Import the image into Docker, tagging it to match `topology.yml`:

   ```bash
   docker import cEOS-lab-4.36.0F.tar.xz ceos:4.36.0F
   ```

4. Confirm it is present:

   ```bash
   docker images | grep ceos
   ```

   You should see `ceos` with tag `4.36.0F`.

> **Tag must match the topology.** `lab/topology.yml` references the image as
> `ceos:4.36.0F`. If you import it under a different tag, either re-tag it or
> update `topology.yml` — otherwise Containerlab cannot find the image.
>
> **A different cEOS version** will probably work, but is untested. If you use
> one, update the `image:` line in `lab/topology.yml` to match your tag.

---

## 4. Run NetBox locally

NetBox is run with the community Docker setup, `netbox-docker`.

```bash
git clone https://github.com/netbox-community/netbox-docker.git
cd netbox-docker
docker compose up -d
```

NetBox comes up at <http://localhost:8000>.

Then, in the NetBox web UI:

1. Create an admin user (the `netbox-docker` README documents the default
   credentials and how to change them).
2. Create an **API token**: profile menu → **API Tokens** → add a token.
   - For seeding you need a token with **write** access.
   - The drift tool itself only needs **read** access.

Keep this token — the next steps need it.

---

## 5. Clone and install the drift tool

```bash
git clone https://github.com/LiamSeanRose/Network-Drift-Detection-Tool.git
cd Network-Drift-Detection-Tool
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -e ".[dev]"` installs the project in editable mode with the dev
dependencies (`pytest`, `ruff`). It also installs the `driftcheck` command.

---

## 6. Deploy the topology

From the repo root:

```bash
sudo containerlab deploy -t lab/topology.yml
```

This starts `core-sw-01` and `core-sw-02`, each booting its startup-config from
`lab/configs/`. Management IPs are `172.20.20.11` and `172.20.20.12`; default cEOS
credentials are `admin` / `admin`.

To tear the lab down again:

```bash
sudo containerlab destroy -t lab/topology.yml
```

The lab is fully reproducible — `topology.yml` plus the `lab/configs/*.cfg` files
mean a destroy followed by a deploy brings back an identical, pre-configured lab.

---

## 7. Seed NetBox

Populate NetBox so its documented state mirrors the topology. The seed script
reads two environment variables:

```bash
export NETBOX_URL=http://localhost:8000
export NETBOX_TOKEN=<your-write-token>
python lab/seed_netbox.py
```

The script is idempotent — running it twice changes nothing the second time.

---

## 8. Configure the drift tool and run it

The collector needs per-device connection details, kept in `devices.yml` at the
repo root. This file holds credentials, so it is gitignored and must never be
committed.

```bash
cp devices.example.yml devices.yml
```

Edit `devices.yml` and set the real `password` for each node (default cEOS is
`admin`). The hostnames and usernames in the example file already match the lab.

The drift tool also needs `NETBOX_URL` and `NETBOX_TOKEN` set in the environment
when it runs (the same variables used for seeding; read access is enough here):

```bash
export NETBOX_URL=http://localhost:8000
export NETBOX_TOKEN=<your-token>
```

> **These are not persisted.** They live only in the current shell. Open a new
> terminal and you must export them again, or `driftcheck` exits with
> `NETBOX_URL and NETBOX_TOKEN environment variables must be set`. Persisting
> them properly is tracked as a separate issue.

Run a drift check:

```bash
driftcheck core-sw-01
```

With a freshly seeded NetBox and a freshly deployed lab, this prints
`OK — no drift`. To see a drift record, change a field on the device (or in
NetBox) and run it again.

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `NETBOX_URL and NETBOX_TOKEN ... must be set` | Env vars not exported in this shell — see Section 8. |
| Containerlab cannot find the image | cEOS image tag does not match `ceos:4.36.0F` in `topology.yml` — see Section 3. |
| `driftcheck` cannot reach a node | Lab not deployed, or wrong hostname/credentials in `devices.yml`. |
| Device not found in NetBox | NetBox not seeded, or device name mismatch — names must match exactly across NetBox, `devices.yml`, and the device. |
| NetBox not reachable at `:8000` | `netbox-docker` containers not running — `docker compose up -d` in the `netbox-docker` directory. |