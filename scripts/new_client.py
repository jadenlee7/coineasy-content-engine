"""
New Client Scaffolder

Creates a new client directory with a starter config.yaml.

USAGE:
    python scripts/new_client.py --id wallet_v --name "Wallet V"
    
    # Creates:
    #   clients/wallet_v/
    #   ├── config.yaml         (starter template, ready to edit)
    #   ├── assets/
    #   │   └── .gitkeep        (logos to be added manually)
    #   └── overrides/
    #       └── .gitkeep        (custom templates, optional)

After running:
    1. cp ~/Downloads/wallet_v_logo_*.png clients/wallet_v/assets/
    2. vim clients/wallet_v/config.yaml   # fill in brand, glossary, channels
    3. Test: python scripts/generate_cli.py --client wallet_v --mock
"""

import argparse
import sys
from pathlib import Path
from textwrap import dedent


CLIENTS_DIR = Path("clients")


STARTER_CONFIG = """\
client_id: {client_id}
name: {client_name}
locale: ko-KR
active: false    # Set to true when ready

brand:
  primary_color: "#000000"         # TODO: brand accent color (e.g. Squid mint #00F5D4)
  bg_dark: "#000000"                # Dark theme background
  bg_yellow: "#FFDE00"              # Light theme background (accent)
  text_primary: "#0A0A0A"
  text_body: "#595959"
  logo_dark: assets/logo_dark.png   # Logo for dark backgrounds (usually white)
  logo_light: assets/logo_light.png # Logo for light backgrounds (usually dark)
  font_family: "Pretendard Variable"

content_sources:
  twitter:
    handle: "@TODO"
    poll_interval_min: 30
  blog_rss: []

llm:
  edu_carousel:
    # model: 생략 시 중앙 디폴트(claude-opus-4-8) 상속. override 필요시만 명시
    temperature: 0.3
    tone_guidance: >
      Professional but approachable. 경어체.
    preserve_terms:
      # - "Product Name"
      # - "$TOKEN"
    glossary:
      # "technical term": "한국어 번역"

  news_card:
    # model: 생략 시 중앙 디폴트(claude-opus-4-8) 상속. override 필요시만 명시
    temperature: 0.2

publishing:
  telegram:
    public_channel: "@TODO"
    # approval_channel_id: -100xxxxxx
    bot_token_env: "TELEGRAM_BOT_TOKEN_{env_suffix}"
  twitter:
    typefully_account_id: "{client_id}_kr"
  discord:
    enabled: false

feature_flags:
  auto_approve: false
  education_carousel: true
  news_card: true

routing:
  skip_patterns:
    - "Day 1"
    - "on the ground"
    - "AMA"
  edu_signals:
    - "protocol"
    - "architecture"
    - "how it works"
  news_signals:
    - "partnership"
    - "launch"
    - "now live"
"""


ONBOARDING_CHECKLIST = dedent("""\

    ═══════════════════════════════════════════════════════════════
      ✓ Client '{client_id}' scaffolded
    ═══════════════════════════════════════════════════════════════

    Next steps (30 minute checklist):

    1. Add logos (2-5 min)
       cp ~/path/to/logo_for_dark_bg.png   clients/{client_id}/assets/logo_dark.png
       cp ~/path/to/logo_for_light_bg.png  clients/{client_id}/assets/logo_light.png
       
       Note: logo_dark = the one you'd use ON a dark background (usually white logo)
             logo_light = the one you'd use ON a light background (usually dark logo)

    2. Edit config (10-15 min)
       vim clients/{client_id}/config.yaml
       
       Priority fields:
       - brand.primary_color    (paste from client's brand guide)
       - content_sources.twitter.handle   (@... official account)
       - publishing.telegram.public_channel
       - llm.edu_carousel.preserve_terms  (product names, tickers)
       - llm.edu_carousel.glossary        (technical terms)
       - routing.skip_patterns / edu_signals / news_signals

    3. Dry-run test (2 min)
       python scripts/generate_cli.py --client {client_id} --mock \\
         --source "Sample tweet text here..."

    4. Live LLM test (2 min)
       export ANTHROPIC_API_KEY=sk-ant-...
       python scripts/generate_cli.py --client {client_id} \\
         --source "Real source content..."

    5. Activate
       Edit clients/{client_id}/config.yaml → active: true
       Restart Railway deployment
       Clients auto-picked up on startup

    6. Railway env vars (if new Telegram bot)
       railway variables set TELEGRAM_BOT_TOKEN_{env_suffix}=<bot_token>

""")


def scaffold(client_id: str, client_name: str):
    """Create clients/<client_id>/ directory with starter files."""
    client_dir = CLIENTS_DIR / client_id
    
    if client_dir.exists():
        print(f"✗ Client '{client_id}' already exists at {client_dir}")
        sys.exit(1)
    
    # Directories
    (client_dir / "assets").mkdir(parents=True)
    (client_dir / "overrides").mkdir(parents=True)
    
    # .gitkeep for empty dirs
    (client_dir / "assets" / ".gitkeep").write_text("")
    (client_dir / "overrides" / ".gitkeep").write_text("")
    
    # config.yaml
    env_suffix = client_id.upper().replace("-", "_")
    config_content = STARTER_CONFIG.format(
        client_id=client_id,
        client_name=client_name,
        env_suffix=env_suffix,
    )
    (client_dir / "config.yaml").write_text(config_content)
    
    # Print onboarding checklist
    print(ONBOARDING_CHECKLIST.format(
        client_id=client_id,
        env_suffix=env_suffix,
    ))


def main():
    parser = argparse.ArgumentParser(description="Scaffold a new client")
    parser.add_argument("--id", required=True, help="client_id (lowercase, snake_case)")
    parser.add_argument("--name", required=True, help="Display name (e.g. 'Wallet V')")
    args = parser.parse_args()
    
    # Validate client_id
    if not args.id.replace("_", "").isalnum():
        print(f"✗ Invalid client_id '{args.id}'. Use lowercase alphanumeric + underscore.")
        sys.exit(1)
    if args.id != args.id.lower():
        print(f"✗ client_id must be lowercase: '{args.id}'")
        sys.exit(1)
    
    scaffold(args.id, args.name)


if __name__ == "__main__":
    main()
