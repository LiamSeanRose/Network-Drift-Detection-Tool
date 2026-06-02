# Contributing

## Workflow

- `main` is always working and shippable.
- One branch per change: `feat/...`, `fix/...`, `chore/...`, `docs/...`.
- Every change goes through a pull request. Self-merge is allowed once CI passes — alert the other maintainer so they can review the closed PR after the fact.

## Before opening a PR

```bash
ruff check .   # lint (line-length: 100)
pytest         # full test suite
```

Both must pass. CI enforces the same checks on every push.

## Schema changes

`docs/schema.md` is the data contract between the collector side and the diff engine side. **Any change requires both maintainers to review and approve** — even typo fixes. The process is:

1. Open a proposal PR with a `docs/schema-<version>-proposal.md` document.
2. Discuss and ratify each design decision in the PR.
3. On approval, fold the changes into `docs/schema.md` in a follow-up implementation PR.

## Adding a vendor collector

The plugin registry (v1.0) makes adding a vendor a self-contained change:

1. Create `src/netdrift/collectors/<vendor>.py` with a `@register(...)`-decorated `get_reality` function — see `collectors/base.py` for the contract.
2. Add the module name string to `COLLECTOR_MODULES` in `collectors/__init__.py`.
3. Add the platform string and NetBox slug to `docs/schema.md` Section 4 (requires both-maintainer approval per above).
4. Write integration tests against the Containerlab lab.

No edits to `pipeline.py`, `cli.py`, or `netbox_client.py` are needed — the registry handles dispatch automatically.

## Submitting a pattern

The community pattern library (`patterns/`) ships cause-and-fix knowledge for recurring drift, so a fresh install is useful on day one. Adding a pattern needs no Python — drop a YAML file and open a PR.

1. Add `patterns/<slug>.yaml`. The format and the available fields are documented in `patterns/README.md`.
2. Run `pytest tests/test_bundled_patterns.py`. It validates every file against `PatternSchema`, rejects a fingerprint that collides with an existing pattern, and confirms the fingerprint is one the differ actually produces. A pattern that passes is guaranteed to match real drift.
3. Keep `cause` and `fix` specific and vendor-accurate. The whole value of a pattern is that the next operator does not have to re-derive the diagnosis.
4. Use `remediation: { kind: restore_intent }` only for a field an applier supports (see `patterns/README.md`); leave operational symptoms and whole-object-missing patterns diagnosis-only.

CI runs the same validation on every PR, so a malformed or colliding pattern fails the build rather than shipping broken.

## TDD for logic changes

New behaviour in `differ.py`, `pipeline.py`, collectors, and the API should be added test-first:

1. Write the failing test.
2. Confirm it fails for the right reason.
3. Write the smallest code change that makes it pass.
4. Run the full suite to catch regressions.

Differ tests use hand-written intent/reality pairs in `tests/fixtures/`. Pipeline and scheduler tests inject fake callables — no lab hardware needed.
