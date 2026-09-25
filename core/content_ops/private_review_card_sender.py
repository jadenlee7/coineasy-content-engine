"""Send-only Telegram adapter for the existing private review bot.

No polling, webhook, scheduler, DB, publisher or credential discovery. The
caller supplies the one existing bot token and the exact private supergroup.
Every send is one attempt; unknown outcomes are never retried here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Callable

import httpx

from core.content_ops.private_review_card_receipt import ObservedSend, validate_part_response
from core.content_ops.review_buttons import PRIVATE_CONTROL_LABELS


_TOKEN = re.compile(r"([1-9][0-9]{4,15}):[A-Za-z0-9_-]{30,100}\Z")
_ROOM = re.compile(r"-100[1-9][0-9]{6,12}\Z")
_API = "https://api.telegram.org/bot"
_USERNAME = "coineasy_review_bot"


class PrivateCardSenderError(RuntimeError):
    """Fixed status code only, never a provider response or credential."""


class _BotUrlFilter(logging.Filter):
    def filter(self, record):
        return "api.telegram.org/bot" not in record.getMessage()


for _logger_name in ("httpx", "httpcore"):
    _logger = logging.getLogger(_logger_name)
    _logger.addFilter(_BotUrlFilter())
    _logger.setLevel(logging.WARNING)


def _result(response):
    if response.status_code != 200 or not 0 < len(response.content) <= 65536:
        raise PrivateCardSenderError("private_card_telegram_unknown")
    try:
        body = response.json()
    except Exception:
        raise PrivateCardSenderError("private_card_telegram_unknown") from None
    if type(body) is not dict or body.get("ok") is not True or type(body.get("result")) is not dict:
        raise PrivateCardSenderError("private_card_telegram_unknown")
    return body["result"]


class TelegramPrivateCardSender:
    def __init__(self, *, bot_token: str, bot_id: int, chat_id: int,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], datetime] | None = None):
        token = _TOKEN.fullmatch(bot_token) if type(bot_token) is str else None
        if (token is None or type(bot_id) is not int or bot_id != int(token.group(1))
            or type(chat_id) is not int or _ROOM.fullmatch(str(chat_id)) is None):
            raise PrivateCardSenderError("private_card_sender_configuration_invalid")
        self._token = bot_token
        self._bot_id = bot_id
        self._chat_id = chat_id
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._verified = False
        self._attempted: set[str] = set()
        self._next_index = 0
        self._terminal = False

    async def _post(self, method: str, *, body=None, image=None):
        if method not in {"getMe", "getChat", "getChatMember", "sendPhoto", "sendMessage"}:
            raise PrivateCardSenderError("private_card_method_denied")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                    trust_env=False, transport=self._transport) as client:
                url = _API + self._token + "/" + method
                if image is None:
                    response = await client.post(url, json=body,
                        headers={"Accept-Encoding": "identity"})
                else:
                    response = await client.post(url, data=body,
                        files={"photo": ("news-card.png", image, "image/png")},
                        headers={"Accept-Encoding": "identity"})
            if response.is_redirect:
                raise PrivateCardSenderError("private_card_telegram_unknown")
            return response
        except Exception:
            raise PrivateCardSenderError("private_card_telegram_unknown") from None

    async def preflight(self, *, bot_id: int, chat_id: int) -> None:
        if self._attempted:
            raise PrivateCardSenderError("private_card_preflight_replay_denied")
        self._verified = False
        if bot_id != self._bot_id or chat_id != self._chat_id:
            raise PrivateCardSenderError("private_card_destination_mismatch")
        bot = _result(await self._post("getMe", body={}))
        if (type(bot.get("id")) is not int or bot["id"] != self._bot_id
            or bot.get("is_bot") is not True or bot.get("username") != _USERNAME):
            raise PrivateCardSenderError("private_card_bot_mismatch")
        chat = _result(await self._post("getChat", body={"chat_id": self._chat_id}))
        if (type(chat.get("id")) is not int or chat["id"] != self._chat_id
            or chat.get("type") != "supergroup"
            or any(key in chat for key in ("username", "active_usernames", "linked_chat_id"))):
            raise PrivateCardSenderError("private_card_destination_mismatch")
        member = _result(await self._post("getChatMember", body={
            "chat_id": self._chat_id, "user_id": self._bot_id}))
        user = member.get("user")
        if (member.get("status") != "member" or type(user) is not dict
            or type(user.get("id")) is not int or user["id"] != self._bot_id
            or user.get("is_bot") is not True):
            raise PrivateCardSenderError("private_card_bot_role_invalid")
        self._verified = True

    async def send_once(self, request: dict, *, png: bytes | None) -> ObservedSend:
        if not self._verified or self._terminal or type(request) is not dict:
            raise PrivateCardSenderError("private_card_preflight_required")
        kind = request.get("kind")
        method = request.get("method")
        text = request.get("text")
        expected = {"image": "sendPhoto", "telegram": "sendMessage",
                    "x": "sendMessage", "controls": "sendMessage"}
        if (self._next_index >= 4 or kind != ("image", "telegram", "x", "controls")[self._next_index]
            or method != expected[kind]
            or type(text) is not str or not text.strip()
            or len(text.encode("utf-16-le")) // 2 > (1024 if kind == "image" else 4096)
            or (kind == "controls") != ("reply_markup" in request)
            or set(request) != ({"kind", "method", "text", "reply_markup"}
                                if kind == "controls" else {"kind", "method", "text"})
            or (kind == "image" and (type(png) is not bytes or not png.startswith(b"\x89PNG\r\n\x1a\n")))
            or (kind != "image" and png is not None)):
            raise PrivateCardSenderError("private_card_request_invalid")
        if kind == "controls":
            markup = request["reply_markup"]
            if (type(markup) is not dict or set(markup) != {"inline_keyboard"}
                or type(markup["inline_keyboard"]) is not list
                or len(markup["inline_keyboard"]) != 3
                or any(type(row) is not list or len(row) != 2 for row in markup["inline_keyboard"])
                or any(type(button) is not dict or set(button) != {"text", "callback_data"}
                    or type(button["text"]) is not str
                    or type(button["callback_data"]) is not str
                    or re.fullmatch(r"ce1:[A-Za-z0-9_-]{51}", button["callback_data"]) is None
                    for row in markup["inline_keyboard"] for button in row)
                or tuple(tuple(button["text"] for button in row)
                    for row in markup["inline_keyboard"]) != PRIVATE_CONTROL_LABELS):
                raise PrivateCardSenderError("private_card_request_invalid")
        if ((kind == "image" and "비공개 검수용" not in text)
            or (kind == "telegram" and not text.startswith("[Telegram 공지 전문]\n"))
            or (kind == "x" and not text.startswith("[X 게시글 전문]\n"))
            or (kind == "controls" and "공식 채널 게시 승인이 아닙니다." not in text)):
            raise PrivateCardSenderError("private_card_request_invalid")
        request_hash = hashlib.sha256(json.dumps(request, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if request_hash in self._attempted:
            raise PrivateCardSenderError("private_card_duplicate_send_denied")
        self._attempted.add(request_hash)  # Before possible provider I/O.
        self._next_index += 1
        self._terminal = True
        if kind == "image":
            body = {"chat_id": self._chat_id, "caption": text, "protect_content": "true"}
        else:
            body = {"chat_id": self._chat_id, "text": text,
                    "protect_content": True,
                    "link_preview_options": {"is_disabled": True}}
            if kind == "controls":
                body["reply_markup"] = request["reply_markup"]
        response = await self._post(method, body=body,
                                    image=png if kind == "image" else None)
        observed_at = self._clock()
        if type(observed_at) is not datetime or observed_at.utcoffset() is None:
            raise PrivateCardSenderError("private_card_clock_invalid")
        observed = ObservedSend(method, response.status_code, response.content, observed_at)
        try:
            validate_part_response(observed, request, bot_id=self._bot_id,
                                   chat_id=self._chat_id, thread_id=None)
        except Exception:
            raise PrivateCardSenderError("private_card_telegram_unknown") from None
        self._terminal = False
        return observed
