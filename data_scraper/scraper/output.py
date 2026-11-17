"""CSV writing and file naming.

Naming format:  <output_dir>/YYYYMMDD-HHMMSS[_<prefix>]_tw<N>_eksi<M>_<slug>[_incomplete].csv
  e.g.          data/20261115-143002_tw10_eksi2_yapay-zeka.csv
                data/20261115-150510_offensive_tw100_eksi40_tr-offensive.csv

- timestamp first  -> files sort chronologically in `ls`
- tw/eksi counts   -> the actual number of rows from each source
- slug             -> ASCII version of the (anonymized) search terms, max 40 chars
- _incomplete      -> the run stopped early (error or quit); rows collected so far

While a run is going, rows are streamed to <stamp>[_<prefix>]_<slug>.partial.csv.
"""

from __future__ import annotations

import csv
import random
import re
import threading
from datetime import datetime
from pathlib import Path

from .anonymize import NameRedactor, anonymize_query, anonymize_text
from .models import CSV_COLUMNS, Post

_TR = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")


def slugify(*parts: str, max_len: int = 40) -> str:
    text = " ".join(p for p in parts if p).translate(_TR).lower()
    text = re.sub(r"(https?://)?\S*eksisozluk\.com/", "", text)
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug[:max_len].rstrip("-") or "posts"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def build_filename(
    output_dir: str, n_tw: int, n_eksi: int, *terms: str, prefix: str = "",
    stamp: str | None = None, suffix: str = "",
) -> Path:
    stamp = stamp or _stamp()
    prefix = f"_{prefix}" if prefix else ""
    slug = slugify(*(anonymize_query(t) for t in terms))
    return Path(output_dir) / f"{stamp}{prefix}_tw{n_tw}_eksi{n_eksi}_{slug}{suffix}.csv"


_NAME_RE = re.compile(
    r"^(?P<stamp>\d{8}-\d{6})(?P<prefix>_[a-z]+)?_tw\d+_eksi\d+_(?P<slug>.+?)(?:_incomplete)?\.csv$"
)


def list_outputs(output_dir: str, offensive: bool) -> list[Path]:
    """Finished CSVs that a run can append to, newest first. Offensive batches
    and normal scrapes are kept apart (they have different column use)."""
    folder = Path(output_dir)
    if not folder.is_dir():
        return []
    files = [
        f for f in folder.glob("*.csv")
        if not f.name.endswith(".partial.csv") and ("_offensive_" in f.name) == offensive
    ]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


class StreamClosed(RuntimeError):
    """Raised by CsvStream.add after finish(); stops fetchers still running in a
    worker thread (e.g. Ekşi) once the run has been cancelled."""


class CsvStream:
    """Writes anonymized rows to disk while a run is still going.

    Rows go to `<stamp>[_prefix]_<slug>.partial.csv` as they arrive, already
    anonymized with every name seen so far. `finish()` then re-runs the bare-name
    redaction with the full name set (so a name first seen late in the run is
    also removed from earlier rows), optionally shuffles, and renames the file
    to the usual `..._tw<N>_eksi<M>_...` form. Raw text never reaches disk.

    With `append_to`, the new rows are merged into that existing CSV at
    finish(). The existing file is not touched before then, so a crash or a
    stop mid-run leaves it as it was.
    """

    def __init__(
        self, output_dir: str, *terms: str, prefix: str = "", shuffle: bool = False,
        append_to: Path | None = None,
    ):
        self.output_dir = output_dir
        self.terms = terms
        self.prefix = prefix
        self.shuffle = shuffle
        self.stamp = _stamp()
        self.counts = {"twitter": 0, "eksisozluk": 0}
        self.groups: dict[str, int] = {}
        self._redactor = NameRedactor()  # every name seen so far in this run
        self._lock = threading.Lock()

        # rows already in the file we append to (counted, not kept in memory)
        self.append_to = Path(append_to) if append_to else None
        self.base_counts = {"twitter": 0, "eksisozluk": 0}
        self.base_groups: dict[str, int] = {}
        if self.append_to:
            for r in _read_rows(self.append_to):
                src = r.get("source", "")
                self.base_counts[src] = self.base_counts.get(src, 0) + 1
                if g := r.get("sample_group"):
                    self.base_groups[g] = self.base_groups.get(g, 0) + 1

        pre = f"_{prefix}" if prefix else ""
        slug = slugify(*(anonymize_query(t) for t in terms))
        self.partial_path = Path(output_dir) / f"{self.stamp}{pre}_{slug}.partial.csv"
        self.partial_path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig so Excel/LibreOffice open Turkish characters correctly
        self._fh = self.partial_path.open("w", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_COLUMNS)
        self._writer.writeheader()
        self._fh.flush()

    @property
    def total(self) -> int:
        """Rows added in this run."""
        return sum(self.counts.values())

    def file_counts(self) -> dict[str, int]:
        """Rows per source in the finished file (existing + this run)."""
        keys = set(self.counts) | set(self.base_counts)
        return {k: self.counts.get(k, 0) + self.base_counts.get(k, 0) for k in keys}

    def file_groups(self) -> dict[str, int]:
        keys = set(self.groups) | set(self.base_groups)
        return {k: self.groups.get(k, 0) + self.base_groups.get(k, 0) for k in keys}

    def existing_texts(self) -> list[str]:
        """Anonymized texts already in the file we append to (for de-duplication)."""
        return [r.get("text", "") for r in _read_rows(self.append_to)] if self.append_to else []

    def __enter__(self) -> "CsvStream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def add(self, posts: list[Post]) -> None:
        with self._lock:
            if self._fh.closed:
                raise StreamClosed("output file already finished")
            for p in posts:
                self._redactor.add(p.names)
            for p in posts:
                self.counts[p.source] = self.counts.get(p.source, 0) + 1
                if p.sample_group:
                    self.groups[p.sample_group] = self.groups.get(p.sample_group, 0) + 1
                self._writer.writerow(_row(p, self.total, self._redactor))
            self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def finish(self, incomplete: bool = False) -> Path | None:
        """Finalize the file; -> its path, or None if nothing was written."""
        with self._lock:
            self.close()
            if self.total == 0:
                self.partial_path.unlink(missing_ok=True)
                return None
            rows = (_read_rows(self.append_to) if self.append_to else []) + _read_rows(self.partial_path)
            if self.shuffle:
                random.shuffle(rows)
            final = self._final_path(incomplete)
            tmp = final.with_suffix(".tmp")
            with tmp.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, restval="", extrasaction="ignore")
                writer.writeheader()
                for i, r in enumerate(rows, start=1):
                    r["row"] = i
                    # names first seen after this row was written (old rows too)
                    r["text"] = self._redactor.sub(r.get("text") or "")
                    r["query"] = self._redactor.sub(r.get("query") or "")
                    writer.writerow(r)
            tmp.replace(final)
            if self.append_to and self.append_to != final:
                self.append_to.unlink(missing_ok=True)  # only after the merged file is in place
            self.partial_path.unlink(missing_ok=True)
            return final

    def _final_path(self, incomplete: bool) -> Path:
        suffix = "_incomplete" if incomplete else ""
        counts = self.file_counts()
        n_tw, n_eksi = counts.get("twitter", 0), counts.get("eksisozluk", 0)
        if self.append_to:
            m = _NAME_RE.match(self.append_to.name)
            if not m:  # renamed by hand: keep the user's name
                return self.append_to
            return self.append_to.with_name(
                f"{m['stamp']}{m['prefix'] or ''}_tw{n_tw}_eksi{n_eksi}_{m['slug']}{suffix}.csv"
            )
        return build_filename(self.output_dir, n_tw, n_eksi, *self.terms, prefix=self.prefix,
                              stamp=self.stamp, suffix=suffix)


def _row(p: Post, row: int, redactor: NameRedactor) -> dict:
    return {
        "row": row,
        "source": p.source,
        "method": p.method,
        "query": anonymize_query(p.query, redactor=redactor),
        "topic": p.topic,
        "text": anonymize_text(p.text, redactor=redactor),
        "created_at": p.created_at,
        "lang": p.lang,
        "like_count": p.like_count,
        "repost_count": p.repost_count,
        "reply_count": p.reply_count,
        "quote_count": p.quote_count,
        "view_count": p.view_count,
        "favorite_count": p.favorite_count,
        "hashtags": p.hashtags,
        "post_type": p.post_type,
        "sample_group": p.sample_group,
        "matched_terms": p.matched_terms,
        "matched_categories": p.matched_categories,
    }


def write_csv(posts: list[Post], path: Path) -> None:
    """Write a complete list in one go (kept for small/one-off use and tests)."""
    redactor = NameRedactor()
    for p in posts:
        redactor.add(p.names)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for i, p in enumerate(posts, start=1):
            writer.writerow(_row(p, i, redactor))
