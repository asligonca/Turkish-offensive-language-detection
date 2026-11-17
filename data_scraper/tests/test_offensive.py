import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.models import Post  # noqa: E402
from scraper.offensive import (  # noqa: E402
    CONTROL,
    OFFENSIVE,
    X_QUERY_MAX,
    build_control_query,
    build_queries,
    classify,
    dedupe,
    load_lexicon,
    normalize_tr,
    split_counts,
)

LEXICON = load_lexicon(ROOT / "lexicons" / "tr_offensive.txt")


def post(text: str, names=()) -> Post:
    return Post(source="twitter", method="t", query="q", text=text, raw_id=text, names=set(names))


class NormalizeTests(unittest.TestCase):
    def test_turkish_casefold(self):
        self.assertEqual(normalize_tr("IŞIK İSTANBUL"), "ışık istanbul")

    def test_leetspeak(self):
        self.assertEqual(normalize_tr("s1kt1r"), "siktir")

    def test_dotted_letters(self):
        self.assertEqual(normalize_tr("a.q bu ne"), "aq bu ne")
        self.assertEqual(normalize_tr("o.ç"), "oç")

    def test_repeated_letters(self):
        self.assertEqual(normalize_tr("siktirrrrr"), "siktir")

    def test_placeholders_removed(self):
        self.assertEqual(normalize_tr("@username selam #username"), "selam")

    def test_normal_words_untouched(self):
        self.assertEqual(normalize_tr("bu bir test"), "bu bir test")


class MatchTests(unittest.TestCase):
    def terms(self, text):
        return [t.raw for t in LEXICON.match(text)]

    def test_suffix_match(self):
        self.assertIn("şerefsiz", self.terms("Şerefsizler hepsi"))

    def test_whole_word_only(self):
        self.assertEqual(self.terms("bunu götürmek lazım"), [])
        self.assertIn("göt", self.terms("göt herif"))

    def test_obfuscated(self):
        self.assertIn("aq", self.terms("a.q ne biçim iş"))
        self.assertIn("siktir", self.terms("S1KT1RRRR git"))

    def test_clean_text(self):
        self.assertEqual(self.terms("bugün hava çok güzel, boksör maçı izledik"), [])

    def test_categories_loaded(self):
        cats = {t.category for t in LEXICON.terms}
        self.assertEqual(cats, {"profanity", "insult", "identity-based", "curse", "threat"})

    def test_new_hits(self):
        cases = {
            "tam bir serefsiz": "insult",
            "amına koyim ya": "profanity",
            "orosbu çocuğu": "profanity",
            "mal herif yine yaptı": "insult",
            "seni öldürürüm bak": "threat",
            "geberesice herif": "curse",
            "Allah'ın belası": "curse",
            "yobazlar": "identity-based",
        }
        for text, category in cases.items():
            with self.subTest(text=text):
                self.assertIn(category, {t.category for t in LEXICON.match(text)})

    def test_false_positive_guards(self):
        for text in (
            "sık sık geliyor", "sıkıntılı bir gün", "mal varlığı açıklandı", "alçak tavanlı ev",
            "bunu götürdüm", "piccolo çaldı", "got it", "aşağılık kompleksi var",
            "boynuzlu geyik", "yerde pislik var", "ezik domates", "sıkışık trafik",
            "kekolar değil kek aldım", "malzemeler hazır",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.terms(text), [])

    def test_full_lexicon_queries_fit(self):
        queries = build_queries([t.raw for t in LEXICON.terms], "since:2026-01-01")
        self.assertTrue(all(len(q) <= X_QUERY_MAX for q in queries))


class QueryTests(unittest.TestCase):
    def test_split_under_limit(self):
        terms = [f"kelime{i}" for i in range(200)]
        queries = build_queries(terms, "since:2026-01-01")
        self.assertGreater(len(queries), 1)
        for q in queries:
            self.assertLessEqual(len(q), X_QUERY_MAX)
            self.assertIn("lang:tr", q)
            self.assertIn("since:2026-01-01", q)
        joined = " ".join(queries)
        self.assertTrue(all(f"kelime{i} " in joined or f"kelime{i})" in joined for i in range(200)))

    def test_multiword_quoted(self):
        self.assertIn('"geri zekalı"', build_queries(["geri zekalı"])[0])

    def test_control_query(self):
        q = build_control_query([t.raw for t in LEXICON.terms])
        self.assertLessEqual(len(q), X_QUERY_MAX)
        self.assertIn("-amk", q)

    def test_split_counts(self):
        self.assertEqual(split_counts(100, 50), (50, 50))
        self.assertEqual(split_counts(10, 33), (3, 7))
        self.assertEqual(split_counts(10, 150), (10, 0))


class ClassifyTests(unittest.TestCase):
    def test_classify_and_tags(self):
        a, b = post("aq çok kötü"), post("güzel bir gün")
        off, ctrl = classify([a, b], LEXICON)
        self.assertEqual((off, ctrl), ([a], [b]))
        self.assertEqual(a.sample_group, OFFENSIVE)
        self.assertEqual(a.matched_terms, "aq")
        self.assertEqual(a.matched_categories, "profanity")
        self.assertEqual(b.sample_group, CONTROL)

    def test_usernames_not_matched(self):
        # a username that looks like a lexicon word is anonymized before matching
        off, _ = classify([post("@salak123 merhaba", names={"salak123"})], LEXICON)
        self.assertEqual(off, [])

    def test_dedupe(self):
        posts = [post("Aynı metin!!"), post("aynı   metin!!"), post("başka")]
        self.assertEqual(len(dedupe(posts, set())), 2)



class ExistingTextDedupeTests(unittest.TestCase):
    def test_text_already_in_file_is_skipped(self):
        from scraper.offensive import _Absorber, _Quota, Lexicon, Term
        import re as _re
        lex = Lexicon("t", [Term("kotu", "kotu", "c", False, _re.compile(r"(?<!\w)kotu"))])
        got = []
        quota = _Quota(10, 10, got.extend)
        absorb = _Absorber(lex, quota, True, seen_texts=["bu kotu bir şey"])("lbl", True)
        absorb([Post(source="twitter", method="m", query="q", text="Bu KOTU bir şey", raw_id="1"),
                Post(source="twitter", method="m", query="q", text="yepyeni", raw_id="2")])
        self.assertEqual([p.text for p in got], ["yepyeni"])


if __name__ == "__main__":
    unittest.main()
