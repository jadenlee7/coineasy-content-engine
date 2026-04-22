"""
One-time migration: Replace hardcoded :root CSS tokens with Jinja variables.

BEFORE:
  :root {
    --yellow: #FFDE00;
    --bg-dark: #000000;
    ...
  }

AFTER:
  :root {
    --yellow: {{ brand_primary_color | default('#FFDE00') }};
    --bg-dark: {{ brand_bg_dark | default('#000000') }};
    ...
  }

Run once per template. Idempotent (skip if already migrated).
"""

import re
from pathlib import Path


# Default :root block that Python renderer will inject values into.
# Keeps same variable names so nothing else in CSS breaks.
TOKENIZED_ROOT_BLOCK = """  :root {
    --yellow: {{ brand_primary_color | default('#FFDE00') }};
    --bg-dark: {{ brand_bg_dark | default('#000000') }};
    --bg-yellow: {{ brand_bg_yellow | default('#FFDE00') }};
    --card-bg: #FFFFFF;
    --card-inner-bg: #F2F2F2;
    --text-primary: {{ brand_text_primary | default('#0A0A0A') }};
    --text-body: {{ brand_text_body | default('#595959') }};
    --text-meta: #8A8A8A;
    --font-family: '{{ font_family | default('Pretendard Variable') }}', Pretendard, sans-serif;
  }"""


def migrate_template(path: Path) -> bool:
    """Returns True if migrated, False if already migrated or no match."""
    content = path.read_text()
    
    # Skip if already migrated
    if "brand_primary_color" in content:
        print(f"  ⊘ {path.name} already migrated")
        return False
    
    # Find the :root { ... } block (non-greedy match)
    pattern = re.compile(r"  :root\s*\{[^}]+\}", re.DOTALL)
    match = pattern.search(content)
    
    if not match:
        print(f"  ⚠ {path.name} no :root block found")
        return False
    
    # Replace
    new_content = content[:match.start()] + TOKENIZED_ROOT_BLOCK + content[match.end():]
    
    # Also replace the font-family line to use the variable
    new_content = re.sub(
        r"font-family:\s*'Pretendard Variable'[^;]+;",
        "font-family: var(--font-family);",
        new_content,
    )
    
    path.write_text(new_content)
    print(f"  ✓ {path.name} migrated")
    return True


def main():
    template_dir = Path("core/templates/edu")
    templates = sorted(template_dir.glob("edu_p*.html"))
    
    print(f"Migrating {len(templates)} templates in {template_dir}:")
    migrated = 0
    for t in templates:
        if migrate_template(t):
            migrated += 1
    
    print(f"\nDone. {migrated}/{len(templates)} migrated.")


if __name__ == "__main__":
    main()
