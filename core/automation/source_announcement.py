"""Pure structural source-text minimum; never semantic or factual approval.

This conservative gate only rejects bodies too sparse to support a draft.
Passing it does not establish an announcement, factual accuracy, relevance,
freshness, official authorship, or permission to generate/send. Callers must
apply those independent checks to the latest eligible source; this module
does not select sources or authorize a fallback to an older post.
"""

import re
import unicodedata


_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_MENTION = re.compile(r"(?<!\w)@\w+", re.UNICODE)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def meets_source_text_minimum(value: object) -> bool:
    """Require 6 words, 4 distinct words, and 32 letters outside URLs/mentions.

    Words here are Unicode alphabetic runs, not interpreted natural-language
    claims. Compatibility normalization lets ordinary full-width text follow
    the same rule. Numbers, punctuation, emoji, and decorative spacing cannot
    satisfy the letter minimum; repeating a label cannot satisfy diversity.
    Very short factual text may be held conservatively. The exact input is
    neither modified nor returned, and malformed types are never coerced.
    """
    if type(value) is not str:
        return False
    normalized = unicodedata.normalize("NFKC", value)
    body = _MENTION.sub(" ", _URL.sub(" ", normalized))
    words = _WORD.findall(body)
    return (
        len(words) >= 6
        and len({word.casefold() for word in words}) >= 4
        and sum(len(word) for word in words) >= 32
    )
