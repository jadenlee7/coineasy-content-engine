from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Mapping


REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "figma_templates.json"
)
_ALLOWED_CLIENTS = frozenset({"yellow", "origintrail", "squid", "babylon"})
_NODE_ID_PATTERN = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")
_VERSION_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[1-9][0-9]*$")


class FigmaTemplateRegistryError(RuntimeError):
    pass


def _load_mapping(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FigmaTemplateRegistryError("figma_template_registry_unavailable") from exc
    if not isinstance(value, Mapping):
        raise FigmaTemplateRegistryError("figma_template_registry_invalid")
    return value


@lru_cache(maxsize=1)
def load_figma_template_registry() -> dict[str, object]:
    raw = _load_mapping(REGISTRY_PATH)
    if set(raw) != {
        "schema_version",
        "file_key",
        "file_name",
        "approval_marker",
        "templates",
        "pending_clients",
    }:
        raise FigmaTemplateRegistryError("figma_template_registry_invalid")
    if (
        raw.get("schema_version") != "1.0"
        or raw.get("file_key") != "hsRSASQjEMxl5NMLH9y5Wm"
        or raw.get("file_name") != "CoinEasy Management"
        or raw.get("approval_marker") != "[KEEP]"
    ):
        raise FigmaTemplateRegistryError("figma_template_registry_invalid")

    templates = raw.get("templates")
    pending = raw.get("pending_clients")
    if (
        not isinstance(templates, Mapping)
        or not isinstance(pending, list)
        or len(set(pending)) != len(pending)
        or any(client not in _ALLOWED_CLIENTS for client in pending)
        or set(templates).intersection(pending)
        or set(templates).union(pending) != _ALLOWED_CLIENTS
    ):
        raise FigmaTemplateRegistryError("figma_template_registry_invalid")

    normalized: dict[str, dict[str, object]] = {}
    seen_nodes: set[str] = set()
    for client_id, value in templates.items():
        if client_id not in _ALLOWED_CLIENTS or not isinstance(value, Mapping):
            raise FigmaTemplateRegistryError("figma_template_registry_invalid")
        if set(value) != {
            "content_kind",
            "status",
            "page_name",
            "node_id",
            "frame_name",
            "width",
            "height",
            "content_engine_style",
            "version",
        }:
            raise FigmaTemplateRegistryError("figma_template_registry_invalid")
        node_id = value.get("node_id")
        frame_name = value.get("frame_name")
        version = value.get("version")
        if (
            value.get("content_kind") != "daily_news"
            or value.get("status") != "approved"
            or value.get("page_name") != "Daily content"
            or not isinstance(node_id, str)
            or not _NODE_ID_PATTERN.fullmatch(node_id)
            or node_id in seen_nodes
            or not isinstance(frame_name, str)
            or not frame_name.startswith("[KEEP] Banner_")
            or value.get("width") != 1080
            or value.get("height") != 1080
            or value.get("content_engine_style") != "classic"
            or not isinstance(version, str)
            or not _VERSION_PATTERN.fullmatch(version)
        ):
            raise FigmaTemplateRegistryError("figma_template_registry_invalid")
        seen_nodes.add(node_id)
        normalized[client_id] = dict(value)

    return {
        "schema_version": "1.0",
        "file_key": raw["file_key"],
        "file_name": raw["file_name"],
        "approval_marker": "[KEEP]",
        "templates": normalized,
        "pending_clients": tuple(pending),
    }


def approved_figma_template(
    client_id: str,
    *,
    content_kind: str,
    template_style: str,
) -> dict[str, object] | None:
    registry = load_figma_template_registry()
    templates = registry["templates"]
    assert isinstance(templates, Mapping)
    template = templates.get(client_id)
    if (
        not isinstance(template, Mapping)
        or template.get("content_kind") != content_kind
        or template.get("content_engine_style") != template_style
    ):
        return None
    return {
        "registry_schema_version": registry["schema_version"],
        "file_key": registry["file_key"],
        "file_name": registry["file_name"],
        "page_name": template["page_name"],
        "node_id": template["node_id"],
        "frame_name": template["frame_name"],
        "status": template["status"],
        "version": template["version"],
    }
