"""
Generate CLI

Run a carousel generation from the command line, for testing or ad-hoc generation.

USAGE:
    # Mock mode (no API key needed, for smoke testing)
    python scripts/generate_cli.py --client yellow --mock \\
      --source "Yellow is chain-agnostic by design..."
    
    # Real LLM call
    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/generate_cli.py --client yellow \\
      --source "Yellow is chain-agnostic by design..." \\
      --source-url "https://x.com/Yellow/status/xxx"
    
    # From a file
    python scripts/generate_cli.py --client yellow \\
      --source-file ./test_inputs/chain_agnostic.txt
"""

import argparse
import sys
from pathlib import Path

# Make sure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.orchestrator import generate_edu_carousel
from core.client_config import list_available_clients


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True, help="client_id (e.g. 'yellow')")
    parser.add_argument("--source", help="Source content (English)")
    parser.add_argument("--source-file", help="Path to file containing source content")
    parser.add_argument("--source-type", default="tweet", choices=["tweet", "blog", "article"])
    parser.add_argument("--source-url", default="", help="Original URL for attribution")
    parser.add_argument("--series-number", help="Manual series number override")
    parser.add_argument("--output-dir", help="Output directory (default: ./output/<client>/<timestamp>)")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM (no API call)")
    args = parser.parse_args()
    
    # Validate client
    available = list_available_clients()
    if args.client not in available:
        print(f"✗ Client '{args.client}' not found. Available: {available}")
        sys.exit(1)
    
    # Get source content
    if args.source_file:
        source_content = Path(args.source_file).read_text()
    elif args.source:
        source_content = args.source
    else:
        print("✗ Need either --source or --source-file")
        sys.exit(1)
    
    output_dir = Path(args.output_dir) if args.output_dir else None
    
    result = generate_edu_carousel(
        client_id=args.client,
        source_content=source_content,
        source_type=args.source_type,
        source_url=args.source_url,
        series_number=args.series_number,
        output_dir=output_dir,
        mock_llm=args.mock,
    )
    
    print(f"\n✓ Generated {len(result.png_paths)} PNG(s) in {result.duration_ms}ms")
    print(f"  Series: {result.series_meta['series_number']} · {result.series_meta['series_title_en']}")
    print(f"  Manifest: {result.manifest_path}")
    
    # Try to auto-open (macOS/Linux)
    import subprocess
    try:
        subprocess.run(["open", str(Path(result.png_paths[0]).parent)], check=False)
    except Exception:
        try:
            subprocess.run(["xdg-open", str(Path(result.png_paths[0]).parent)], check=False)
        except Exception:
            pass


if __name__ == "__main__":
    main()
