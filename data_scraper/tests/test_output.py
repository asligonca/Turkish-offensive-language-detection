import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.anonymize import NAME_TOKEN, NameRedactor, names_pattern  # noqa: E402
from scraper.models import Post  # noqa: E402
from scraper.output import CsvStream, StreamClosed  # noqa: E402


def post(text, names=(), source="twitter", group=""):
    return Post(source=source, method="m", query="q", text=text, raw_id="x",
                names=set(names), sample_group=group)


def read_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


class CsvStreamTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_rows_hit_disk_before_finish(self):
        s = CsvStream(self.dir, "yapay zeka")
        s.add([post("bir"), post("iki")])
        self.assertEqual(len(read_rows(s.partial_path)), 2)
        s.finish()

    def test_name_seen_later_is_redacted_from_earlier_rows(self):
        s = CsvStream(self.dir, "q")
        s.add([post("Ahmet Yılmaz haklı")])
        s.add([post("ikinci", names={"Ahmet Yılmaz"})])
        rows = read_rows(s.finish())
        self.assertEqual(rows[0]["text"], f"{NAME_TOKEN} haklı")

    def test_final_name_counts_and_partial_removed(self):
        s = CsvStream(self.dir, "yapay zeka", prefix="offensive")
        s.add([post("a"), post("b")])
        s.add([post("c", source="eksisozluk")])
        path = s.finish()
        self.assertRegex(path.name, r"^\d{8}-\d{6}_offensive_tw2_eksi1_yapay-zeka\.csv$")
        self.assertFalse(s.partial_path.exists())
        self.assertEqual(sorted(p.name for p in Path(self.dir).iterdir()), [path.name])

    def test_incomplete_suffix(self):
        s = CsvStream(self.dir, "q")
        s.add([post("a")])
        self.assertTrue(s.finish(incomplete=True).name.endswith("_incomplete.csv"))

    def test_empty_stream_writes_nothing(self):
        s = CsvStream(self.dir, "q")
        self.assertIsNone(s.finish())
        self.assertEqual(list(Path(self.dir).iterdir()), [])

    def test_shuffle_renumbers_rows(self):
        s = CsvStream(self.dir, "q", shuffle=True)
        s.add([post(str(i)) for i in range(50)])
        rows = read_rows(s.finish())
        self.assertEqual([r["row"] for r in rows], [str(i) for i in range(1, 51)])
        self.assertEqual(sorted(int(r["text"]) for r in rows), list(range(50)))

    def test_add_after_finish_raises(self):
        s = CsvStream(self.dir, "q")
        s.add([post("a")])
        s.finish()
        with self.assertRaises(StreamClosed):
            s.add([post("b")])

    def test_groups_counted(self):
        s = CsvStream(self.dir, "q")
        s.add([post("a", group="offensive_candidate"), post("b", group="control"), post("c", group="control")])
        self.assertEqual(s.groups, {"offensive_candidate": 1, "control": 2})
        s.finish()


class NameRedactorTests(unittest.TestCase):
    NAMES = {"Ahmet Yılmaz", "ssg", "x.ali", "ŞEYMA", "İsmail", "😀😀😀", "pena"}
    TEXTS = [
        "ssg haklı, ahmet yılmaz da öyle dedi",
        "bak x.ali dedi", "şeyma burada", "ismail geldi", "hi 😀😀😀 there",
        "penalti", "@ssg", "#ŞEYMA", "", "hiçbiri yok",
    ]

    def test_same_result_as_single_regex(self):
        r = NameRedactor(self.NAMES)
        pattern = names_pattern(self.NAMES)
        for text in self.TEXTS:
            self.assertEqual(r.sub(text), pattern.sub(NAME_TOKEN, text), text)

    def test_add_extends(self):
        r = NameRedactor()
        self.assertEqual(r.sub("ssg haklı"), "ssg haklı")
        r.add({"ssg"})
        self.assertEqual(r.sub("ssg haklı"), f"{NAME_TOKEN} haklı")



class AppendTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def make_file(self, posts, **kw):
        s = CsvStream(self.dir, "lex", prefix="offensive", **kw)
        s.add(posts)
        return s.finish()

    def test_append_merges_and_renames_with_new_counts(self):
        old = self.make_file([post("eski 1"), post("eski 2", source="eksisozluk")])
        s = CsvStream(self.dir, "lex", prefix="offensive", append_to=old)
        s.add([post("yeni 1"), post("yeni 2")])
        new = s.finish()
        self.assertRegex(new.name, r"_offensive_tw3_eksi1_lex\.csv$")
        self.assertEqual(new.name[:15], old.name[:15])  # original timestamp kept
        self.assertEqual([p.name for p in Path(self.dir).iterdir()], [new.name])  # old one replaced
        rows = read_rows(new)
        self.assertEqual([r["text"] for r in rows], ["eski 1", "eski 2", "yeni 1", "yeni 2"])
        self.assertEqual([r["row"] for r in rows], ["1", "2", "3", "4"])

    def test_new_names_are_redacted_from_old_rows(self):
        old = self.make_file([post("Ahmet Yılmaz yine yazmış")])
        s = CsvStream(self.dir, "lex", prefix="offensive", append_to=old)
        s.add([post("ikinci", names={"Ahmet Yılmaz"})])
        rows = read_rows(s.finish())
        self.assertEqual(rows[0]["text"], f"{NAME_TOKEN} yine yazmış")

    def test_original_untouched_until_finish(self):
        old = self.make_file([post("eski")])
        before = old.read_bytes()
        s = CsvStream(self.dir, "lex", prefix="offensive", append_to=old)
        s.add([post("yeni")])
        s.close()  # e.g. the app was killed: finish() never ran
        self.assertEqual(old.read_bytes(), before)

    def test_empty_run_leaves_file_alone(self):
        old = self.make_file([post("eski")])
        s = CsvStream(self.dir, "lex", prefix="offensive", append_to=old)
        self.assertIsNone(s.finish())
        self.assertTrue(old.exists())

    def test_merges_file_with_different_columns(self):
        odd = Path(self.dir) / "20260101-000000_offensive_tw1_eksi0_lex.csv"
        odd.write_text("row,source,text,post_hash\n1,twitter,eski,abc\n", encoding="utf-8-sig")
        s = CsvStream(self.dir, "lex", prefix="offensive", append_to=odd)
        s.add([post("yeni")])
        new = s.finish()
        rows = read_rows(new)
        self.assertEqual([r["text"] for r in rows], ["eski", "yeni"])
        self.assertNotIn("post_hash", rows[0])
        self.assertRegex(new.name, r"_tw2_eksi0_")

    def test_counts_groups_and_existing_texts(self):
        old = self.make_file([post("a", group="control"), post("b", group="offensive_candidate")])
        s = CsvStream(self.dir, "lex", prefix="offensive", append_to=old)
        s.add([post("c", group="control")])
        self.assertEqual(s.file_groups(), {"control": 2, "offensive_candidate": 1})
        self.assertEqual(sorted(s.existing_texts()), ["a", "b"])
        s.finish()

    def test_list_outputs_separates_kinds_and_skips_partial(self):
        from scraper.output import list_outputs
        off = self.make_file([post("a")])
        s = CsvStream(self.dir, "q")
        s.add([post("b")])
        plain = s.finish()
        CsvStream(self.dir, "pending").close()  # leaves a .partial.csv
        self.assertEqual(list_outputs(self.dir, offensive=True), [off])
        self.assertEqual(list_outputs(self.dir, offensive=False), [plain])


if __name__ == "__main__":
    unittest.main()
