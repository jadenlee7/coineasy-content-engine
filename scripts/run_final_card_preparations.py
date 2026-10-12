"""Bounded hosted entry point; one invocation, no poller or public sender."""
import argparse
import json
from core.content_ops.final_card_preparation_runtime import run


def main(argv=None):
    parser=argparse.ArgumentParser(description="Prepare human-checked private final cards; default OFF")
    parser.add_argument("--validate-only",action="store_true")
    args=parser.parse_args(argv)
    result=run(validate_only=args.validate_only)
    print(json.dumps(result,separators=(",",":")))
    return 0 if result["ok"] else 1


if __name__=="__main__":
    raise SystemExit(main())
