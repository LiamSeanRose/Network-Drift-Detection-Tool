"""patterns/loader.py — read, validate, and fingerprint pattern YAML files.

This is the pure I/O-and-validation layer. It does not touch the database; the
``import-patterns`` CLI command (slice 3) consumes ``load_patterns_dir`` and
upserts the results into ``known_issues``.

``yaml.safe_load`` is mandatory — ``yaml.load`` with the default loader can
construct arbitrary Python objects from a malicious file, and patterns are
exactly the kind of artifact a user pastes from the internet. A CI lint rule
also flags ``yaml.load(`` repo-wide.
"""

from pathlib import Path

import yaml

from netdrift.fingerprint import fingerprint as _fingerprint
from netdrift.patterns.schema import PatternSchema


class PatternError(Exception):
    """A pattern file failed to load, validate, or produced a fingerprint that
    collides with another pattern."""


def pattern_fingerprints(pattern: PatternSchema) -> list[str]:
    """Return the fingerprint(s) this pattern maps to — one per drift_kind.

    Computed via the differ's own ``fingerprint()`` against a synthetic drift
    record, so the result is identical to what a real drift event produces by
    construction. A pattern listing N drift_kinds maps to N fingerprints; the
    importer creates one ``known_issues`` row per fingerprint.
    """
    return [
        _fingerprint(
            {
                "object": f"{pattern.object_type}:_",
                "field": pattern.field,
                "drift_kind": kind,
            }
        )
        for kind in pattern.drift_kinds
    ]


def load_pattern_file(path: Path) -> PatternSchema:
    """Load and validate one ``.yaml`` pattern file.

    Raises ``PatternError`` (wrapping the underlying YAML or validation error)
    so the caller gets one exception type with the offending path attached.
    """
    path = Path(path)
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise PatternError(f"{path}: invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise PatternError(
            f"{path}: top-level YAML must be a mapping, got {type(data).__name__}"
        )

    try:
        return PatternSchema.model_validate(data)
    except Exception as exc:  # pydantic ValidationError (and anything it raises)
        raise PatternError(f"{path}: {exc}") from exc


def load_patterns_dir(path: Path) -> list[tuple[Path, PatternSchema]]:
    """Load every ``*.yaml`` file under ``path`` (sorted), validating each and
    asserting no two patterns map to the same fingerprint.

    A fingerprint collision means two patterns would fight over the same
    ``known_issues`` row, so the last import would silently win. That is a
    library-authoring bug, so it is a hard error here rather than at import time.

    Returns ``[(path, pattern), ...]`` sorted by path.
    """
    path = Path(path)
    if not path.is_dir():
        raise PatternError(f"{path}: not a directory")

    files = sorted(path.glob("*.yaml"))
    if not files:
        raise PatternError(f"{path}: no .yaml pattern files found")

    loaded: list[tuple[Path, PatternSchema]] = []
    fingerprint_owner: dict[str, Path] = {}

    for file in files:
        pattern = load_pattern_file(file)
        for fp in pattern_fingerprints(pattern):
            existing = fingerprint_owner.get(fp)
            if existing is not None:
                raise PatternError(
                    f"fingerprint collision on {fp!r}: "
                    f"{existing.name} and {file.name} map to the same fingerprint"
                )
            fingerprint_owner[fp] = file
        loaded.append((file, pattern))

    return loaded
