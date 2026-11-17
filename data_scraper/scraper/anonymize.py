"""Anonymization helpers.

Rules:
- @mentions (Twitter and Ekşi)            -> @username
- (bkz: @nick) / (ara: @nick) on Ekşi       -> (bkz: @username)
- bare occurrences of known usernames or
  display names seen during the run        -> #username
- profile / status links on x.com, twitter.com,
  eksisozluk.com/biri/...                  -> generic link
- e-mail addresses and phone numbers       -> [email] / [phone]
- user ids and post ids are never written, not even as hashes
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable

MENTION_TOKEN = "@username"
NAME_TOKEN = "#username"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
_EKSI_REF_RE = re.compile(r"\((bkz|ara|u)\s*:\s*@[^)]*\)", re.IGNORECASE)
_MENTION_RE = re.compile(r"(?<![\w@])@\w+")
_X_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(?!i/|hashtag/|search|home|intent/)\w+\S*",
    re.IGNORECASE,
)
_EKSI_USER_URL_RE = re.compile(r"https?://(?:www\.)?eksisozluk\.com/biri/\S+", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")

MIN_USERNAME_LEN = 3
MIN_DISPLAYNAME_LEN = 4


def clean_markup(text: str) -> str:
    """Strip simple HTML tags/entities that some APIs return."""
    return html.unescape(_HTML_TAG_RE.sub("", text or ""))


def names_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    usable = sorted(
        {n.strip() for n in names if n and len(n.strip()) >= MIN_USERNAME_LEN},
        key=len,
        reverse=True,  # longest first so "ali veli" wins over "ali"
    )
    if not usable:
        return None
    alternation = "|".join(re.escape(n) for n in usable)
    return re.compile(rf"(?<![\w@#])(?:{alternation})(?!\w)", re.IGNORECASE)


_WORD_RE = re.compile(r"\w+")


def _fold(token: str) -> str:
    """Aggressive case/accent folding for the candidate index. Over-matching is
    harmless (the real regex decides); it must never under-match."""
    token = unicodedata.normalize("NFKD", token.casefold()).replace("ı", "i")
    return "".join(c for c in token if not unicodedata.combining(c))


class NameRedactor:
    """Replaces bare usernames/display names with NAME_TOKEN.

    One regex over tens of thousands of names costs ~10 ms per text, so instead
    the names are indexed by their first word: for each text only the names
    whose first word occurs in it are put into the regex. Same matches as
    `names_pattern(all_names)`, far less work.
    """

    def __init__(self, names: Iterable[str] = ()):
        self._index: dict[str, set[str]] = {}
        self._always: set[str] = set()  # names without any word character
        self.add(names)

    def add(self, names: Iterable[str]) -> None:
        for name in names:
            name = (name or "").strip()
            if len(name) < MIN_USERNAME_LEN:
                continue
            m = _WORD_RE.search(name)
            if m:
                self._index.setdefault(_fold(m.group()), set()).add(name)
            else:
                self._always.add(name)

    def sub(self, text: str) -> str:
        if not text:
            return text
        candidates = set(self._always)
        for tok in {_fold(t) for t in _WORD_RE.findall(text)}:
            candidates |= self._index.get(tok, set())
        if not candidates:
            return text
        pattern = names_pattern(candidates)
        return pattern.sub(NAME_TOKEN, text) if pattern else text


def anonymize_text(
    text: str, known_names: Iterable[str] = (), redactor: NameRedactor | None = None
) -> str:
    """Pass a reusable `redactor` instead of `known_names` when anonymizing many
    texts against the same name set."""
    text = clean_markup(text)
    text = _X_URL_RE.sub("https://x.com/username", text)
    text = _EKSI_USER_URL_RE.sub("https://eksisozluk.com/biri/username", text)
    text = _EMAIL_RE.sub("[email]", text)  # before mentions, so foo@bar.com isn't a mention
    text = _EKSI_REF_RE.sub(lambda m: f"({m.group(1).lower()}: {MENTION_TOKEN})", text)
    text = _MENTION_RE.sub(MENTION_TOKEN, text)
    text = (redactor or NameRedactor(known_names)).sub(text)
    text = _PHONE_RE.sub("[phone]", text)
    return text


_OPERATOR_RE = re.compile(r"\b(from|to|list|url):\S+", re.IGNORECASE)


def anonymize_query(
    query: str, known_names: Iterable[str] = (), redactor: NameRedactor | None = None
) -> str:
    """Search queries can name accounts too (from:someone, @someone)."""
    query = _OPERATOR_RE.sub(lambda m: f"{m.group(1).lower()}:username", query)
    return anonymize_text(query, known_names, redactor)

