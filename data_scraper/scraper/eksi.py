"""Ekşi Sözlük scraping through Ekşi's own mobile API (via the `eksiapi` client).

Ekşi does not publish public API docs. `eksiapi` talks to the same
api.eksisozluk.com endpoints the Android app uses, with an anonymous token, so
no Ekşi account is needed.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any, Literal

from eksiapi import EksiClient
from eksiapi.errors import EksiApiError

from .config import Settings
from .models import Emit, Post

Order = Literal["chronological", "popular", "today"]
Progress = Callable[[str], None]

EKSI_METHODS = {
    "topic": "One topic (başlık)",
    "search": "Keyword search over titles",
    "gundem": "Gündem (popular now)",
}
EKSI_ORDERS = {
    "popular": "Most-liked first",
    "chronological": "Oldest first",
    "today": "Today's entries",
}
_ACTION: dict[str, Literal["popular", "today"] | None] = {
    "chronological": None,
    "popular": "popular",
    "today": "today",
}
_TOPIC_ID_IN_URL = re.compile(r"--(\d+)(?:[/?#]|$)")


def _pick(data: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _pause() -> None:
    time.sleep(random.uniform(0.5, 1.5))


def _resolve_topic(client: EksiClient, term: str) -> int:
    if term.isdigit():
        return int(term)
    if m := _TOPIC_ID_IN_URL.search(term):
        return int(m.group(1))
    return client.resolve_topic_id(term)


def _entries_from_topic(
    client: EksiClient, topic_id: int, limit: int, order: Order, method: str, query: str,
    emit: Emit,
) -> int:
    """Emit one batch per page; -> number of entries emitted."""
    total = 0
    page = 1
    while total < limit:
        topic = client.topic_typed(topic_id, page, action=_ACTION[order])
        if not topic.entries:
            break
        batch: list[Post] = []
        for entry in topic.entries:
            if not entry.content:
                continue
            batch.append(
                Post(
                    source="eksisozluk",
                    method=f"{method}-{order}",
                    query=query,
                    topic=topic.title or "",
                    text=entry.content,
                    raw_id=str(entry.id or ""),
                    created_at=str(entry.created_at or ""),
                    favorite_count=_pick(entry.raw, "FavoriteCount", "favoriteCount"),
                    names={entry.author} if entry.author else set(),
                )
            )
            if total + len(batch) >= limit:
                break
        emit(batch)
        total += len(batch)
        if topic.page_count is None or page >= topic.page_count:
            break
        page += 1
        _pause()
    return total


def _topic_ids(items: Any) -> list[int]:
    ids = []
    for item in items:
        if isinstance(item, Mapping):
            value = _pick(item, "TopicId", "topicId", "Id", "id", default=None)
            if value is not None and str(value).isdigit():
                ids.append(int(value))
    return ids


# A topic-list source for topic_pool: ("search", keyword), ("popular",) or ("today",)
Source = tuple[str, ...]


def open_client() -> EksiClient:
    return EksiClient.anonymous(raw_response=False)


def topic_pool(
    client: EksiClient, sources: Iterable[Source], progress: Progress, max_pages: int = 50,
) -> Iterator[tuple[int, Source]]:
    """Yield (topic_id, source) lazily, page by page, without repeats.

    Lets callers keep pulling topics until they have enough entries instead of
    stopping at a fixed number of topics. A source that errors is skipped.
    """
    seen: set[int] = set()
    for source in sources:
        kind = source[0]
        for page_no in range(1, max_pages + 1):
            try:
                if kind == "search":
                    payload = client.search_topics(source[1], page=page_no)
                elif kind == "popular":
                    payload = client.popular(page=page_no)
                elif kind == "today":
                    payload = client.today(page=page_no)
                else:
                    raise ValueError(f"Unknown topic source: {kind}")
                page = client.page(payload, page=page_no)
            except EksiApiError as exc:
                progress(f"Ekşi: {kind} list stopped at page {page_no} ({exc.__class__.__name__})")
                break
            ids = _topic_ids(page.items)
            for topic_id in ids:
                if topic_id not in seen:
                    seen.add(topic_id)
                    yield topic_id, source
            if not ids or not page.has_more:
                break
            _pause()


def read_topic(
    client: EksiClient, topic_id: int, take: int, order: Order, method: str, query: str,
    emit: Emit, progress: Progress,
) -> None:
    """Read up to `take` entries of one topic into `emit`; falls back to
    chronological when a filtered order (`today`, `popular`) has nothing.
    API errors skip the topic's rest."""
    got = 0

    def counted(batch: list[Post]) -> None:
        nonlocal got
        got += len(batch)
        emit(batch)

    try:
        _entries_from_topic(client, topic_id, take, order, method, query, counted)
        # `today` is empty for quiet topics, and `popular` (şükela) is empty for
        # topics without enough favorited entries, which is most small/old ones
        if got == 0 and order != "chronological":
            _entries_from_topic(client, topic_id, take, "chronological", method, query, counted)
    except EksiApiError as exc:
        # pages emitted before the error are already saved
        progress(f"Ekşi: skipping rest of topic {topic_id} ({exc.__class__.__name__})")


def _spread(
    client: EksiClient, topics: Iterable[tuple[int, Source]], limit: int, order: Order,
    method: str, query: str, per_topic: int, progress: Progress, emit: Emit,
) -> int:
    total = 0

    def counted(batch: list[Post]) -> None:
        nonlocal total
        total += len(batch)
        emit(batch)

    n_topics = 0
    for topic_id, _ in topics:
        if total >= limit:
            break
        n_topics += 1
        read_topic(client, topic_id, min(per_topic, limit - total), order, method, query,
                   counted, progress)
        progress(f"Ekşi: {total}/{limit} ({n_topics} topics)")
        _pause()
    if total < limit:
        progress(f"Ekşi: ran out of topics at {total}/{limit}.")
    return total


def fetch_eksi(
    method: str, query: str, limit: int, order: Order, settings: Settings, progress: Progress,
    emit: Emit, per_topic: int | None = None,
) -> int:
    """method: 'topic' (one başlık), 'search' (keyword over titles), 'gundem' (popular now).
    Entries are passed to `emit` page by page; -> number emitted."""
    per_topic = per_topic or settings.eksi_max_per_topic
    with open_client() as client:
        if method == "topic":
            topic_id = _resolve_topic(client, query)
            total = _entries_from_topic(client, topic_id, limit, order, method, query, emit)
            progress(f"Ekşi: {total}/{limit}")
            return total

        if method == "search":
            topics = topic_pool(client, [("search", query)], progress)
            return _spread(client, topics, limit, order, method, query, per_topic, progress, emit)

        if method == "gundem":
            # gündem first, then the much longer "today" feed for big requests
            topics = topic_pool(client, [("popular",), ("today",)], progress)
            return _spread(client, topics, limit, order, method, "gündem", per_topic, progress, emit)

    raise ValueError(f"Unknown Ekşi method: {method}")
