"""auth.py — API key generation and hashing (v3.5).

Pure helpers, no database access, so both the storage layer (repository.py) and
the request middleware (api/app.py) can share one definition of what a key looks
like and how it is hashed.

A key is the constant prefix ``sk-netdrift-`` followed by a URL-safe random
token. The prefix makes keys greppable and recognizable in logs/configs; the
token is the secret. Only the SHA-256 hash of the full key is ever persisted —
the raw key is shown to the operator once at creation and never stored.
"""

import hashlib
import secrets

# Recognizable, greppable prefix on every key. Mirrors the convention used by
# Stripe et al. so operators can spot a leaked key in configs and logs.
KEY_PREFIX = "sk-netdrift-"


def generate_api_key() -> str:
    """Return a new random API key: KEY_PREFIX + a URL-safe 32-byte token."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw key.

    Deterministic: verification hashes the presented key and compares against
    the stored digest. SHA-256 (not a slow password hash) is appropriate here
    because keys are high-entropy random tokens, not user-chosen passwords —
    there is nothing to brute-force.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
