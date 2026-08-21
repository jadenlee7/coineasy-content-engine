from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from .models import AgentWorkOrder


def resolve_repository_root(path: Path) -> Path:
    """Resolve one ordinary local repository root without following a symlink."""
    if path.is_symlink():
        raise ValueError("agent_work_order_repository_root_invalid")
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("agent_work_order_repository_root_invalid") from exc
    if not root.is_dir():
        raise ValueError("agent_work_order_repository_root_invalid")
    return root


def _evidence_path(root: Path, uri: str) -> Path:
    candidate = root
    for part in PurePosixPath(uri).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("agent_work_order_evidence_invalid")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("agent_work_order_evidence_invalid") from exc
    if not resolved.is_file():
        raise ValueError("agent_work_order_evidence_invalid")
    return resolved


def load_verified_agent_work_order(
    path: Path,
    repo_root: Path,
) -> AgentWorkOrder:
    """Load one work order and verify every referenced local evidence digest."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("agent_work_order_input_invalid")
    try:
        if path.stat().st_size > 128 * 1024:
            raise ValueError("agent_work_order_input_invalid")
        raw_order = path.read_bytes()
        if len(raw_order) > 128 * 1024:
            raise ValueError("agent_work_order_input_invalid")
        payload = json.loads(raw_order.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("agent_work_order_input_invalid") from exc

    try:
        order = AgentWorkOrder.model_validate(payload)
    except RecursionError as exc:
        raise ValueError("agent_work_order_input_invalid") from exc
    for evidence in order.evidence:
        evidence_path = _evidence_path(repo_root, evidence.uri)
        if evidence_path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("agent_work_order_evidence_invalid")
        evidence_bytes = evidence_path.read_bytes()
        if len(evidence_bytes) > 5 * 1024 * 1024:
            raise ValueError("agent_work_order_evidence_invalid")
        digest = hashlib.sha256(evidence_bytes).hexdigest()
        if digest != evidence.sha256:
            raise ValueError("agent_work_order_evidence_invalid")
    return order


__all__ = [
    "load_verified_agent_work_order",
    "resolve_repository_root",
]
