import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import httpx

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
X_API_BASE = "https://api.twitter.com/2"


class XClient:
    """Minimal X API v2 client for fetching recent tweets from a user."""

    def __init__(self, bearer_token: Optional[str] = None):
        self.bearer = bearer_token or X_BEARER_TOKEN
        if not self.bearer:
            raise RuntimeError("X_BEARER_TOKEN env var not set")

    async def get_user_id(self, username: str) -> str:
        """Resolve @username to user_id."""
        url = f"{X_API_BASE}/users/by/username/{username.lstrip('@')}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {self.bearer}"})
            r.raise_for_status()
            return r.json()["data"]["id"]

    async def get_recent_tweets(
        self,
        username: str,
        hours: int = 24,
        max_results: int = 30,
    ) -> List[Dict]:
        """Fetch tweets from the last N hours for a given username.
        Returns list of dicts with keys: id, text, created_at, url, is_retweet, is_reply.
        """
        user_id = await self.get_user_id(username)
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        url = f"{X_API_BASE}/users/{user_id}/tweets"
        params = {
            "max_results": min(max(max_results, 5), 100),
            "start_time": since,
            "tweet.fields": "created_at,referenced_tweets,public_metrics",
            "exclude": "replies",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                url,
                headers={"Authorization": f"Bearer {self.bearer}"},
                params=params,
            )
            r.raise_for_status()
            data = r.json().get("data", [])

        results = []
        for t in data:
            refs = t.get("referenced_tweets", [])
            is_retweet = any(r["type"] == "retweeted" for r in refs)
            is_reply = any(r["type"] == "replied_to" for r in refs)
            results.append({
                "id": t["id"],
                "text": t["text"],
                "created_at": t["created_at"],
                "url": f"https://x.com/{username.lstrip('@')}/status/{t['id']}",
                "is_retweet": is_retweet,
                "is_reply": is_reply,
                "metrics": t.get("public_metrics", {}),
            })
        return results
