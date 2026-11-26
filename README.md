# anonymized-tweet-scraper

A terminal app that pulls posts from **Twitter/X** (via [twscrape](https://github.com/vladkens/twscrape)) and **Ekşi Sözlük** (via Ekşi's own mobile API, using the [`eksiapi`](https://github.com/agmmnn/eksiapi) client). It anonymizes them and writes everything into one CSV file.

- **Two sources, one CSV**: Twitter/X and Ekşi Sözlük posts side by side, with the same columns.
- **Anonymized by default**: mentions, usernames, profile links, e-mails and phone numbers are redacted before anything is written.
- **Full-screen terminal UI**: no command-line flags to remember. Works with mouse and keyboard.
- **Offensive-language batch**: builds a Turkish ML dataset that mixes lexicon-matched candidates with control posts.
- **Account rotation**: spreads Twitter requests across several throwaway accounts and waits out rate limits on its own.
- **Long runs are safe**: rows stream to disk, and a stopped or crashed run still saves what it collected.

## Disclaimer

> [!WARNING]
> This project is intended **for academic and research purposes only**.
>
> - Scraping Twitter/X (and Ekşi Sözlük) may **not comply with the platforms' Terms of Service**. By using this software you acknowledge this and accept full responsibility for how you use it.
> - Do **not** use it for malicious purposes, including harassment, surveillance or profiling of individuals, spam, or any activity that violates the law or the rights of others.
> - You are responsible for following applicable data-protection laws (e.g. KVKK, GDPR) when collecting, storing, and sharing data.
> - The software is provided "as is", without warranty (see [LICENSE](LICENSE)). The author is not liable for any misuse or for any consequences, such as account suspensions.

## Quick start

Ekşi Sözlük works right away with no setup. Twitter/X needs at least one throwaway account (see [Twitter accounts](#twitter-accounts)).

Commands are shown for Ubuntu/Debian. On other systems, create a Python virtual environment the usual way.

```bash
sudo apt install python3-venv          # on Python 3.14 the package may be python3.14-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m scraper
```

Later runs only need `source .venv/bin/activate` and `python -m scraper`. If you don't want to install Python, build a [standalone executable](#standalone-executable).

## Twitter accounts

Use throwaway Twitter accounts, never your personal one.

### Adding an account

Go to the app's **Accounts** tab:

1. Log in at x.com in your browser.
2. Press F12 → **Application** (Chrome) or **Storage** (Firefox) → Cookies → `https://x.com`.
3. Fill in **Username**, paste the values of `auth_token` and `ct0`, optionally add a **Proxy** (e.g. `http://user:pass@host:8080`), and press **Save account**.

The app writes the account into `.env` (creating it from `.env.example` if needed, with permissions `600`) and tests it right away (✅/❌ in the log). The account list shows each account's username, state, request count and last error. It never shows cookies or passwords. **Test selected** and **Remove selected** work on the highlighted account, and **Refresh** reloads the list. Editing `.env` by hand still works too (see [Configuration](#configuration)).

### Rotation, rate limits and bans

- Tweets are fetched in chunks of `TW_ROTATE_EVERY` (default 40). Each chunk goes to the **least-recently-used** account, so the accounts take turns, with a random `TW_DELAY_MIN` to `TW_DELAY_MAX` second pause between chunks.
- twscrape also moves off an account that hits a rate limit.
- Every 10 chunks the log shows `Accounts: N active · requests a=… b=…`, so you can see the rotation working.
- When all accounts are rate-limited, the log shows a yellow `X rate limit: all accounts are busy (next free at HH:MM UTC)` line. The run waits and then continues by itself; it hasn't frozen.
- If X bans or logs out an account, the log says `Account @x was deactivated: <reason>` and the run continues with the remaining accounts. If **no** account is left, the run stops and saves what it collected as `_incomplete.csv`.
- To lower the risk of bans, use more accounts. A separate proxy per account helps most.

## Usage

`python -m scraper` opens a full-screen terminal app:

```
┌ Anonymized Post Scraper — Twitter/X + Ekşi Sözlük ─────┐
│ [Scrape]  Offensive batch   Accounts                   │
│ ╭ Twitter / X ─────────────────────────────────────╮   │
│ │ Method   [ Search (latest)                ▾ ]    │   │
│ │ Query    [ yapay zeka lang:tr                 ]  │   │
│ │ Count    [ 10 ]                                  │   │
│ ╰──────────────────────────────────────────────────╯   │
│ ╭ Ekşi Sözlük ─────────────────────────────────────╮   │
│ │ Method   [ Keyword search over titles     ▾ ]    │   │
│ │ Query    [ yapay zeka                         ]  │   │
│ │ Order    [ Most-liked first               ▾ ]    │   │
│ │ Count    [ 2 ]                                   │   │
│ ╰──────────────────────────────────────────────────╯   │
│ ╭──────────────────────────────────────────────────╮   │
│ │ Output   [ New file                       ▾ ]    │   │
│ ╰──────────────────────────────────────────────────╯   │
│          [ ] Shuffle rows  [Stop]  [Start scraping]    │
│ ╭ log ─────────────────────────────────────────────╮   │
│ │ Twitter: 10/10   Ekşi: 2/2                       │   │
│ │ Wrote 12 anonymized posts to data/2026...csv     │   │
│ ╰──────────────────────────────────────────────────╯   │
└ ^s Start  ^x Stop run  ^l Clear log  ^q Quit ──────────┘
```

### Your first run

1. On the **Scrape** tab, pick a method and enter a query for each source you want.
2. Enter a **Count** for each source. Counts start at 0, and a source with count 0 is skipped.
3. Press **Start scraping** (or ctrl+s). When the run ends, the log shows the path of the CSV in `data/`.

### Tabs

- **Scrape**: Twitter and/or Ekşi posts on any topic.
- **Offensive batch**: a Turkish offensive-language dataset for ML (see [below](#offensive-language-batch-for-ml)).
- **Accounts**: add, test and remove Twitter accounts (see [Twitter accounts](#twitter-accounts)).

### Twitter methods

| Method | Input |
| --- | --- |
| `Search (latest)` / `Search (top)` | any X search query: `lang:tr`, `since:2026-01-01`, `-filter:retweets`, `min_faves:10` ... |
| `A user's tweets` / `A user's tweets + replies` | `@handle` or profile URL |
| `Replies to a tweet` | tweet URL |
| `List timeline` | list URL |
| `Trending topic` | press **Load trends**, then pick one |

Follower/following lists exist in twscrape too, but they return accounts, not posts, so they are left out on purpose.

### Ekşi methods

| Method | Input |
| --- | --- |
| `One topic (başlık)` | title, slug or URL |
| `Keyword search over titles` | words to look for in topic titles |
| `Gündem (popular now)` | none |

- **Order**: `Most-liked first`, `Oldest first`, or `Today's entries`.
- Gündem continues into the "today" feed, and keyword search pages through all results until the count is reached.
- `EKSI_MAX_PER_TOPIC` (default 10) caps how many entries come from any one topic.

### Stopping a run

**Stop** (or ctrl+x) ends a run at any time and saves what was collected as `_incomplete.csv`. The app stays open. ctrl+q does the same but also quits.

## Offensive-language batch (for ML)

This tab builds a Turkish dataset that mixes offensive-language **candidates** with **control** posts. Use it as raw material for an offensive-language classifier.

**Fields**: Twitter posts, Ekşi entries, Offensive %, Lexicon (path to the lexicon file), Extra filter (an optional X search filter such as `since:2026-01-01`), Exclude near-duplicate texts, and Output.

- **Lexicon**: `lexicons/tr_offensive.txt` is a seed list of ~150 terms in five categories (profanity, insult, identity-based, curse, threat), including common spellings typed without Turkish letters. Words with a harmless everyday meaning appear only as phrases (a fixed two-word phrase is listed instead of the ambiguous single word). Extend it. The format is explained at the top of the file. A trailing `$` makes a term whole-word only (`top$` won't match `toplantı`). Without `$`, a term also matches with suffixes (`kelime` matches `kelimeler`).
- **Matching** ignores case (Turkish I/ı, İ/i), leetspeak (`k3l1me`), letters split by dots or spaces (`k.e.l.i.m.e`) and stretched letters (`kelimeeee`). Matching runs on the anonymized text, so a username that contains a listed word never counts as a hit.
- **Twitter**: lexicon terms are combined into `(t1 OR t2 ...) lang:tr -filter:retweets` queries under X's length limit. The program fetches from every query so one word doesn't dominate. Control tweets come from a generic Turkish query that excludes lexicon words. A control tweet that still contains one moves to the offensive group.
- **Ekşi**: there is no site-wide full-text search, so the program pulls topics from (1) a title search for each lexicon term, which only adds offensive candidates, and (2) gündem and then the "today" feed, which add both groups. It checks every entry against the lexicon locally and keeps going until both counts are reached. That is roughly 1 s per page of 10 entries. Truly offensive entries are rare outside insult-titled topics, so large Ekşi counts can take hours. A safety cap stops after `max(5000, 40 × requested)` scanned entries.
- **Offensive %** sets the mix (e.g. 50 means half offensive candidates, half control). Rows are shuffled. Near-duplicate texts are removed by default (important for ML: copies leak between train and test).
- **Extra CSV columns**: `sample_group` (`offensive_candidate` / `control`), `matched_terms`, `matched_categories`. In this mode the `query` column holds only a short label (`lexicon:tr_offensive` or `control`), not the long `OR` search string.
- **File name**: `data/YYYYMMDD-HHMMSS_offensive_tw<N>_eksi<M>_<lexicon>.csv`

> **These are weak labels.** A keyword hit is not the same as offensive content (a swear word between friends is often just banter), and many offensive posts contain no listed word (sarcasm, threats, new slang). Have the rows annotated by people, ideally two annotators with an agreement check, before training. The control group is "no lexicon hit", not "verified clean".
> The hand-annotated **OffensEval-2020 Turkish** dataset (Çöltekin, 2020; ~35k tweets) works well as a benchmark or extra training data next to your own batch.

## Anonymization

| Found in text                                          | Replaced with                  |
| ------------------------------------------------------ | ------------------------------ |
| `@mention` (Twitter or Ekşi)                           | `@username`                    |
| `(bkz: @nick)` on Ekşi                                 | `(bkz: @username)`             |
| Bare username / display name of any user seen in the run | `#username`                  |
| `x.com/<user>/...`, `eksisozluk.com/biri/<nick>`       | generic link                   |
| E-mail addresses, phone numbers                        | `[email]`, `[phone]`           |

No user ids, usernames, post ids/URLs, or hashes of any of them are written.

**Limits:** this is pseudonymization, not perfect anonymity. Exact tweet text plus a timestamp can often be found again with a web search. If you publish a dataset, consider rounding `created_at` or paraphrasing. Names shorter than 3 characters (display names shorter than 4) are not replaced as bare words, to avoid mangling ordinary text.

## Output

**File name:** `data/YYYYMMDD-HHMMSS_tw<N>_eksi<M>_<slug>.csv` (the offensive batch adds `_offensive`)

- The timestamp comes first, so files sort chronologically.
- `N` and `M` are the actual row counts from each source.
- The slug is an ASCII form of your search terms.

The file is UTF-8 with a BOM, so Turkish characters display correctly in Excel and LibreOffice.

**Columns:** `row, source, method, query, topic, text, created_at, lang, like_count, repost_count, reply_count, quote_count, view_count, favorite_count, hashtags, post_type, sample_group, matched_terms, matched_categories`

The `query` column is anonymized too: `from:someone` becomes `from:username`. For user, replies and list methods the column only says `@username`, `tweet-replies` or `list`.

### Partial and incomplete files

Rows are written while the run is going, to `data/YYYYMMDD-HHMMSS_<slug>.partial.csv`, so a long run (tens of thousands of posts) doesn't sit in memory. When the run ends, that file is finished and renamed to the name above.

If the run stops early (error, all accounts banned, network problem, Stop/ctrl+x, or quitting with ctrl+q), whatever was collected is still saved, with `_incomplete` added to the name. A `.partial.csv` is only left behind if the process is killed hard. Its rows are anonymized, but a bare name that first appeared later in the run may not have been redacted yet, so delete it or rerun.

### Adding to an existing file

Each tab has an **Output** choice: `New file` (default), or `Append to …` for one of the finished CSVs in `data/`. The Offensive tab lists only offensive batches and the Scrape tab lists only the others.

- The counts you enter are what this run *adds*.
- New rows stream to their own `.partial.csv`. The chosen file is only replaced when the run ends, so stopping or crashing mid-run leaves it unchanged.
- At the end, old and new rows are merged (offensive batches are reshuffled), renumbered, and saved under the original timestamp with updated `tw<N>_eksi<M>` counts. The old copy is removed.
- With "Exclude near-duplicate texts" on, posts whose text is already in the file are skipped.
- Limitation: earlier runs' usernames are never stored, since that would defeat the anonymization. A new post that mentions an author from an *earlier* run by bare name, without `@`, can't be caught. @mentions, profile links, emails and phone numbers are always redacted.

## Configuration

Settings live in `.env`, next to the app. `.env.example` documents every setting. The **Accounts** tab writes the account entries for you.

| Setting | Default | What it does |
| --- | --- | --- |
| `TW_ACCOUNT_<n>_USERNAME`, `_COOKIES`, `_PROXY`, … | | One block per Twitter account, numbered 1, 2, 3, … with no gaps |
| `TW_ROTATE_EVERY` | `40` | Tweets fetched per chunk before switching account |
| `TW_DELAY_MIN` / `TW_DELAY_MAX` | `3` / `8` | Random pause between chunks, in seconds |
| `EKSI_MAX_PER_TOPIC` | `10` | Max entries taken from any one Ekşi topic |
| `OFFENSIVE_LEXICON` | `lexicons/tr_offensive.txt` | Default lexicon for the offensive batch |
| `OUTPUT_DIR` | `data` | Where CSV files are written |
| `TWSCRAPE_DB` | `accounts.db` | twscrape's account and session database |

## Standalone executable

```bash
./build.sh
```

This needs the `.venv` from [Quick start](#quick-start). It installs PyInstaller into `.venv` and produces:

| File | What it is |
| --- | --- |
| `dist/scraper` | the app as one executable (run `./scraper`, no Python needed) |
| `dist/LICENSE` | this project's license |
| `dist/THIRD_PARTY_LICENSES.txt` | licenses of everything bundled inside: Python itself and every runtime package (twscrape, eksiapi, Textual, python-dotenv and their dependencies) |

**Always ship the whole `dist/` folder.** The bundled libraries' licenses (MIT, BSD, Apache-2.0) require their notices to travel with any copy of their code. `build.sh` regenerates the license file on every build (via `tools/third_party_licenses.py`), so it stays in sync when dependencies change. `.env`, `accounts.db` and `data/` are created next to the executable on first use.

## Troubleshooting

- **Ekşi stopped working**: Ekşi Sözlük does not publish public API docs. `eksiapi` talks to the same `api.eksisozluk.com` endpoints the Android app uses, with an anonymous token, so it may break when the app changes. If it does, run `pip install -U eksiapi`.
- **A Twitter run seems stuck**: check the log for the yellow rate-limit line. The run is waiting for an account to free up (see [Rotation, rate limits and bans](#rotation-rate-limits-and-bans)).

## Development

### Tests

```bash
python -m unittest discover -s tests
```

### Secrets

`.env`, `accounts.db` (twscrape stores session cookies there), and all `*.csv` files are gitignored. `.env.example` is committed. `AGENTS.md` tells AI agents never to read `.env`. `.claude/settings.json` also blocks Claude Code from reading it.

### AI assistance

This project was built with the help of AI coding assistants. The author designed the architecture, chose the technologies, and reviewed and tested the code.

## Citation

If you use this project in research or publications, please cite it using the metadata in [`CITATION.cff`](CITATION.cff). On GitHub, the **"Cite this repository"** button in the sidebar gives ready-made APA and BibTeX entries.

## License

Released under the [MIT License](LICENSE).
