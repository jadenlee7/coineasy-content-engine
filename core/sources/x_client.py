import asyncio
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import httpx

X_API_BASE = "https://api.x.com/2"
_MAX_PAGES = 2
_MAX_RESULTS = 200
_TRANSIENT_DELAYS = (0.0, 0.25, 1.0)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


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
                if isinstance(errors, list) and errors:
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
            "tweet.fields": "created_at,referenced_tweets,public_metrics,attachments,note_tweet",
            "expansions": "attachments.media_keys",
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
        media_records: list[dict] = []
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
            payload = await self._request_json(url, params=params, timeout=20.0)
            page_data = payload.get("data", [])
            if not isinstance(page_data, list):
                raise XTransientError("X API timeline response has invalid data")
            data.extend(item for item in page_data if isinstance(item, dict))
            includes = payload.get("includes", {})
            if isinstance(includes, dict) and isinstance(includes.get("media"), list):
                media_records.extend(
                    item for item in includes["media"] if isinstance(item, dict)
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

        media_by_key = {
            item.get("media_key"): item
            for item in media_records
            if isinstance(item, dict) and isinstance(item.get("media_key"), str)
        }

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
            refs = refs if isinstance(refs, list) else []
            is_retweet = any(
                isinstance(reference, dict) and reference.get("type") == "retweeted"
                for reference in refs
            )
            is_reply = any(
                isinstance(reference, dict) and reference.get("type") == "replied_to"
                for reference in refs
            )
            media = []
            attachments = t.get("attachments")
            raw_media_keys = (
                attachments.get("media_keys", [])
                if isinstance(attachments, dict)
                else []
            )
            if not isinstance(raw_media_keys, list):
                raise XTransientError("X API timeline contains invalid media references")
            for media_key in raw_media_keys:
                if not isinstance(media_key, str):
                    continue
                item = media_by_key.get(media_key)
                if not item:
                    continue
                media_url = item.get("url") or item.get("preview_image_url")
                if not self._allowed_media_url(media_url):
                    continue
                media.append({
                    "media_key": media_key,
                    "type": item.get("type"),
                    "url": media_url,
                    "width": item.get("width"),
                    "height": item.get("height"),
                })
            note_tweet = t.get("note_tweet")
            is_note_tweet = (
                isinstance(note_tweet, dict)
                and isinstance(note_tweet.get("text"), str)
                and bool(note_tweet["text"].strip())
            )
            text = (note_tweet["text"] if is_note_tweet else short_text).strip()
            if not text:
                raise XTransientError("X API timeline contains an empty post")
            photo = next((item for item in media if item.get("type") == "photo"), None)
            results.append({
                "id": post_id,
                "text": text,
                "created_at": created_at,
                "url": f"https://x.com/{username}/status/{post_id}",
                "is_retweet": is_retweet,
                "is_reply": is_reply,
                "metrics": t.get("public_metrics", {}),
                "media": media,
                "source_image_url": photo["url"] if photo else "",
                "is_note_tweet": is_note_tweet,
            })
        return results

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
        if not isinstance(value, str) or len(value) > 2_048:
            return False
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.lower() == "pbs.twimg.com"
            and parsed.username is None
            and parsed.password is None
        )
