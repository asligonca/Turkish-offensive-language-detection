import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.anonymize import anonymize_query, anonymize_text  # noqa: E402
from scraper.output import slugify  # noqa: E402


class AnonymizeTests(unittest.TestCase):
    def test_twitter_mentions(self):
        self.assertEqual(
            anonymize_text("RT @elonmusk: hi @ali_veli and @AhmetYılmaz!"),
            "RT @username: hi @username and @username!",
        )

    def test_eksi_bkz_with_spaces(self):
        self.assertEqual(
            anonymize_text("katılıyorum (bkz: @deli dolu yazar) aynen"),
            "katılıyorum (bkz: @username) aynen",
        )

    def test_known_names_bare(self):
        out = anonymize_text("ssg haklı, Ahmet Yılmaz da öyle dedi", {"ssg", "Ahmet Yılmaz"})
        self.assertEqual(out, "#username haklı, #username da öyle dedi")

    def test_known_name_not_inside_words(self):
        self.assertEqual(anonymize_text("penalti", {"pena"}), "penalti")

    def test_short_names_ignored(self):
        self.assertEqual(anonymize_text("ab cd", {"ab"}), "ab cd")

    def test_email_and_phone(self):
        out = anonymize_text("yaz: foo.bar@example.com veya +90 532 123 45 67")
        self.assertEqual(out, "yaz: [email] veya [phone]")

    def test_year_not_phone(self):
        self.assertEqual(anonymize_text("2026 yılında 15 kişi"), "2026 yılında 15 kişi")

    def test_profile_and_status_urls(self):
        out = anonymize_text(
            "see https://x.com/someone/status/123 and https://eksisozluk.com/biri/ssg"
        )
        self.assertEqual(out, "see https://x.com/username and https://eksisozluk.com/biri/username")

    def test_hashtag_and_search_urls_kept(self):
        text = "#python https://x.com/hashtag/python"
        self.assertEqual(anonymize_text(text), text)

    def test_html_stripped(self):
        self.assertEqual(anonymize_text("a <b>kalın</b> &amp; b"), "a kalın & b")

    def test_query_operators(self):
        self.assertEqual(anonymize_query("from:someone to:other kedi"), "from:username to:username kedi")

    def test_slugify(self):
        self.assertEqual(slugify("Yapay Zekâ lang:tr", "çılgın"), "yapay-zeka-lang-tr-cilgin")
        self.assertEqual(slugify("https://eksisozluk.com/python--109286"), "python-109286")
        self.assertEqual(slugify(""), "posts")


if __name__ == "__main__":
    unittest.main()
