import asyncio
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from core.sources.x_media_url import is_allowed_x_media_url

X_API_BASE = "https://api.x.com/2"
_MAX_PAGES = 2
_MAX_RESULTS = 200
_TRANSIENT_DELAYS = (0.0, 0.25, 1.0)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_LINK_ENTITIES = 16
_MAX_SOURCE_CONTENT_LENGTH = 60_000
_MAX_LINK_TITLE_LENGTH = 500
_MAX_LINK_DESCRIPTION_LENGTH = 5_000
_MAX_ARTICLE_TEXT_LENGTH = 55_000
_EXPECTED_UNAVAILABLE_REFERENCE_ERRORS = {
    "https://api.x.com/2/problems/not-authorized-for-resource",
    "https://api.x.com/2/problems/resource-not-found",
    "https://api.twitter.com/2/problems/not-authorized-for-resource",
    "https://api.twitter.com/2/problems/resource-not-found",
}


class XRateLimitError(RuntimeError):
    """X rejected the request until the provider rate-limit window resets."""

    def __init__(self, reset_at: int):
        super().__init__("X API rate limit exceeded")
        self.reset_at = reset_at


class XTransientError(RuntimeError):
    """X or the network remained unavailable after a bounded retry."""


class XRequestError(RuntimeError):
    """X rejected a request that should not be retried unchanged."""

    def __init__(self, status_code: int):
        super().__init__("X API request was rejected")
        self.status_code = status_code


class XClient:
    """Minimal X API v2 client for fetching recent tweets from a user."""

    def __init__(self, bearer_token: Optional[str] = None):
        self.bearer = bearer_token or os.getenv("X_BEARER_TOKEN")
        if not self.bearer:
            raise RuntimeError("X_BEARER_TOKEN env var not set")

    async def _request_json(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        timeout: float,
        allow_partial_errors: bool = False,
    ) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt, delay in enumerate(_TRANSIENT_DELAYS):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    response = await client.get(
                        url,
                        headers={"Authorization": f"Bearer {self.bearer}"},
                        params=params,
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == len(_TRANSIENT_DELAYS) - 1:
                        raise XTransientError("X API network request failed") from exc
                    continue

                if response.status_code == 429:
                    raw_reset = response.headers.get("x-rate-limit-reset", "")
                    try:
                        reset_at = max(int(raw_reset), int(time.time()) + 1)
                    except ValueError:
                        reset_at = int(time.time()) + 15 * 60
                    reset_at = min(reset_at, int(time.time()) + 60 * 60)
                    raise XRateLimitError(reset_at)
                if response.status_code >= 500:
                    if attempt == len(_TRANSIENT_DELAYS) - 1:
                        raise XTransientError(
                            f"X API remained unavailable ({response.status_code})"
                        )
                    continue
                if response.status_code >= 400:
                    raise XRequestError(response.status_code)
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise XTransientError("X API returned invalid JSON") from exc
                if not isinstance(payload, dict):
                    raise XTransientError("X API returned an invalid JSON envelope")
                errors = payload.get("errors")
                if isinstance(errors, list) and errors and not allow_partial_errors:
                    # Fail closed instead of silently building a draft from a
                    # partial timeline. Provider details can contain source data,
                    # so keep the exception safe for worker logs.
                    raise XTransientError("X API returned an incomplete response")
                return payload
        raise XTransientError("X API request failed")

    async def get_user_id(self, username: str) -> str:
        """Resolve @username to user_id."""
        username = self._normalize_username(username)
        url = f"{X_API_BASE}/users/by/username/{username}"
        payload = await self._request_json(url, timeout=15.0)
        data = payload.get("data")
        user_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(user_id, str) or not user_id.isdigit():
            raise XTransientError("X API user response is missing a valid id")
        return user_id

    async def get_recent_tweets(
        self,
        username: str,
        hours: int = 24,
        max_results: int = 30,
        since_id: Optional[str] = None,
        require_complete: bool = False,
    ) -> List[Dict]:
        """Fetch tweets from the last N hours for a given username.

        Returns source-safe dictionaries including allowlisted X media metadata.
        Set ``require_complete`` only for cursor-advancing consumers that must
        fail instead of accepting a bounded newest-first sample.
        """
        if since_id is not None and (
            not isinstance(since_id, str)
            or not since_id.isdigit()
            or len(since_id) > 19
        ):
            raise ValueError("since_id must be a numeric X post id")
        username = self._normalize_username(username)
        user_id = await self.get_user_id(username)
        url = f"{X_API_BASE}/users/{user_id}/tweets"
        total_limit = min(max(max_results, 5), _MAX_RESULTS)
        base_params = {
            "tweet.fields": (
                "author_id,created_at,referenced_tweets,public_metrics,"
                "attachments,note_tweet,entities,article"
            ),
            "expansions": (
                "attachments.media_keys,referenced_tweets.id,"
                "referenced_tweets.id.attachments.media_keys"
            ),
            "media.fields": "media_key,type,url,preview_image_url,width,height",
            "exclude": "replies,retweets",
        }
        if since_id:
            base_params["since_id"] = since_id
        else:
            base_params["start_time"] = (
                datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 168)))
            ).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

        data: list[dict] = []
        media_records: list[tuple[int, dict]] = []
        referenced_tweet_records: list[tuple[int, dict]] = []
        unavailable_referenced_tweet_ids: set[str] = set()
        next_token: Optional[str] = None
        for _page in range(_MAX_PAGES):
            remaining = total_limit - len(data)
            if remaining <= 0:
                break
            params = {
                **base_params,
                "max_results": min(max(remaining, 5), 100),
            }
            if next_token:
                params["pagination_token"] = next_token
            payload = await self._request_json(
                url,
                params=params,
                timeout=20.0,
                allow_partial_errors=True,
            )
            page_data = payload.get("data", [])
            if not isinstance(page_data, list):
                raise XTransientError("X API timeline response has invalid data")
            unavailable_referenced_tweet_ids.update(
                self._expected_unavailable_quote_ids(
                    payload.get("errors"),
                    page_data,
                )
            )
            data.extend(item for item in page_data if isinstance(item, dict))
            includes = payload.get("includes", {})
            if isinstance(includes, dict):
                raw_media = includes.get("media")
                if raw_media is not None:
                    if not isinstance(raw_media, list) or any(
                        not isinstance(item, dict) for item in raw_media
                    ):
                        raise XTransientError(
                            "X API timeline contains incomplete media evidence"
                        )
                    media_records.extend(
                        (_page, item) for item in raw_media
                    )
                raw_referenced_tweets = includes.get("tweets")
                if raw_referenced_tweets is not None:
                    if not isinstance(raw_referenced_tweets, list) or any(
                        not isinstance(item, dict)
                        for item in raw_referenced_tweets
                    ):
                        raise XTransientError(
                            "X API timeline contains invalid reference evidence"
                        )
                    referenced_tweet_records.extend(
                        (_page, item) for item in raw_referenced_tweets
                    )
            meta = payload.get("meta", {})
            next_token = meta.get("next_token") if isinstance(meta, dict) else None
            if not isinstance(next_token, str) or not next_token:
                break

        if require_complete and isinstance(next_token, str) and next_token:
            # Advancing the feed cursor from an incomplete newest-first page
            # would make the unseen older posts impossible to fetch later.
            # Fail closed so operations can increase/backfill the bounded
            # window without silently losing official source evidence.
            raise XTransientError(
                "X API timeline exceeded the bounded collection window"
            )

        data = data[:total_limit]

        media_by_key: dict[str, dict] = {}
        media_page_by_key: dict[str, int] = {}
        duplicate_media_keys: set[str] = set()
        for page, item in media_records:
            media_key = item.get("media_key")
            if not isinstance(media_key, str):
                continue
            if media_key in media_by_key:
                if (
                    media_page_by_key[media_key] != page
                    and media_by_key[media_key] == item
                ):
                    # Includes are scoped to each page, so an identical object
                    # may legitimately be repeated on the next page.
                    continue
                duplicate_media_keys.add(media_key)
                continue
            media_by_key[media_key] = item
            media_page_by_key[media_key] = page

        referenced_tweets_by_id: dict[str, dict] = {}
        referenced_tweet_page_by_id: dict[str, int] = {}
        referenced_tweet_provenance_by_id: dict[str, tuple[object, object]] = {}
        referenced_tweet_author_ids_by_id: dict[str, set[str]] = {}
        duplicate_referenced_tweet_ids: set[str] = set()
        for page, item in referenced_tweet_records:
            referenced_tweet_id = item.get("id")
            if (
                not isinstance(referenced_tweet_id, str)
                or not referenced_tweet_id.isdigit()
                or len(referenced_tweet_id) > 19
            ):
                raise XTransientError(
                    "X API timeline contains invalid reference evidence"
                )
            author_id = item.get("author_id")
            author_ids = referenced_tweet_author_ids_by_id.setdefault(
                referenced_tweet_id,
                set(),
            )
            if isinstance(author_id, str):
                author_ids.add(author_id)
            provenance = self._referenced_tweet_provenance(item)
            if referenced_tweet_id in referenced_tweets_by_id:
                if (
                    referenced_tweet_page_by_id[referenced_tweet_id] != page
                    and referenced_tweet_provenance_by_id[
                        referenced_tweet_id
                    ] == provenance
                ):
                    # Engagement metrics and other mutable Tweet fields may
                    # legitimately change between paginated API requests. Only
                    # immutable author/attachment provenance determines whether
                    # the repeated expansion is the same source evidence.
                    continue
                duplicate_referenced_tweet_ids.add(referenced_tweet_id)
                continue
            referenced_tweets_by_id[referenced_tweet_id] = item
            referenced_tweet_page_by_id[referenced_tweet_id] = page
            referenced_tweet_provenance_by_id[referenced_tweet_id] = provenance

        results = []
        for t in data:
            post_id = t.get("id")
            short_text = t.get("text")
            created_at = t.get("created_at")
            if (
                not isinstance(post_id, str)
                or not post_id.isdigit()
                or len(post_id) > 19
                or not isinstance(short_text, str)
                or not isinstance(created_at, str)
                or not self._valid_provider_datetime(created_at)
            ):
                raise XTransientError("X API timeline contains an invalid post")
            refs = t.get("referenced_tweets", [])
            if not isinstance(refs, list):
                raise XTransientError(
                    "X API timeline contains invalid reference evidence"
                )
            for reference in refs:
                if (
                    not isinstance(reference, dict)
                    or reference.get("type")
                        not in {"retweeted", "replied_to", "quoted"}
                    or not isinstance(reference.get("id"), str)
                    or not reference["id"].isdigit()
                    or len(reference["id"]) > 19
                ):
                    raise XTransientError(
                        "X API timeline contains invalid reference evidence"
                    )
            is_retweet = any(
                reference["type"] == "retweeted"
                for reference in refs
            )
            is_reply = any(
                reference["type"] == "replied_to"
                for reference in refs
            )
            is_quote = any(
                reference["type"] == "quoted"
                for reference in refs
            )
            media = self._resolve_media(
                t.get("attachments"),
                media_by_key=media_by_key,
                duplicate_media_keys=duplicate_media_keys,
            )
            if not media:
                inherited_media: list[dict] | None = None
                for reference in refs:
                    if reference["type"] != "quoted":
                        continue
                    referenced_tweet_id = reference["id"]
                    if referenced_tweet_id in unavailable_referenced_tweet_ids:
                        # X explicitly identified this quoted resource as
                        # deleted or protected. It is safe to retain the outer
                        # post, but there is no usable media provenance.
                        continue
                    referenced_tweet = referenced_tweets_by_id.get(
                        referenced_tweet_id
                    )
                    if referenced_tweet is None:
                        # Deleted, protected, or otherwise unavailable quoted
                        # posts cannot establish same-account provenance.
                        continue
                    if referenced_tweet_id in duplicate_referenced_tweet_ids:
                        if user_id in referenced_tweet_author_ids_by_id.get(
                            referenced_tweet_id,
                            set(),
                        ):
                            raise XTransientError(
                                "X API timeline contains incomplete reference evidence"
                            )
                        # Conflicting evidence for an external or unverified
                        # quote is irrelevant because its media can never be
                        # inherited by this account.
                        continue
                    if referenced_tweet.get("author_id") != user_id:
                        # Never localize media borrowed from another account.
                        continue
                    candidate_media = self._resolve_media(
                        referenced_tweet.get("attachments"),
                        media_by_key=media_by_key,
                        duplicate_media_keys=duplicate_media_keys,
                    )
                    if not candidate_media:
                        continue
                    if inherited_media is not None:
                        raise XTransientError(
                            "X API timeline contains ambiguous quoted media evidence"
                        )
                    inherited_media = candidate_media
                if inherited_media is not None:
                    media = inherited_media
            note_tweet = t.get("note_tweet")
            is_note_tweet = (
                isinstance(note_tweet, dict)
                and isinstance(note_tweet.get("text"), str)
                and bool(note_tweet["text"].strip())
            )
            text = (note_tweet["text"] if is_note_tweet else short_text).strip()
            if not text:
                raise XTransientError("X API timeline contains an empty post")
            source_content = self._source_content(
                text,
                t.get("entities"),
                note_tweet.get("entities") if is_note_tweet else None,
                raw_article=t.get("article"),
            )
            photo = next((item for item in media if item.get("type") == "photo"), None)
            results.append({
                "id": post_id,
                "text": source_content,
                "created_at": created_at,
                "url": f"https://x.com/{username}/status/{post_id}",
                "is_retweet": is_retweet,
                "is_reply": is_reply,
                "is_quote": is_quote,
                "metrics": t.get("public_metrics", {}),
                "media": media,
                "source_image_url": photo["url"] if photo else "",
                "is_note_tweet": is_note_tweet,
            })
        return results

    @classmethod
    def _source_content(
        cls,
        text: str,
        *raw_entities: object,
        raw_article: object = None,
    ) -> str:
        """Attach bounded, provider-returned link metadata to an X source.

        The worker deliberately does not fetch expanded URLs. X already returns
        the resolved URL and optional card title/description inside
        ``entities.urls``; persisting that immutable response is enough to make
        link-only official posts useful without adding an SSRF-capable crawler.
        """
        if not isinstance(text, str) or not text.strip():
            raise XTransientError("X API timeline contains an empty post")
        sections = [text.strip()]
        if raw_article is not None:
            if not isinstance(raw_article, dict):
                raise XTransientError(
                    "X API timeline contains invalid article evidence"
                )
            article_title = cls._entity_text(
                raw_article.get("title"),
                maximum=_MAX_LINK_TITLE_LENGTH,
                error="X API timeline contains invalid article evidence",
            )
            article_text = cls._entity_text(
                raw_article.get("plain_text"),
                maximum=_MAX_ARTICLE_TEXT_LENGTH,
                error="X API timeline contains invalid article evidence",
            )
            if not article_title or not article_text:
                raise XTransientError(
                    "X API timeline contains incomplete article evidence"
                )
            sections.extend([
                "[X Article]",
                f"Title: {article_title}\nPlain text: {article_text}",
            ])

        links: list[tuple[str, str, str]] = []
        seen_urls: set[str] = set()
        for entities in raw_entities:
            if entities is None:
                continue
            if not isinstance(entities, dict):
                raise XTransientError(
                    "X API timeline contains invalid URL evidence"
                )
            raw_urls = entities.get("urls")
            if raw_urls is None:
                continue
            if (
                not isinstance(raw_urls, list)
                or len(raw_urls) > _MAX_LINK_ENTITIES
                or any(not isinstance(item, dict) for item in raw_urls)
            ):
                raise XTransientError(
                    "X API timeline contains invalid URL evidence"
                )
            for item in raw_urls:
                resolved_url = cls._resolved_entity_url(item)
                if resolved_url is None or resolved_url in seen_urls:
                    continue
                title = cls._entity_text(
                    item.get("title"),
                    maximum=_MAX_LINK_TITLE_LENGTH,
                )
                description = cls._entity_text(
                    item.get("description"),
                    maximum=_MAX_LINK_DESCRIPTION_LENGTH,
                )
                # Plain media/status links add no copy evidence. Keep a link
                # only when X supplied card text or it points outside X.
                host = urlsplit(resolved_url).hostname or ""
                if not title and not description and host in {
                    "x.com",
                    "www.x.com",
                    "twitter.com",
                    "www.twitter.com",
                }:
                    continue
                seen_urls.add(resolved_url)
                links.append((resolved_url, title, description))

        if links:
            sections.append("[X-provided link metadata]")
            for resolved_url, title, description in links:
                fields = [f"URL: {resolved_url}"]
                if title:
                    fields.append(f"Title: {title}")
                if description:
                    fields.append(f"Description: {description}")
                sections.append("\n".join(fields))
        enriched = "\n\n".join(sections)
        if len(enriched) > _MAX_SOURCE_CONTENT_LENGTH:
            raise XTransientError("X API URL evidence exceeds the source limit")
        return enriched

    @staticmethod
    def _resolved_entity_url(item: dict) -> str | None:
        for name in ("unwound_url", "expanded_url"):
            value = item.get(name)
            if value is None:
                continue
            if not isinstance(value, str) or len(value) > 2_048:
                raise XTransientError(
                    "X API timeline contains invalid URL evidence"
                )
            try:
                parsed = urlsplit(value)
                port = parsed.port
            except ValueError as exc:
                raise XTransientError(
                    "X API timeline contains invalid URL evidence"
                ) from exc
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or port not in {None, 443}
            ):
                raise XTransientError(
                    "X API timeline contains invalid URL evidence"
                )
            return value
        return None

    @staticmethod
    def _entity_text(
        value: object,
        *,
        maximum: int,
        error: str = "X API timeline contains invalid URL evidence",
    ) -> str:
        if value is None:
            return ""
        if not isinstance(value, str) or len(value) > maximum:
            raise XTransientError(error)
        return _WHITESPACE_RE.sub(" ", value).strip()

    @staticmethod
    def _expected_unavailable_quote_ids(
        errors: object,
        page_data: list[object],
    ) -> set[str]:
        """Accept only explicit unavailable errors for quoted child Posts.

        Any error that could refer to a primary timeline Post, another resource
        type, or an unexpected provider failure still rejects the full page.
        """
        if errors is None or errors == []:
            return set()
        if not isinstance(errors, list) or any(
            not isinstance(item, dict) for item in errors
        ):
            raise XTransientError("X API returned an incomplete response")

        primary_ids: set[str] = set()
        quoted_ids: set[str] = set()
        for item in page_data:
            if not isinstance(item, dict):
                continue
            post_id = item.get("id")
            if isinstance(post_id, str):
                primary_ids.add(post_id)
            references = item.get("referenced_tweets", [])
            if not isinstance(references, list):
                continue
            for reference in references:
                if not isinstance(reference, dict):
                    continue
                referenced_tweet_id = reference.get("id")
                if (
                    reference.get("type") == "quoted"
                    and isinstance(referenced_tweet_id, str)
                ):
                    quoted_ids.add(referenced_tweet_id)

        unavailable_ids: set[str] = set()
        for error in errors:
            resource_id = error.get("resource_id")
            if (
                not isinstance(resource_id, str)
                or not resource_id.isdigit()
                or len(resource_id) > 19
                or resource_id in primary_ids
                or resource_id not in quoted_ids
                or error.get("resource_type") != "tweet"
                or error.get("type") not in _EXPECTED_UNAVAILABLE_REFERENCE_ERRORS
            ):
                raise XTransientError("X API returned an incomplete response")
            unavailable_ids.add(resource_id)
        return unavailable_ids

    @staticmethod
    def _referenced_tweet_provenance(item: dict) -> tuple[object, object]:
        """Return only immutable fields used to trust a quoted Tweet."""
        attachments = item.get("attachments")
        if attachments is None:
            media_keys: object = ()
        elif isinstance(attachments, dict):
            raw_media_keys = attachments.get("media_keys", [])
            media_keys = (
                tuple(raw_media_keys)
                if isinstance(raw_media_keys, list)
                and all(isinstance(value, str) for value in raw_media_keys)
                else None
            )
        else:
            media_keys = None
        return item.get("author_id"), media_keys

    @classmethod
    def _resolve_media(
        cls,
        attachments: object,
        *,
        media_by_key: dict[str, dict],
        duplicate_media_keys: set[str],
    ) -> list[dict]:
        if attachments is None:
            return []
        if not isinstance(attachments, dict):
            raise XTransientError("X API timeline contains invalid media references")
        raw_media_keys = attachments.get("media_keys", [])
        if not isinstance(raw_media_keys, list):
            raise XTransientError("X API timeline contains invalid media references")
        media: list[dict] = []
        seen_media_keys: set[str] = set()
        for media_key in raw_media_keys:
            if (
                not isinstance(media_key, str)
                or not media_key
                or media_key in seen_media_keys
                or media_key in duplicate_media_keys
            ):
                raise XTransientError(
                    "X API timeline contains incomplete media evidence"
                )
            seen_media_keys.add(media_key)
            item = media_by_key.get(media_key)
            if item is None:
                raise XTransientError(
                    "X API timeline contains incomplete media evidence"
                )
            media_type = item.get("type")
            if media_type not in {"photo", "video", "animated_gif"}:
                raise XTransientError(
                    "X API timeline contains incomplete media evidence"
                )
            media_url = next(
                (
                    candidate
                    for candidate in (
                        item.get("url"),
                        item.get("preview_image_url"),
                    )
                    if cls._allowed_media_url(candidate)
                ),
                None,
            )
            if media_url is None:
                raise XTransientError(
                    "X API timeline contains incomplete media evidence"
                )
            media.append({
                "media_key": media_key,
                "type": media_type,
                "url": media_url,
                "width": item.get("width"),
                "height": item.get("height"),
            })
        return media

    @staticmethod
    def _normalize_username(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("X username must be a string")
        username = value.strip().lstrip("@")
        if not _USERNAME_RE.fullmatch(username):
            raise ValueError("X username is invalid")
        return username

    @staticmethod
    def _valid_provider_datetime(value: str) -> bool:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None

    @staticmethod
    def _allowed_media_url(value: object) -> bool:
        return is_allowed_x_media_url(value)
