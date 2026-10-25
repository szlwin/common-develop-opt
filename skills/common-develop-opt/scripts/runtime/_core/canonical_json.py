"""Canonical encoding retained for existing event and Review digests."""
import hashlib
import json


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest_json(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
