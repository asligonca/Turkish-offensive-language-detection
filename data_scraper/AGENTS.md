# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Cursor, Copilot, etc.) working in this repository.

## Secrets: hard rules

- **Never open, read, print, cat, grep, diff, copy, or summarize `.env`**, or any other `.env.*` file except `.env.example`. This includes indirect access such as `source .env`, `env`/`printenv` after loading it, `python -c "...load_dotenv..."` with printing, or opening it in a tool.
- **Never read `accounts.db`** (or any `*.db`). twscrape stores live session cookies there.
- Never ask the user to paste credentials, cookies, or tokens into the chat.
- Never hardcode credentials in source files, tests, commit messages, or logs.
- If you need to know which settings exist, read **`.env.example`**. When adding a new setting, add it to `.env.example` with an empty or placeholder value and document it.
- Never commit `.env`, `*.db`, or `*.csv`. Do not weaken the related `.gitignore` rules.

## Data handling

- Scraped CSVs (`data/`, `*.csv`) contain public posts by real people. Do not open them unless the user asks, and never send their contents to external services.
- All output must go through `scraper/anonymize.py`. Do not add columns with user ids, usernames, display names, profile URLs, or raw post ids/URLs.

## Project layout

- `scraper/tui.py` + `scraper/app.tcss`: the Textual terminal app (`python -m scraper`). There is no CLI-argument mode on purpose.
- `scraper/twitter.py`: twscrape pool setup, account rotation, Twitter methods (search, user, replies, list, trend)
- `scraper/eksi.py`: Ekşi Sözlük via the `eksiapi` mobile-API client
- `scraper/offensive.py`: offensive-language batch (lexicon loading, Turkish normalization, query building, weak labels)
- `lexicons/tr_offensive.txt`: seed lexicon. The format is documented at the top of the file
- `scraper/envfile.py`: the only code allowed to write `.env` (used by the Accounts tab). Its tests must use temporary files, never the real `.env`
- `scraper/anonymize.py`: redaction rules
- `scraper/output.py`: CSV writer and file naming
- `tests/`: run with `python -m unittest discover -s tests`
- `build.sh` + `run_scraper.py` + `requirements-build.txt`: PyInstaller build. `tools/third_party_licenses.py` writes `dist/THIRD_PARTY_LICENSES.txt` from the runtime dependency tree. Never ship a build without it.
- `scraper/paths.py`: `APP_DIR` (writable: .env, data/, accounts.db) vs `RESOURCE_DIR` (bundled read-only files). Use these instead of `__file__` so frozen builds work.

Core modules report progress through a `progress(str)` callback; never `print()` from them (it would corrupt the TUI).
