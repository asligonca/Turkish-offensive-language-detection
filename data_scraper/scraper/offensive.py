"""Offensive-language batch: a Turkish dataset mixing offensive candidates and controls.

The labels produced here are WEAK labels (lexicon hits). They are meant as a
starting pool for human annotation, not as ground truth.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .anonymize import anonymize_text
from .models import Emit, Post
from .paths import resource_path

Progress = Callable[[str], None]

OFFENSIVE = "offensive_candidate"
CONTROL = "control"

X_QUERY_MAX = 450  # X rejects very long queries; stay well under ~512 chars
BASE_FILTER = "lang:tr -filter:retweets"
CONTROL_WORDS = ["ve", "bir", "bu", "çok", "ama", "için", "ne"]

_LEET = str.maketrans({"1": "i", "3": "e", "0": "o", "4": "a", "5": "s", "7": "t", "$": "s", "€": "e"})
_PLACEHOLDERS = re.compile(r"https?://\S+|[@#]username|\[(?:email|phone)\]")
_SPACED_LETTERS = re.compile(r"(?<!\w)(?:\w[.\-_*\s]){1,}\w(?!\w)")
_REPEATS = re.compile(r"(\w)\1{2,}")


def normalize_tr(text: str) -> str:
    """Normalize Turkish text for matching (never used for output).

    - Turkish-aware lowercasing (I -> ı, İ -> i)
    - leetspeak (k3l1me -> kelime, @ is handled after placeholders are removed)
    - spaced/dotted single letters joined (k.e.l.i.m.e -> kelime, k e l i m e -> kelime)
    - 3+ repeated letters collapsed (kelimeeee -> kelime)
    """
    text = _PLACEHOLDERS.sub(" ", text)
    text = re.sub(r"['’`]", "", text)  # allah'ın -> allahın
    text = text.replace("I", "ı").replace("İ", "i").lower()
    text = text.translate(_LEET).replace("@", "a")
    text = _SPACED_LETTERS.sub(lambda m: re.sub(r"[.\-_*\s]", "", m.group()), text)
    text = _REPEATS.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Term:
    raw: str  # as written in the lexicon (used in X queries)
    norm: str  # normalized (used for local matching)
    category: str
    whole_word: bool
    pattern: re.Pattern[str] = field(repr=False, default=None)  # type: ignore[assignment]


@dataclass
class Lexicon:
    name: str
    terms: list[Term]

    def match(self, text: str) -> list[Term]:
        norm = normalize_tr(text)
        return [t for t in self.terms if t.pattern.search(norm)]


def load_lexicon(path: str | Path) -> Lexicon:
    """One term per line. `# category` starts a category. A trailing `$` means
    whole-word only (for short/ambiguous terms, e.g. `top$` won't match "toplantı").
    Without `$` a term also matches with Turkish suffixes (kelime -> kelimeler)."""
    path = resource_path(str(path))
    if not path.is_file():
        raise FileNotFoundError(f"Lexicon not found: {path}")
    category = "uncategorized"
    terms: list[Term] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            category = line.lstrip("#").strip() or category
            continue
        whole = line.endswith("$")
        raw = line.rstrip("$").strip()
        norm = normalize_tr(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        tail = r"(?!\w)" if whole else ""
        terms.append(
            Term(raw, norm, category, whole, re.compile(rf"(?<!\w){re.escape(norm)}{tail}"))
        )
    if not terms:
        raise ValueError(f"Lexicon {path} has no terms.")
    return Lexicon(path.stem, terms)


def _quote(term: str) -> str:
    return f'"{term}"' if " " in term else term


def build_queries(terms: list[str], extra: str = "", max_len: int = X_QUERY_MAX) -> list[str]:
    """Split terms into `(a OR b OR ...) lang:tr -filter:retweets <extra>` queries under max_len."""
    suffix = f" {BASE_FILTER}" + (f" {extra.strip()}" if extra.strip() else "")
    queries: list[str] = []
    group: list[str] = []

    def render(g: list[str]) -> str:
        return f"({' OR '.join(g)}){suffix}"

    for term in (_quote(t) for t in terms):
        if group and len(render(group + [term])) > max_len:
            queries.append(render(group))
            group = []
        group.append(term)
    if group:
        queries.append(render(group))
    return queries


def build_control_query(terms: list[str], extra: str = "", max_len: int = X_QUERY_MAX) -> str:
    """Generic Turkish query that excludes as many lexicon terms as fit."""
    query = f"({' OR '.join(CONTROL_WORDS)}) {BASE_FILTER}"
    if extra.strip():
        query += f" {extra.strip()}"
    for term in terms:
        candidate = f"{query} -{_quote(term)}"
        if len(candidate) > max_len:
            break
        query = candidate
    return query


def split_counts(total: int, ratio_pct: int) -> tuple[int, int]:
    """-> (offensive, control)."""
    ratio_pct = max(0, min(100, ratio_pct))
    offensive = round(total * ratio_pct / 100)
    return offensive, total - offensive


def text_fingerprint(post: Post) -> str:
    return anonymized_fingerprint(anonymize_text(post.text, post.names))


def anonymized_fingerprint(text: str) -> str:
    """Fingerprint of already-anonymized text (e.g. rows of an existing CSV)."""
    return hashlib.sha1(normalize_tr(text).encode()).hexdigest()


def classify(posts: list[Post], lexicon: Lexicon) -> tuple[list[Post], list[Post]]:
    """Tag posts in place; -> (offensive_candidates, controls). Matching runs on anonymized text."""
    offensive, control = [], []
    for post in posts:
        hits = lexicon.match(anonymize_text(post.text, post.names))
        if hits:
            post.sample_group = OFFENSIVE
            post.matched_terms = "|".join(dict.fromkeys(t.raw for t in hits))
            post.matched_categories = "|".join(dict.fromkeys(t.category for t in hits))
            offensive.append(post)
        else:
            post.sample_group = CONTROL
            control.append(post)
    return offensive, control


def dedupe(posts: list[Post], seen: set[str]) -> list[Post]:
    out = []
    for post in posts:
        fp = text_fingerprint(post)
        if fp not in seen:
            seen.add(fp)
            out.append(post)
    return out


class _Quota:
    """Passes classified posts on to `emit` until each group is full."""

    def __init__(self, n_off: int, n_ctrl: int, emit: Emit):
        self.want = {OFFENSIVE: n_off, CONTROL: n_ctrl}
        self.have = {OFFENSIVE: 0, CONTROL: 0}
        self.emit = emit

    def full(self, group: str) -> bool:
        return self.have[group] >= self.want[group]

    def take(self, posts: list[Post]) -> None:
        keep = []
        for post in posts:
            if not self.full(post.sample_group):
                self.have[post.sample_group] += 1
                keep.append(post)
        self.emit(keep)

    @property
    def total(self) -> int:
        return sum(self.have.values())

    def done(self) -> bool:
        return self.full(OFFENSIVE) and self.full(CONTROL)


class _Absorber:
    """Turns raw fetched batches into accepted rows: drops repeated posts
    (same id, and optionally same anonymized text), tags them with lexicon
    hits, and hands them to the quota."""

    def __init__(self, lexicon: Lexicon, quota: _Quota, exclude_duplicates: bool,
                 seen_texts: Iterable[str] = ()):
        self.lexicon = lexicon
        self.quota = quota
        self.exclude_duplicates = exclude_duplicates
        self.seen_ids: set[str] = set()
        # texts already in the file being appended to
        self.seen_text: set[str] = {anonymized_fingerprint(t) for t in seen_texts} if exclude_duplicates else set()
        self.scanned = 0

    def __call__(self, label: str, controls_wanted: bool, method: str | None = None) -> Emit:
        def absorb(batch: list[Post]) -> None:
            self.scanned += len(batch)
            for p in batch:
                # short label instead of the long search string; hits are in matched_terms
                p.query = label
                if method:
                    p.method = method
            batch = [p for p in batch if not p.raw_id or p.raw_id not in self.seen_ids]
            self.seen_ids.update(p.raw_id for p in batch if p.raw_id)
            if self.exclude_duplicates:
                batch = dedupe(batch, self.seen_text)
            off, ctrl = classify(batch, self.lexicon)
            # Offensive-only sources drop posts the local matcher didn't confirm;
            # mixed sources keep both (a "control" that still contains lexicon
            # words counts as an offensive candidate).
            self.quota.take(off + (ctrl if controls_wanted else []))
        return absorb


async def collect_twitter(
    api, lexicon: Lexicon, n_off: int, n_ctrl: int, extra: str, settings, progress: Progress,
    emit: Emit, exclude_duplicates: bool = True, seen_texts: Iterable[str] = (),
) -> int:
    """Accepted posts go to `emit` chunk by chunk; -> number emitted.
    `seen_texts`: anonymized texts already in the output file (skipped as duplicates)."""
    from .twitter import AccountWatch, fetch_search

    quota = _Quota(n_off, n_ctrl, emit)
    absorber = _Absorber(lexicon, quota, exclude_duplicates, seen_texts)
    watch = AccountWatch(api, progress)

    def totals() -> str:
        return (f"offensive {quota.have[OFFENSIVE]:,}/{n_off:,}, "
                f"control {quota.have[CONTROL]:,}/{n_ctrl:,}")

    def labelled(prefix: str) -> Progress:
        # fetch_search reports "Twitter: x/limit" for *this* query only
        return lambda m: progress(f"{prefix} · {m}" if m.startswith("Twitter:") else m)

    search_terms = [t.raw for t in lexicon.terms]
    if n_off > 0:
        queries = build_queries(search_terms, extra)
        # Spread over all queries so one term group doesn't dominate; over-fetch
        # a bit to make up for duplicates and posts the local matcher rejects.
        per_query = max(5, math.ceil(n_off * 1.3 / len(queries)))
        for i, q in enumerate(queries, 1):
            if quota.full(OFFENSIVE):
                break
            progress(f"Offensive query {i}/{len(queries)} (so far: {totals()})")
            await fetch_search(api, q, per_query, "Latest", settings, labelled(f"query {i}/{len(queries)}"),
                               absorber(f"lexicon:{lexicon.name}", False), method="offensive-search",
                               watch=watch)

    if n_ctrl > 0:
        progress(f"Control query (so far: {totals()})")
        q = build_control_query(search_terms, extra)
        await fetch_search(api, q, math.ceil(n_ctrl * 1.2) + 5, "Latest", settings, labelled("control"),
                           absorber("control", True), method="control-search", watch=watch)

    progress(f"Twitter done: {totals()}")
    if quota.have[OFFENSIVE] < n_off:
        progress(f"Only {quota.have[OFFENSIVE]}/{n_off} offensive candidates found on Twitter.")
    if quota.have[CONTROL] < n_ctrl:
        progress(f"Only {quota.have[CONTROL]}/{n_ctrl} control tweets found.")
    return quota.total


EKSI_PER_TOPIC = 50  # entries read per topic in the offensive batch
EKSI_TITLE_PAGES = 3  # title-search result pages per lexicon term


def collect_eksi(
    lexicon: Lexicon, n_off: int, n_ctrl: int, settings, progress: Progress,
    emit: Emit, exclude_duplicates: bool = True, seen_texts: Iterable[str] = (),
) -> int:
    """Ekşi has no full-text search across the site, so topics are pulled from:

    1. title search for each lexicon term -> offensive candidates only (controls
       from topics *about* insults would skew the control set)
    2. gündem, then the "today" feed      -> offensive candidates and controls

    Entries are classified locally and streamed to `emit` until both quotas are
    full, the topics run out, or a safety cap on scanned entries is hit.
    """
    from .eksi import open_client, read_topic, topic_pool

    quota = _Quota(n_off, n_ctrl, emit)
    absorber = _Absorber(lexicon, quota, exclude_duplicates, seen_texts)
    max_scan = max(5000, 40 * (n_off + n_ctrl))
    n_topics = 0

    def report() -> None:
        progress(f"Ekşi: offensive {quota.have[OFFENSIVE]}/{n_off}, control {quota.have[CONTROL]}/{n_ctrl} "
                 f"(scanned {absorber.scanned} entries, {n_topics} topics)")

    capped = False

    def stop() -> bool:
        nonlocal capped
        if absorber.scanned >= max_scan:
            if not capped:
                progress(f"Ekşi: stopping at the safety cap of {max_scan} scanned entries.")
            capped = True
            return True
        return quota.done()

    with open_client() as client:
        if n_off > 0:
            sources = [("search", t.raw) for t in lexicon.terms]
            title_sink = absorber(f"lexicon:{lexicon.name}", False, "offensive-eksi-title")
            for topic_id, _ in topic_pool(client, sources, progress, max_pages=EKSI_TITLE_PAGES):
                if quota.full(OFFENSIVE) or stop():
                    break
                n_topics += 1
                read_topic(client, topic_id, EKSI_PER_TOPIC, "popular", "offensive", "", title_sink, progress)
                if n_topics % 5 == 0:
                    report()

        if not stop():
            mixed_sink = absorber("gündem", True, "offensive-gundem")
            for topic_id, _ in topic_pool(client, [("popular",), ("today",)], progress):
                if stop():
                    break
                n_topics += 1
                read_topic(client, topic_id, EKSI_PER_TOPIC, "today", "offensive", "", mixed_sink, progress)
                if n_topics % 5 == 0:
                    report()

    report()
    if quota.have[OFFENSIVE] < n_off:
        progress(f"Only {quota.have[OFFENSIVE]}/{n_off} offensive candidates found on Ekşi.")
    if quota.have[CONTROL] < n_ctrl:
        progress(f"Only {quota.have[CONTROL]}/{n_ctrl} control entries found on Ekşi.")
    return quota.total
