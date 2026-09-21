"""Offline cross-repo contract, using actual PTB Update and signed controller.

Usage: python -m scripts.check_private_review_polling_contract --meme-repo PATH
Requires python-telegram-bot==21.11.1. Uses synthetic owner fixtures only;
no DB, network, tokens, environment discovery, enrollment or publication.
"""
import argparse
import asyncio
import os
from pathlib import Path
import sys
from dataclasses import replace
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def check(meme_repo):
    root = Path(__file__).resolve().parents[1]
    sys.path.extend([str(meme_repo), str(root / "tests")])
    from telegram import Update, CallbackQuery
    from core.content_ops.polling_review_adapter import PollingReviewAdapter
    from test_content_ops_review_ingress import ReviewIngressTest, NOW
    from pipeline.private_review_route import PrivateReviewRoute
    from pipeline.private_review_runtime import compose_private_review
    from core.content_ops.review_edit_ingress import EditBindings
    from core.content_ops.private_review_owner import PostgresPrivateReviewOwner
    from core.content_ops.private_reply_owner import PostgresPrivateReplyOwner
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:synthetic-test-only"}):
        from pipeline import review_bot

    case = ReviewIngressTest(); case.setUp()
    def no_connect():
        raise AssertionError('composition must not perform I/O')
    db_policy = replace(case.policy,reviewers=tuple((user,str(uuid4())) for user,_ in case.policy.reviewers))
    composed = compose_private_review(enabled=True,policy=db_policy,signer=case.signer,
        bindings=EditBindings(b'synthetic-only-binding-key-for-composition'),connection_factory=no_connect)
    assert type(composed) is PrivateReviewRoute
    assert type(composed.owner.transactional_review_owner) is PostgresPrivateReviewOwner
    assert type(composed.owner.transactional_reply_owner) is PostgresPrivateReplyOwner
    assert composed.owner.edit_owner is None
    assert composed.owner.review_owner is None
    adapter = PollingReviewAdapter(enabled=True, policy=case.policy,
        signer=case.signer, review_owner=case.owner)
    route = PrivateReviewRoute(case.policy.chat_id, case.policy.bot_id,
        frozenset(x[0] for x in case.policy.reviewers), adapter)
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"private_review_route": route}),
                          user_data={})
    results = []
    with patch.object(review_bot.time, "time", return_value=NOW), \
         patch.object(review_bot, "_load_job", side_effect=AssertionError("legacy path called")), \
         patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer:
        for action in ("s", "c", "a", "t"):
            data = case.update(action)
            data["callback_query"]["data"] = "ce1:" + data["callback_query"]["data"]
            data["callback_query"]["from"]["first_name"] = "Fixture"
            data["callback_query"]["message"]["from"]["first_name"] = "FixtureBot"
            before = case.owner.applies
            await review_bot.on_button(Update.de_json(data, None), ctx)
            if action == "a":
                assert case.owner.applies == before
                assert "재시도하지" in answer.call_args.args[0]
            else:
                assert case.owner.applies == before + 1
                assert "기록" in answer.call_args.args[0]
            assert not case.owner.outbox
            results.append(action)
    assert results == ["s", "c", "a", "t"]
    print("PASS: actual poller handler -> numeric policy -> signed private controller; "
          "legacy calls=0; public outboxes=0; external sends=0 (offline fixtures).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--meme-repo", required=True, type=Path)
    args = parser.parse_args()
    if not (args.meme_repo / "pipeline/private_review_route.py").is_file():
        parser.error("local integration checkout required")
    asyncio.run(check(args.meme_repo.resolve()))
