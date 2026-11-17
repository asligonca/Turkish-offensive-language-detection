"""Twitter/X scraping via twscrape, with account rotation."""

from __future__ import annotations

import asyncio
import os
import random
import re
import tempfile
from collections.abc import AsyncIterator, Callable

from twscrape import API, NoAccountError, set_log_level

from .config import Settings
from .models import Emit, Post

set_log_level("WARNING")

Progress = Callable[[str], None]

# value -> (label, input label, placeholder)
TW_METHODS: dict[str, tuple[str, str, str]] = {
    "search_latest": ("Search (latest)", "Query", "yapay zeka lang:tr -filter:retweets"),
    "search_top": ("Search (top)", "Query", "yapay zeka lang:tr"),
    "user": ("A user's tweets", "Handle", "@someaccount"),
    "user_replies": ("A user's tweets + replies", "Handle", "@someaccount"),
    "replies": ("Replies to a tweet", "Tweet URL", "https://x.com/.../status/123..."),
    "list": ("List timeline", "List URL", "https://x.com/i/lists/123..."),
    "trend": ("Trending topic", "Trend", ""),
}

# What goes in the CSV `query` column: never the target account or tweet.
_QUERY_LABEL = {
    "user": "@username",
    "user_replies": "@username",
    "replies": "tweet-replies",
    "list": "list",
}


class ScrapeError(Exception):
    """A user-facing error (bad input, no accounts, ...)."""


class AccountsExhausted(ScrapeError):
    """Every account in the pool is deactivated (banned, suspended, logged out)."""


class AccountWatch:
    """Keeps an eye on the account pool during a run.

    twscrape marks banned/expired accounts inactive by itself and moves on to the
    others. When *all* of them are gone, a search just returns nothing, which
    would look like "no more results". This turns that into a clear stop.
    Usernames here are the user's own scraping accounts and only go to the log.
    """

    SUMMARY_EVERY = 10  # chunks

    def __init__(self, api: API, progress: Progress):
        self.api = api
        self.progress = progress
        self._active: set[str] | None = None
        self._checks = 0

    async def check(self) -> None:
        info = await self.api.pool.accounts_info()
        active = {a["username"] for a in info if a["active"]}
        if self._active is not None:
            for a in info:
                if a["username"] in self._active - active:
                    reason = a["error_msg"] if a["error_msg"] not in ("", "None") else "no reason given"
                    self.progress(f"[yellow]Account @{a['username']} was deactivated: {reason}. "
                                  f"Continuing with {len(active)} active.[/yellow]")
        self._active = active
        self._checks += 1
        if not active:
            raise AccountsExhausted(
                "All Twitter accounts are deactivated (banned or logged out). "
                "Stopping; collected rows are saved."
            )
        if self._checks % self.SUMMARY_EVERY == 0:
            reqs = " ".join(f"{a['username']}={a['total_req']}" for a in info if a["active"])
            self.progress(f"[dim]Accounts: {len(active)} active · requests {reqs}[/dim]")


async def build_api(settings: Settings, progress: Progress) -> API:
    """Mirror the accounts in .env into twscrape's pool and log them in."""
    api = API(settings.twscrape_db, raise_when_no_account=False)
    # Least-recently-used first => every new search chunk goes to a different
    # account (round-robin) instead of always hammering the first one.
    api.pool._order_by = "last_used ASC"

    wanted = {a.username for a in settings.accounts}
    existing = {acc.username for acc in await api.pool.get_all()}
    if stale := existing - wanted:
        await api.pool.delete_accounts(list(stale))
    if not settings.accounts:
        raise ScrapeError("No Twitter accounts yet. Add one in the Accounts tab.")

    for acc in settings.accounts:
        if acc.username not in existing:
            await api.pool.add_account(
                acc.username,
                acc.password or "_",
                acc.email or "_",
                acc.email_password or "_",
                proxy=acc.proxy or None,
                cookies=acc.cookies or None,
                mfa_code=acc.mfa_code or None,
            )
        if acc.cookies:  # keep stored cookies in sync with .env
            await api.pool.add_account_cookies(acc.username, acc.cookies)

    await api.pool.login_all()

    active = [a for a in await api.pool.accounts_info() if a["active"]]
    if not active:
        raise ScrapeError(
            "No Twitter account could log in. Cookie login (auth_token + ct0) is the most "
            "reliable option; see .env.example."
        )
    progress(f"Twitter: {len(active)}/{len(settings.accounts)} account(s) active.")
    return api


async def account_rows(api: API) -> list[tuple[str, str, int, str]]:
    """(username, state, requests, error) — never cookies or passwords."""
    return [
        (a["username"], "active" if a["active"] else "inactive", a["total_req"], a["error_msg"] or "")
        for a in await api.pool.accounts_info()
    ]


async def check_account(settings: Settings, username: str, timeout: float = 60) -> tuple[bool, str]:
    """Really try the account: a throwaway pool with only this account looks up @X.

    The real accounts.db and the other accounts are not touched.
    """
    acc = next((a for a in settings.accounts if a.username.lower() == username.lower()), None)
    if acc is None:
        return False, "account not found in .env"
    if not acc.cookies:
        return False, "no cookies saved for this account"
    with tempfile.TemporaryDirectory() as tmp:
        api = API(os.path.join(tmp, "check.db"), raise_when_no_account=True)
        await api.pool.add_account(
            acc.username, acc.password or "_", acc.email or "_", acc.email_password or "_",
            proxy=acc.proxy or None, cookies=acc.cookies,
        )
        try:
            user = await asyncio.wait_for(api.user_by_login("X"), timeout)
        except NoAccountError:
            user = None
        except TimeoutError:
            return False, f"no answer within {int(timeout)}s (rate limit or network/proxy problem)"
        except Exception as exc:  # network errors, parse errors, ...
            return False, f"{exc.__class__.__name__}: {exc}"
        if user is not None:
            return True, "works"
        info = await api.pool.accounts_info()
        error = info[0]["error_msg"] if info and info[0]["error_msg"] not in ("", "None") else ""
        return False, error or "X rejected the cookies (expired, logged out, or copied wrong?)"


async def load_trends(api: API, limit: int = 30) -> list[str]:
    names: list[str] = []
    async for trend in api.trends("trending", limit=limit):
        if trend.name and trend.name not in names:
            names.append(trend.name)
    return names


def _to_post(tw, query: str, method: str) -> Post:
    names = {tw.user.username, tw.user.displayname}
    for ref in tw.mentionedUsers or []:
        names.update({ref.username, ref.displayname})
    if tw.inReplyToUser:
        names.update({tw.inReplyToUser.username, tw.inReplyToUser.displayname})
    for nested in (tw.retweetedTweet, tw.quotedTweet):
        if nested:
            names.update({nested.user.username, nested.user.displayname})

    if tw.retweetedTweet:
        post_type = "retweet"
    elif tw.inReplyToTweetId:
        post_type = "reply"
    elif tw.quotedTweet:
        post_type = "quote"
    else:
        post_type = "original"

    return Post(
        source="twitter",
        method=method,
        query=query,
        text=tw.rawContent,
        raw_id=tw.id_str,
        created_at=tw.date.isoformat(),
        lang=tw.lang or "",
        like_count=tw.likeCount,
        repost_count=tw.retweetCount,
        reply_count=tw.replyCount,
        quote_count=tw.quoteCount,
        view_count=tw.viewCount if tw.viewCount is not None else "",
        hashtags=" ".join(f"#{h}" for h in tw.hashtags or []),
        post_type=post_type,
        names={n for n in names if n},
    )


def _numeric_id(text: str, pattern: str, what: str) -> int:
    text = text.strip()
    if text.isdigit():
        return int(text)
    if m := re.search(pattern, text):
        return int(m.group(1))
    raise ScrapeError(f"Could not find a {what} id in: {text!r}")


async def fetch_search(
    api: API, query: str, limit: int, product: str, settings: Settings, progress: Progress,
    emit: Emit, method: str | None = None, watch: AccountWatch | None = None,
) -> int:
    """Search in chunks of TW_ROTATE_EVERY; -> number of posts emitted.

    Each chunk opens a fresh search, so twscrape picks the least-recently-used
    account for it, with a random pause between chunks. twscrape also switches
    accounts by itself whenever one gets rate-limited. Every chunk is passed to
    `emit` as soon as it is complete.
    """
    method = method or f"search-{product.lower()}"
    watch = watch or AccountWatch(api, progress)
    seen: set[int] = set()
    total = 0
    max_id: int | None = None

    while total < limit:
        chunk = min(settings.rotate_every, limit - total)
        q = query if max_id is None else f"{query} max_id:{max_id}"
        batch: list[Post] = []
        try:
            async for tw in api.search(q, limit=chunk, kv={"product": product}):
                if tw.id in seen:
                    continue
                seen.add(tw.id)
                batch.append(_to_post(tw, query, method))
                if len(batch) >= chunk:
                    break
        finally:
            emit(batch)  # keep what this chunk got even if the search failed
            total += len(batch)
        progress(f"Twitter: {total}/{limit}")
        await watch.check()  # raises if every account is gone, so empty != "no results"
        if not batch:
            break  # no more results
        max_id = min(seen) - 1
        if total < limit:
            await asyncio.sleep(random.uniform(settings.delay_min, settings.delay_max))
    return total


async def _collect(
    gen: AsyncIterator, limit: int, query: str, method: str, progress: Progress, emit: Emit,
    watch: AccountWatch,
) -> int:
    batch: list[Post] = []
    seen: set[int] = set()
    total = 0
    try:
        async for tw in gen:
            if tw.id in seen:
                continue
            seen.add(tw.id)
            batch.append(_to_post(tw, query, method))
            total += 1
            if len(batch) >= 20:
                emit(batch)
                batch = []
                progress(f"Twitter: {total}/{limit}")
                await watch.check()
            if total >= limit:
                break
    finally:
        emit(batch)
    progress(f"Twitter: {total}/{limit}")
    await watch.check()
    return total


async def fetch_tweets(
    api: API, method: str, target: str, limit: int, settings: Settings, progress: Progress,
    emit: Emit,
) -> int:
    """Dispatch on the method chosen in the UI (see TW_METHODS); -> posts emitted."""
    target = target.strip()
    watch = AccountWatch(api, progress)
    if method in ("search_latest", "search_top"):
        product = "Latest" if method == "search_latest" else "Top"
        return await fetch_search(api, target, limit, product, settings, progress, emit, watch=watch)

    if method == "trend":
        # trend searches support the same chunked rotation
        return await fetch_search(api, target, limit, "Top", settings, progress, emit, method="trend",
                                  watch=watch)

    label = _QUERY_LABEL[method]
    if method in ("user", "user_replies"):
        handle = re.sub(r"^https?://(www\.)?(x|twitter)\.com/", "", target).lstrip("@").split("/")[0]
        user = await api.user_by_login(handle)
        if user is None:
            raise ScrapeError("That account was not found (or is private/suspended).")
        fn = api.user_tweets if method == "user" else api.user_tweets_and_replies
        return await _collect(fn(user.id, limit=limit), limit, label, method, progress, emit, watch)

    if method == "replies":
        twid = _numeric_id(target, r"/status(?:es)?/(\d+)", "tweet")
        return await _collect(api.tweet_replies(twid, limit=limit), limit, label, method, progress, emit, watch)

    if method == "list":
        list_id = _numeric_id(target, r"/lists/(\d+)", "list")
        return await _collect(api.list_timeline(list_id, limit=limit), limit, label, method, progress, emit, watch)

    raise ScrapeError(f"Unknown Twitter method: {method}")
