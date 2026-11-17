"""Shared data structures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

CSV_COLUMNS = [
    "row",
    "source",
    "method",
    "query",
    "topic",
    "text",
    "created_at",
    "lang",
    "like_count",
    "repost_count",
    "reply_count",
    "quote_count",
    "view_count",
    "favorite_count",
    "hashtags",
    "post_type",
    "sample_group",
    "matched_terms",
    "matched_categories",
]


@dataclass
class Post:
    """One scraped post *before* anonymization.

    `raw_id` and `names` exist only in memory: they are used for in-run
    de-duplication and redaction and are never written to the CSV.
    """

    source: str  # "twitter" | "eksisozluk"
    method: str
    query: str
    text: str
    raw_id: str
    created_at: str = ""
    topic: str = ""
    lang: str = ""
    like_count: int | str = ""
    repost_count: int | str = ""
    reply_count: int | str = ""
    quote_count: int | str = ""
    view_count: int | str = ""
    favorite_count: int | str = ""
    hashtags: str = ""
    post_type: str = "original"
    sample_group: str = ""  # offensive batch: "offensive_candidate" | "control"
    matched_terms: str = ""
    matched_categories: str = ""
    names: set[str] = field(default_factory=set)  # usernames / display names to redact


# Fetchers hand over posts in chunks through this callback (usually
# output.CsvStream.add) so long runs are written to disk as they go.
Emit = Callable[[list[Post]], None]
