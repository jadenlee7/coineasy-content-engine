from __future__ import annotations

import hashlib
from typing import Optional

from .models import (
    GtmDomain,
    GtmInboxPage,
    GtmOperatorItem,
    _REF_PATTERN,
    build_gtm_inbox,
    validate_squid_shadow_page,
)


class GtmReadOnlyBroker:
    """Bounded in-memory list/get surface for a sanitized shadow snapshot.

    The broker deliberately has no source credentials, network client, database
    client, mutation method, verdict method, or publication method. Future HTTP
    or MCP adapters may wrap these two methods only after their source readers
    have an independently reviewed least-privilege contract.
    """

    def __init__(self, page: GtmInboxPage):
        self._page = validate_squid_shadow_page(page)
        self._items = {item.ref: item for item in page.items}
        self._cursor_state: dict[
            str,
            tuple[Optional[GtmDomain], str],
        ] = {}

    @staticmethod
    def _normalize_domain(domain: Optional[GtmDomain]) -> Optional[GtmDomain]:
        if domain is None:
            return None
        try:
            return GtmDomain(domain)
        except (TypeError, ValueError) as exc:
            raise ValueError("gtm_broker_domain_invalid") from exc

    def _new_cursor(
        self,
        domain: Optional[GtmDomain],
        ref: str,
    ) -> str:
        binding = "|".join((
            self._page.snapshot_sha256,
            domain.value if domain is not None else "*",
            "squid",
            ref,
        ))
        cursor = f"cursor:{hashlib.sha256(binding.encode('utf-8')).hexdigest()}"
        self._cursor_state[cursor] = (domain, ref)
        return cursor

    def list_operator_inbox(
        self,
        *,
        domain: Optional[GtmDomain] = None,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> GtmInboxPage:
        if isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("gtm_broker_limit_invalid")
        normalized_domain = self._normalize_domain(domain)

        filtered = [
            item
            for item in self._page.items
            if (normalized_domain is None or item.domain == normalized_domain)
        ]
        start = 0
        if cursor is not None:
            if not isinstance(cursor, str) or not _REF_PATTERN.fullmatch(cursor):
                raise ValueError("gtm_broker_cursor_invalid")
            try:
                cursor_domain, last_ref = self._cursor_state[cursor]
            except KeyError as exc:
                raise ValueError("gtm_broker_cursor_not_found") from exc
            if cursor_domain != normalized_domain:
                raise ValueError("gtm_broker_cursor_scope_mismatch")
            refs = [item.ref for item in filtered]
            try:
                start = refs.index(last_ref) + 1
            except ValueError as exc:
                raise ValueError("gtm_broker_cursor_snapshot_mismatch") from exc
        selected = filtered[start:start + limit]
        has_more = start + len(selected) < len(filtered)
        next_cursor = (
            self._new_cursor(
                normalized_domain,
                selected[-1].ref,
            )
            if has_more and selected
            else None
        )
        return build_gtm_inbox(
            selected,
            generated_at=self._page.generated_at,
            next_cursor=next_cursor,
        )

    def get_operator_item(self, ref: str) -> Optional[GtmOperatorItem]:
        if not isinstance(ref, str):
            raise ValueError("gtm_broker_ref_invalid")
        normalized = ref.strip()
        if not _REF_PATTERN.fullmatch(normalized):
            raise ValueError("gtm_broker_ref_invalid")
        return self._items.get(normalized)
