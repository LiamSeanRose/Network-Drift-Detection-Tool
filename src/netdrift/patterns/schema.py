"""patterns/schema.py — Pydantic v2 validation model for a pattern YAML file.

A pattern file is validated against ``PatternSchema`` before it is ever loaded
into the database. The model is intentionally strict (``extra="forbid"``): a
typo'd key is a hard error, not a silently ignored field, because a pattern that
imports wrong is worse than one that fails to import.

The vocabularies here are the contract with the rest of the tool and must not
drift from it:

- ``object_type`` — the four object types the differ emits (schema.md §2/§5).
- ``drift_kinds`` — the three kinds ``differ.py`` actually produces. A pattern
  naming any other kind could never match a real drift.
- ``vendors`` / ``by_platform`` keys — the canonical platform strings
  (schema.md §4).

``remediation`` mirrors the ``known_issues.remediation`` discriminated union
(schema.md §9). A pattern carries the *intent* of the fix; the importer derives
the stored ``restore_intent`` payload (``object_type``/``field``/``drift_kinds``)
from the pattern's own top-level fields, so contributors never hand-write a
fingerprint or duplicate those fields inside the remediation block.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Contract vocabularies (keep in lockstep with schema.md and differ.py) ----

ObjectType = Literal[
    "interface", "vlan", "bgp_neighbor", "ospf_adjacency", "config", "device"
]

# The three kinds differ.py emits. A pattern naming anything else is rejected —
# it could never match a real drift fingerprint.
DriftKind = Literal["value_mismatch", "missing_in_intent", "missing_in_reality"]

# Canonical platform strings (schema.md §4).
Platform = Literal["arista_eos", "cisco_iosxe", "nokia_srlinux", "juniper_junos"]

# Operational-symptom fields are never directly configurable, so a pattern for
# one MUST be diagnosis-only (remediation absent). Mirrors schema.md §9.
OPERATIONAL_FIELDS = frozenset({"session_state", "adjacency_state"})


# --- Remediation union (mirrors known_issues.remediation, schema.md §9) --------


class RestoreIntentRemediation(BaseModel):
    """An auto-apply-eligible policy grant. The stored payload's
    object_type/field/drift_kinds are filled in by the importer from the
    pattern's own top-level fields, so this block carries only ``kind``."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["restore_intent"]


class PlatformBody(BaseModel):
    """One platform's entry in a raw_snippet remediation. ``cli`` transports
    carry a ``body`` string; ``gnmi`` transports carry ``updates`` tuples."""

    model_config = ConfigDict(extra="forbid")
    transport: Literal["cli", "gnmi"]
    body: str | None = None
    updates: list[dict] | None = None

    @model_validator(mode="after")
    def _transport_matches_payload(self) -> "PlatformBody":
        if self.transport == "cli":
            if not (self.body and self.body.strip()):
                raise ValueError("cli transport requires a non-empty 'body'")
            if self.updates is not None:
                raise ValueError("cli transport must not carry 'updates'")
        else:  # gnmi
            if not self.updates:
                raise ValueError("gnmi transport requires a non-empty 'updates' list")
            if self.body is not None:
                raise ValueError("gnmi transport must not carry 'body'")
        return self


class RawSnippetRemediation(BaseModel):
    """A suggest-only config snippet, keyed by canonical platform. Never
    auto-apply eligible (enforced at the storage layer, schema.md §9)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["raw_snippet"]
    by_platform: dict[Platform, PlatformBody]

    @model_validator(mode="after")
    def _by_platform_non_empty(self) -> "RawSnippetRemediation":
        if not self.by_platform:
            raise ValueError("raw_snippet requires at least one platform in 'by_platform'")
        return self


Remediation = Annotated[
    Union[RestoreIntentRemediation, RawSnippetRemediation],
    Field(discriminator="kind"),
]


# --- The pattern model --------------------------------------------------------


def _non_empty(value: str, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"'{name}' must be a non-empty string")
    return value


class PatternSchema(BaseModel):
    """A single community pattern. ``remediation`` absent (or null) means the
    pattern is diagnosis-only — a cause and a fix description, no executable
    payload."""

    model_config = ConfigDict(extra="forbid")

    name: str
    object_type: ObjectType
    field: str
    drift_kinds: list[DriftKind] = Field(min_length=1)
    vendors: list[Platform] = Field(default_factory=list)
    cause: str
    fix: str
    remediation: Remediation | None = None

    @model_validator(mode="after")
    def _validate(self) -> "PatternSchema":
        _non_empty(self.name, "name")
        _non_empty(self.field, "field")
        _non_empty(self.cause, "cause")
        _non_empty(self.fix, "fix")

        if len(set(self.drift_kinds)) != len(self.drift_kinds):
            raise ValueError("'drift_kinds' must not contain duplicates")

        # Operational-symptom fields are symptoms, not settings — never an
        # executable fix (schema.md §9).
        if self.field in OPERATIONAL_FIELDS and self.remediation is not None:
            raise ValueError(
                f"field '{self.field}' is operational-symptom-only and must be "
                "diagnosis-only (omit 'remediation')"
            )

        return self
