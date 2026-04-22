"""
Template Resolver

Resolves HTML template paths with the following priority:
  1. clients/<client_id>/overrides/<path>      (per-client customization)
  2. core/templates/<path>                      (shared default)

USAGE:
    from core.renderers.template_resolver import resolve_template
    
    path = resolve_template("yellow", "edu/edu_p1_3card.html")
    # → Path(...) or raises FileNotFoundError
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# Base directories (overridable via env vars for testing)
CORE_TEMPLATES_DIR = Path(os.environ.get("CORE_TEMPLATES_DIR", "core/templates"))
CLIENTS_DIR = Path(os.environ.get("CLIENTS_DIR", "clients"))


def resolve_template(client_id: str, template_path: str) -> Path:
    """
    Resolve a template path, checking client override first, then core default.
    
    Args:
        client_id: e.g. "yellow"
        template_path: relative path like "edu/edu_p1_3card.html"
    
    Returns:
        Path to the template file
    
    Raises:
        FileNotFoundError: if neither override nor core has it
    """
    # 1. Check client override
    override_path = CLIENTS_DIR / client_id / "overrides" / template_path
    if override_path.exists():
        return override_path
    
    # 2. Fall back to core default
    core_path = CORE_TEMPLATES_DIR / template_path
    if core_path.exists():
        return core_path
    
    raise FileNotFoundError(
        f"Template not found for client '{client_id}': {template_path}\n"
        f"  Checked override: {override_path}\n"
        f"  Checked core:     {core_path}"
    )


def list_available_templates(client_id: Optional[str] = None) -> dict[str, Path]:
    """
    Return a dict of {template_name: resolved_path} for all known templates.
    If client_id is provided, includes their overrides.
    
    Useful for debugging and admin UI.
    """
    templates: dict[str, Path] = {}
    
    # Scan core templates
    if CORE_TEMPLATES_DIR.exists():
        for html_file in CORE_TEMPLATES_DIR.rglob("*.html"):
            rel = html_file.relative_to(CORE_TEMPLATES_DIR)
            templates[str(rel)] = html_file
    
    # Override with client-specific
    if client_id:
        overrides_dir = CLIENTS_DIR / client_id / "overrides"
        if overrides_dir.exists():
            for html_file in overrides_dir.rglob("*.html"):
                rel = html_file.relative_to(overrides_dir)
                templates[str(rel)] = html_file  # override wins
    
    return templates


# ────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python template_resolver.py <client_id> [template_path]")
        print("\nAvailable core templates:")
        for name, path in list_available_templates().items():
            print(f"  {name}")
        sys.exit(0)
    
    client_id = sys.argv[1]
    
    if len(sys.argv) >= 3:
        path = resolve_template(client_id, sys.argv[2])
        print(f"Resolved: {path}")
    else:
        templates = list_available_templates(client_id)
        print(f"Templates visible to client '{client_id}':")
        for name, path in sorted(templates.items()):
            is_override = "overrides" in str(path)
            marker = "🎨" if is_override else "  "
            print(f"  {marker} {name:40s} → {path}")
