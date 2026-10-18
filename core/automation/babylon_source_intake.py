"""Pure Babylon intake policy; no fetching, persistence or delivery authority."""

from collections.abc import Mapping


class BabylonIntakeError(ValueError):
    """A bounded predicate code, never a provider payload."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _valid_id(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 19
        and value.isascii()
        and value.isdigit()
    )


def select_babylon_intake_posts(posts, previous_cursor: str | None) -> tuple:
    """Exclude known replies/retweets only after validating the entire batch.

    Caller must collect a complete bounded timeline first. Unknown flags and
    malformed/duplicate/cursor-ineligible rows still fail closed, including
    rows which would otherwise be excluded. Quotes remain valid candidates.
    Caller computes the next cursor from this result, not the raw timeline;
    an empty result retains the previous cursor. Freshness and publication
    eligibility remain separate checks and are not renewed by this function.
    """
    if previous_cursor is not None and not _valid_id(previous_cursor):
        raise BabylonIntakeError("previous_cursor_invalid")
    if type(posts) not in (list, tuple) or len(posts) > 100:
        raise BabylonIntakeError("collection_size")
    seen = set()
    for post in posts:
        if not isinstance(post, Mapping):
            raise BabylonIntakeError("post_shape")
        post_id = post.get("id")
        if not _valid_id(post_id):
            raise BabylonIntakeError("post_id_format")
        if post_id in seen:
            raise BabylonIntakeError("collection_unique_ids")
        seen.add(post_id)
        if previous_cursor is not None and int(post_id) <= int(previous_cursor):
            raise BabylonIntakeError("post_after_cursor")
        for flag in ("is_retweet", "is_reply", "is_quote"):
            if type(post.get(flag)) is not bool:
                raise BabylonIntakeError("post_classification_boolean")

    return tuple(
        post for post in posts
        if post["is_reply"] is False and post["is_retweet"] is False
    )
