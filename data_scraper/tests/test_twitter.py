"""Account watch (ban handling) and the rate-limit log filter, with fakes (no network)."""

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper import twitter  # noqa: E402
from scraper.models import Post  # noqa: E402
from scraper.tui import library_log_text  # noqa: E402
from scraper.twitter import AccountsExhausted, AccountWatch  # noqa: E402


class FakePool:
    def __init__(self, states):
        self.states = states  # list of {username: active} snapshots, one per call
        self.calls = 0

    async def accounts_info(self):
        snap = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        return [{"username": u, "active": a, "total_req": 1, "error_msg": "" if a else "(64) suspended"}
                for u, a in snap.items()]


def fake_api(states, results=()):
    """`results`: one list of fake tweets per search call."""
    api = types.SimpleNamespace(pool=FakePool(states))
    batches = iter(results)

    async def search(q, limit, kv):
        for tw in next(batches, []):
            yield tw
    api.search = search
    return api


def run(coro):
    return asyncio.run(coro)


class AccountWatchTests(unittest.TestCase):
    def test_logs_deactivated_account_and_continues(self):
        log = []
        watch = AccountWatch(fake_api([{"a": True, "b": True}, {"a": True, "b": False}]), log.append)
        run(watch.check())
        run(watch.check())
        self.assertTrue(any("@b was deactivated" in m and "1 active" in m for m in log))

    def test_raises_when_no_account_is_left(self):
        watch = AccountWatch(fake_api([{"a": True}, {"a": False}]), lambda m: None)
        run(watch.check())
        with self.assertRaises(AccountsExhausted):
            run(watch.check())

    def test_periodic_summary(self):
        log = []
        watch = AccountWatch(fake_api([{"a": True, "b": True}]), log.append)
        for _ in range(AccountWatch.SUMMARY_EVERY):
            run(watch.check())
        self.assertTrue(any(m.startswith("[dim]Accounts: 2 active") for m in log))


class FetchSearchBanTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(twitter, "_to_post",
                                    lambda tw, q, m: Post("twitter", m, q, "t", str(tw.id)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = types.SimpleNamespace(rotate_every=2, delay_min=0, delay_max=0)

    def test_empty_chunk_with_all_accounts_banned_is_an_error_not_the_end(self):
        tweets = [types.SimpleNamespace(id=i) for i in (10, 9)]
        api = fake_api([{"a": True}, {"a": False}], results=[tweets, []])
        got = []
        with self.assertRaises(AccountsExhausted):
            run(twitter.fetch_search(api, "q", 10, "Latest", self.settings, lambda m: None, got.extend))
        self.assertEqual(len(got), 2)  # rows before the ban were emitted (saved)

    def test_empty_chunk_with_active_accounts_is_the_end(self):
        tweets = [types.SimpleNamespace(id=i) for i in (10, 9)]
        api = fake_api([{"a": True}], results=[tweets, []])
        n = run(twitter.fetch_search(api, "q", 10, "Latest", self.settings, lambda m: None, lambda b: None))
        self.assertEqual(n, 2)


class LibraryLogTextTests(unittest.TestCase):
    def test_rate_limit_wait_is_shown(self):
        text = library_log_text(
            "INFO", 'No account available for queue "SearchTimeline". Next available at 2026-11-17 01:22:10+00:00')
        self.assertIn("rate limit", text)
        self.assertIn("01:22", text)

    def test_other_info_is_hidden(self):
        self.assertIsNone(library_log_text("INFO", "Account x added successfully (active=True)"))

    def test_warnings_pass(self):
        self.assertIn("Ban detected", library_log_text("WARNING", "Ban detected: 403 - ..."))


if __name__ == "__main__":
    unittest.main()
