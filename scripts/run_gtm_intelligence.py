from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from core.gtm_intelligence import (
    GtmInboxPage,
    SquidGtmSourceBundle,
    build_squid_gtm_projection,
    phase0_gtm_json_schema,
    render_gtm_inbox,
    source_bundle_json_schema,
    validate_squid_shadow_page,
)


_MAX_INPUT_BYTES = 2 * 1024 * 1024


def _read_input(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("gtm_intelligence_input_invalid")
    try:
        if path.stat().st_size > _MAX_INPUT_BYTES:
            raise ValueError("gtm_intelligence_input_invalid")
        raw = path.read_bytes()
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("gtm_intelligence_input_invalid")
        return raw
    except OSError as exc:
        raise ValueError("gtm_intelligence_input_invalid") from exc


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("gtm_intelligence_input_invalid")
        payload[key] = value
    return payload


def _reject_json_constant(_value: str) -> object:
    raise ValueError("gtm_intelligence_input_invalid")


def _load_json(path: Path) -> tuple[bytes, object]:
    raw = _read_input(path)
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise ValueError("gtm_intelligence_input_invalid") from exc
    return raw, payload


def _load_page(path: Path) -> GtmInboxPage:
    _, payload = _load_json(path)
    return validate_squid_shadow_page(GtmInboxPage.model_validate(payload))


def _load_source_bundle(path: Path) -> GtmInboxPage:
    raw, _ = _load_json(path)
    bundle = SquidGtmSourceBundle.model_validate_json(raw)
    return build_squid_gtm_projection(bundle)


def _failure() -> dict[str, object]:
    return {
        "ok": False,
        "error": "gtm_intelligence_invalid",
        "mode": "shadow_read_only",
        "read_only_projection": True,
        "external_calls": False,
        "database_calls": False,
        "provider_calls": False,
        "publication_calls": False,
        "automatic_publication": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a saved GTM page, or purely project sanitized owner "
            "records into one, without calling Railway, Telegram, X, a "
            "database, or an AI provider."
        ),
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--source-bundle",
        action="store_true",
        help="Interpret --input as a sanitized Squid owner-source bundle.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot-json", action="store_true")
    mode.add_argument("--dashboard", action="store_true")
    mode.add_argument("--print-schema", action="store_true")
    mode.add_argument("--print-source-schema", action="store_true")
    args = parser.parse_args()

    try:
        if args.print_schema or args.print_source_schema:
            if args.input is not None or args.source_bundle:
                raise ValueError("gtm_intelligence_input_invalid")
            print(json.dumps(
                (
                    source_bundle_json_schema()
                    if args.print_source_schema
                    else phase0_gtm_json_schema()
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ))
            return 0
        if args.input is None:
            raise ValueError("gtm_intelligence_input_invalid")
        page = (
            _load_source_bundle(args.input)
            if args.source_bundle
            else _load_page(args.input)
        )
        if args.dashboard:
            print(render_gtm_inbox(page), end="")
        else:
            print(json.dumps(
                page.as_payload(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ))
        return 0
    except (OSError, ValueError, ValidationError):
        print(json.dumps(
            _failure(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
