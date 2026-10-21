"""Two serializers that must not be mixed.

hash_canonical is the compact form the mock hashes before it will create a
record. citation_source_text is the form the evaluator uses as tool-source
text, with default spaces and default ASCII escaping. A quote checked against
the wrong form fails the evaluator or fails the create hash.
"""

import hashlib
import json


def hash_canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(hash_canonical(value).encode()).hexdigest()


def citation_source_text(payload: object) -> str:
    return json.dumps(payload, sort_keys=True)


def idempotency_key(case_id: str, review_id: str, server_hash: str, second_review_id: str | None = None) -> str:
    """Stable key from server state, so a retry cannot open a second record."""
    material = f"v1|{case_id}|{review_id}|{server_hash}"
    if second_review_id:
        material = f"{material}|{second_review_id}"
    return "dc-" + hashlib.sha256(material.encode()).hexdigest()
