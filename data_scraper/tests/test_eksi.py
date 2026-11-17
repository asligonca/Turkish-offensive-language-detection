"""Ekşi topic paging and the offensive Ekşi batch, against a fake client (no network)."""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eksiapi.errors import EksiApiError  # noqa: E402

from scraper import eksi, offensive  # noqa: E402
from scraper.offensive import CONTROL, OFFENSIVE, Lexicon, Term  # noqa: E402

PAGE_SIZE = 10  # entries per topic page
TOPICS_PER_LIST_PAGE = 20


class FakeClient:
    """Topic lists: popular has 3 pages, today 30, search 2 per keyword.
    Topic ids encode their origin; topics >= 900_000 are "about" insults and
    every entry in them contains the lexicon word."""

    def __init__(self, fail_today=False, entries_per_topic=30):
        self.fail_today = fail_today
        self.entries_per_topic = entries_per_topic
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _list(self, base, page, pages):
        if page > pages:
            return {"items": [], "has_more": False}
        ids = [base + page * 100 + i for i in range(TOPICS_PER_LIST_PAGE)]
        return {"items": [{"TopicId": i} for i in ids], "has_more": page < pages}

    def popular(self, page=1):
        self.calls.append(("popular", page))
        return self._list(100_000, page, 3)

    def today(self, page=1):
        self.calls.append(("today", page))
        if self.fail_today:
            raise EksiApiError("boom")
        # page 1 repeats popular's first ids to test de-duplication
        if page == 1:
            return self._list(100_000, 1, 30)
        return self._list(200_000, page, 30)

    def search_topics(self, query, page=1):
        self.calls.append(("search", query, page))
        return self._list(900_000, page, 2)

    def page(self, payload, page=1):
        return types.SimpleNamespace(items=payload["items"], has_more=payload["has_more"])

    def topic_typed(self, topic_id, page, action=None):
        start = (page - 1) * PAGE_SIZE
        n = max(0, min(PAGE_SIZE, self.entries_per_topic - start))
        insult = topic_id >= 900_000
        entries = [
            types.SimpleNamespace(
                id=topic_id * 1000 + start + i,
                content=(f"bu kotu bir şey {topic_id}-{start + i}" if insult or (start + i) % 5 == 0
                         else f"normal entry {topic_id}-{start + i}"),
                created_at="", raw={}, author=f"yazar{topic_id}",
            )
            for i in range(n)
        ]
        pages = -(-self.entries_per_topic // PAGE_SIZE)
        return types.SimpleNamespace(entries=entries, title="t", page_count=pages)


LEXICON = Lexicon("test", [Term("kotu", "kotu", "c", False, offensive.re.compile(r"(?<!\w)kotu"))])


class EksiTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(eksi, "_pause", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)


class TopicPoolTests(EksiTestCase):
    def test_pages_until_empty_and_dedupes(self):
        ids = [tid for tid, _ in eksi.topic_pool(FakeClient(), [("popular",), ("today",)], lambda m: None)]
        self.assertEqual(len(ids), len(set(ids)))
        # 3 popular pages + 29 new today pages (today page 1 == popular page 1)
        self.assertEqual(len(ids), (3 + 29) * TOPICS_PER_LIST_PAGE)

    def test_failing_source_is_skipped(self):
        log = []
        ids = list(eksi.topic_pool(FakeClient(fail_today=True), [("today",), ("popular",)], log.append))
        self.assertEqual(len(ids), 3 * TOPICS_PER_LIST_PAGE)
        self.assertTrue(any("today" in m for m in log))

    def test_is_lazy(self):
        client = FakeClient()
        pool = eksi.topic_pool(client, [("popular",), ("today",)], lambda m: None)
        next(pool)
        self.assertEqual(client.calls, [("popular", 1)])


class FetchEksiTests(EksiTestCase):
    def test_gundem_goes_past_two_popular_pages(self):
        got = []
        settings = types.SimpleNamespace(eksi_max_per_topic=10)
        with mock.patch.object(eksi, "open_client", lambda: FakeClient()):
            n = eksi.fetch_eksi("gundem", "", 3000, "today", settings, lambda m: None, got.extend)
        self.assertEqual(n, 3000)
        self.assertEqual(len(got), 3000)


class CollectEksiTests(EksiTestCase):
    def run_collect(self, n_off, n_ctrl, client=None):
        chunks = []
        client = client or FakeClient()
        with mock.patch.object(eksi, "open_client", lambda: client):
            n = offensive.collect_eksi(LEXICON, n_off, n_ctrl, None, lambda m: None, chunks.append)
        posts = [p for c in chunks for p in c]
        return n, posts, chunks, client

    def test_reaches_quota_above_old_800_cap(self):
        n, posts, chunks, _ = self.run_collect(1500, 1500)
        self.assertEqual(n, 3000)
        self.assertEqual(sum(p.sample_group == OFFENSIVE for p in posts), 1500)
        self.assertEqual(sum(p.sample_group == CONTROL for p in posts), 1500)
        self.assertGreater(len(chunks), 1)  # streamed, not one big batch

    def test_title_topics_give_no_controls(self):
        _, posts, _, _ = self.run_collect(100, 100)
        for p in posts:
            if p.method == "offensive-eksi-title":
                self.assertEqual(p.sample_group, OFFENSIVE)
                self.assertEqual(p.query, "lexicon:test")
            else:
                self.assertEqual(p.method, "offensive-gundem")
                self.assertEqual(p.query, "gündem")

    def test_stops_once_quotas_are_full(self):
        _, _, _, client = self.run_collect(20, 20)
        # needs only a few topics: no deep paging through the today feed
        self.assertLess(sum(1 for c in client.calls if c[0] == "today"), 3)

    def test_skips_title_search_when_no_offensive_wanted(self):
        _, posts, _, client = self.run_collect(0, 50)
        self.assertFalse(any(c[0] == "search" for c in client.calls))
        self.assertEqual(len(posts), 50)


if __name__ == "__main__":
    unittest.main()
