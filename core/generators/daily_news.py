from typing import List, Dict
from anthropic import Anthropic
import os
import json

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


FILTER_PROMPT = """You are filtering crypto project tweets for relevance to Korean retail crypto investors.

Given the following tweets from a project's X account in the last 24 hours, select ONLY the tweets that are:
- Real announcements, product updates, partnerships, technical milestones, or significant ecosystem news
- NOT marketing fluff, generic engagement bait, retweets, replies, or community shoutouts

Return a JSON array of tweet IDs that pass the filter. If none qualify, return [].

Tweets:
{tweets_json}

Return ONLY the JSON array, no other text. Example: ["123", "456"]"""


TRANSLATE_PROMPT = """You are a Korean crypto content writer for CoinEasy, a Korea GTM partner for global crypto projects. Translate and adapt the following tweets into a daily news brief for Korean retail crypto audiences.

Tone: 정중하고 정확한 한국어. 친근하지만 전문적. 영어 원문의 마케팅 hyperbole는 제거하고 사실만 전달.
전문용어: 한국어 우선, 영문 병기 (예: 스테이트 채널(state channel)).

Format the output as JSON with this exact schema:
{{
  "headline": "1-line Korean headline, max 60 characters, no emoji",
  "summary": "1-line Korean summary, max 100 characters",
  "body": "3-5 line Korean body. Each line is a separate update. Each line starts with '• '.",
  "hashtags": ["#한국어태그1", "#한국어태그2", "#영문태그1"]
}}

Source tweets ({client_name}):
{tweets_text}

Source URLs (include in body if relevant):
{urls}

Return ONLY the JSON object."""


class DailyNewsGenerator:
    def __init__(self):
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-5-20250929"

    async def filter_tweets(self, tweets: List[Dict]) -> List[Dict]:
        """LLM-filter tweets to keep only meaningful announcements."""
        if not tweets:
            return []
        # Pre-filter: drop retweets and replies (already excluded by API but double-check)
        candidates = [t for t in tweets if not t.get("is_retweet") and not t.get("is_reply")]
        if not candidates:
            return []
        compact = [{"id": t["id"], "text": t["text"][:280]} for t in candidates]
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": FILTER_PROMPT.format(tweets_json=json.dumps(compact, ensure_ascii=False)),
            }],
        )
        raw = msg.content[0].text.strip()
        # Strip code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            kept_ids = json.loads(raw)
        except json.JSONDecodeError:
            kept_ids = []
        return [t for t in candidates if t["id"] in kept_ids]

    async def translate(self, tweets: List[Dict], client_name: str) -> Dict:
        """Translate filtered tweets into Korean daily news format."""
        if not tweets:
            return {
                "headline": "",
                "summary": "",
                "body": "",
                "hashtags": [],
                "is_empty": True,
            }
        tweets_text = "\n\n".join([f"[{i+1}] {t['text']}" for i, t in enumerate(tweets)])
        urls = "\n".join([t["url"] for t in tweets])
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": TRANSLATE_PROMPT.format(
                    client_name=client_name,
                    tweets_text=tweets_text,
                    urls=urls,
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        result = json.loads(raw)
        result["is_empty"] = False
        result["source_urls"] = [t["url"] for t in tweets]
        result["source_tweet_ids"] = [t["id"] for t in tweets]
        return result
