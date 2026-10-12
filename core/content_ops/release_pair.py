"""Pure, role-labelled compatibility identity for two independent Git builds.

A digest proves identity, NOT permission. The database owner must separately
verify this pair against its operator-installed, current workspace/client
selection. Neither a Telegram payload nor this helper can register a pair.
"""
from __future__ import annotations

import hashlib
import hmac
import re

_RELEASE = re.compile(r"[a-f0-9]{40}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def release_pair_sha256(engine_release_sha: str, callback_release_sha: str) -> str:
    if not all(type(value) is str and _RELEASE.fullmatch(value)
               for value in (engine_release_sha, callback_release_sha)):
        raise ValueError("content_ops_release_pair_invalid")
    payload = ("content-ops-release-pair@1\nengine=" + engine_release_sha
               + "\ncallback=" + callback_release_sha + "\n")
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def valid_release_pair(engine_release_sha, callback_release_sha, digest) -> bool:
    if type(digest) is not str or not _SHA.fullmatch(digest):
        return False
    try:
        return hmac.compare_digest(
            release_pair_sha256(engine_release_sha, callback_release_sha), digest)
    except ValueError:
        return False
