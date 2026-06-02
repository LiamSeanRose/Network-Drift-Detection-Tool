# Contributing

- `main` is always working and shippable.
- One branch per change: `feat/...`, `fix/...`, `chore/...`.
- Every change goes through a pull request, reviewed by the other maintainer.
- Run `ruff check .` and `pytest` before opening a PR.

## Contributing drift patterns

The bundled community patterns in [`patterns/`](patterns/) seed the knowledge
base, so a fresh install surfaces a stored cause and fix the first time a drift
appears — before you have any history of your own. To add one:

1. Drop a new `<slug>.yaml` in [`patterns/`](patterns/). The field reference and
   examples live in [`patterns/README.md`](patterns/README.md).
2. Run `driftcheck validate-patterns patterns/` — it schema-validates every file
   and rejects fingerprint collisions, no database required. CI runs the same
   check, so a malformed pattern fails the build.
3. Open a PR. A maintainer reviews the **vendor-specific** fields — which
   platforms a `restore_intent` fix is actually known to work on — before merge.

Patterns always import with auto-apply **off**; an operator enables it per issue
after the confirm-threshold gate. Never hand-write a fingerprint — the loader
computes it from `object_type` / `field` / `drift_kinds` with the differ's own
function, so a pattern only ever matches drift the differ really produces.