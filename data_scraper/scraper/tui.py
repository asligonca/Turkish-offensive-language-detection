"""Full-screen terminal app (Textual). Start with: python -m scraper"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from collections.abc import Callable

from loguru import logger
from rich.markup import escape

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from . import envfile
from .config import Settings, env_example_path, env_path, load_settings
from .eksi import EKSI_METHODS, EKSI_ORDERS
from pathlib import Path

from .output import CsvStream, list_outputs
from .twitter import TW_METHODS, ScrapeError

ACCOUNTS_HELP = (
    "[b]Add a Twitter account[/b] (use a throwaway account, never your personal one):\n"
    "1. Log in at x.com in your browser.  2. Press F12 → [b]Application[/b] (Chrome) or [b]Storage[/b] (Firefox) "
    "→ Cookies → https://x.com.  3. Copy the [b]Value[/b] of [i]auth_token[/i] and [i]ct0[/i] into the fields below.\n"
    "[dim]Tarayıcıda x.com'a gir → F12 → Application/Storage → Cookies → https://x.com → "
    "auth_token ve ct0 değerlerini kopyala.[/dim]"
)

OFFENSIVE_HELP = (
    "Builds a Turkish dataset that mixes [b]offensive candidates[/b] (posts matching the lexicon) "
    "with [b]control[/b] posts. Labels are keyword hits only, so annotate them by hand before training. "
    "Each row gets [i]sample_group[/i], [i]matched_terms[/i] and [i]matched_categories[/i]."
)


def row(label: str, widget: Widget, *, row_id: str | None = None, label_id: str | None = None) -> Horizontal:
    return Horizontal(Label(label, classes="field-label", id=label_id), widget, classes="row", id=row_id)


NEW_FILE = "__new__"
_NEXT_FREE_RE = re.compile(r"Next available at (\S+ )?(\d{1,2}:\d{2})")


def library_log_text(level: str, message: str) -> str | None:
    """What (if anything) to show in the log panel for a twscrape log record.

    Warnings and errors always pass. Of the INFO records only the "all accounts
    rate-limited, waiting" one passes; otherwise a long wait looks like a freeze.
    """
    if level in ("WARNING", "ERROR", "CRITICAL"):
        return f"[dim]twscrape {level.lower()}: {escape(message)}[/dim]"
    if level == "INFO" and message.startswith("No account available"):
        m = _NEXT_FREE_RE.search(message)
        when = f"next free at {m.group(2)} UTC" if m else escape(message)
        return (f"[yellow]X rate limit: all accounts are busy ({when}). "
                f"The run continues by itself.[/yellow]")
    return None


def int_value(app: App, selector: str) -> int:
    raw = app.query_one(selector, Input).value.strip()
    return int(raw) if raw.lstrip("-").isdigit() else 0


class ScraperApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "Anonymized Post Scraper"
    SUB_TITLE = "Twitter/X + Ekşi Sözlük"
    BINDINGS = [
        ("ctrl+s", "start", "Start"),
        ("ctrl+x", "stop", "Stop run"),
        ("ctrl+l", "clear_log", "Clear log"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.settings: Settings = load_settings()
        self._api = None
        self._api_lock = asyncio.Lock()
        self.busy = False

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Scrape", id="tab-scrape"):
                with VerticalScroll():
                    with Vertical(classes="group"):
                        yield Label("Twitter / X", classes="group-title")
                        yield row("Method", Select(
                            [(label, value) for value, (label, _, _) in TW_METHODS.items()],
                            value="search_latest", allow_blank=False, id="tw-method"))
                        yield row("Query", Input(placeholder=TW_METHODS["search_latest"][2], id="tw-query"),
                                  row_id="tw-query-row", label_id="tw-query-label")
                        yield Horizontal(
                            Label("Trend", classes="field-label"),
                            Select([], prompt="Press 'Load trends'", id="tw-trend"),
                            Button("Load trends", id="load-trends"),
                            classes="row hidden", id="tw-trend-row",
                        )
                        yield row("Count", Input("0", type="integer", id="tw-count"))
                    with Vertical(classes="group"):
                        yield Label("Ekşi Sözlük", classes="group-title")
                        yield row("Method", Select(
                            [(label, value) for value, label in EKSI_METHODS.items()],
                            value="search", allow_blank=False, id="eksi-method"))
                        yield row("Query", Input(placeholder="yapay zeka", id="eksi-query"),
                                  row_id="eksi-query-row", label_id="eksi-query-label")
                        yield row("Order", Select(
                            [(label, value) for value, label in EKSI_ORDERS.items()],
                            value="popular", allow_blank=False, id="eksi-order"))
                        yield row("Count", Input("0", type="integer", id="eksi-count"))
                    with Vertical(classes="group"):
                        yield row("Output", Select([("New file", NEW_FILE)], value=NEW_FILE,
                                                   allow_blank=False, id="scrape-output"))
                    with Horizontal(classes="actions"):
                        yield Checkbox("Shuffle rows", id="shuffle")
                        yield Button("Stop", variant="error", id="stop-scrape", disabled=True)
                        yield Button("Start scraping", variant="primary", id="start-scrape")

            with TabPane("Offensive batch", id="tab-offensive"):
                with VerticalScroll():
                    yield Static(OFFENSIVE_HELP, classes="help")
                    with Vertical(classes="group"):
                        yield row("Twitter posts", Input("100", type="integer", id="off-tw-count"))
                        yield row("Ekşi entries", Input("0", type="integer", id="off-eksi-count"))
                        yield row("Offensive %", Input("50", type="integer", id="off-ratio"))
                        yield row("Lexicon", Input(
                            os.getenv("OFFENSIVE_LEXICON", "lexicons/tr_offensive.txt"), id="off-lexicon"))
                        yield row("Extra filter", Input(placeholder="since:2026-01-01 (optional)", id="off-extra"))
                        yield Checkbox("Exclude near-duplicate texts", value=True, id="off-dedupe")
                        yield row("Output", Select([("New file", NEW_FILE)], value=NEW_FILE,
                                                   allow_blank=False, id="off-output"))
                    with Horizontal(classes="actions"):
                        yield Button("Stop", variant="error", id="stop-offensive", disabled=True)
                        yield Button("Build offensive batch", variant="warning", id="start-offensive")

            with TabPane("Accounts", id="tab-accounts"):
                with VerticalScroll():
                    yield Static(ACCOUNTS_HELP, classes="help")
                    with Vertical(classes="group"):
                        yield row("Username", Input(placeholder="your_throwaway_account", id="acc-username"))
                        yield row("auth_token", Input(placeholder="paste auth_token value", password=True,
                                                      id="acc-auth-token"))
                        yield row("ct0", Input(placeholder="paste ct0 value", password=True, id="acc-ct0"))
                        yield row("Proxy", Input(placeholder="optional, e.g. http://user:pass@host:8080",
                                                 id="acc-proxy"))
                        with Horizontal(classes="actions"):
                            yield Button("Save account", variant="primary", id="save-account")
                    yield DataTable(id="accounts-table", cursor_type="row", zebra_stripes=True)
                    with Horizontal(classes="actions"):
                        yield Button("Test selected", id="test-account")
                        yield Button("Remove selected", variant="error", id="remove-account")
                        yield Button("Refresh", id="refresh-accounts")

        yield RichLog(id="log", wrap=True, markup=True, max_lines=1000)
        yield Footer()

    def route_library_logs(self) -> None:
        """twscrape logs to stderr via loguru, which would garble the full-screen UI.
        Send warnings/errors to the log panel instead."""
        app_thread = threading.get_ident()

        def sink(message) -> None:
            record = message.record
            text = library_log_text(record["level"].name, record["message"])
            if text is None:
                return
            if threading.get_ident() == app_thread:
                self.say(text)
            else:
                self.call_from_thread(self.say, text)

        logger.remove()
        logger.add(sink, level="INFO", format="{message}")

    def on_mount(self) -> None:
        self.route_library_logs()
        self.query_one("#accounts-table", DataTable).add_columns("Username", "State", "Requests", "Last error")
        self.refresh_outputs()
        self.say("[b]Ready.[/b] Fill in the form and press [b]Start[/b] (ctrl+s). "
                 "Stop a run with ctrl+x (keeps what was collected). Quit with ctrl+q.")
        if not self.settings.accounts:
            self.say("[yellow]No Twitter accounts yet: only Ekşi works. Add one in the Accounts tab.[/yellow]")

    # ----------------------------------------------------------------- helpers
    def say(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def thread_progress(self) -> Callable[[str], None]:
        return lambda m: self.call_from_thread(self.say, m)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for bid in ("#start-scrape", "#start-offensive", "#scrape-output", "#off-output"):
            self.query_one(bid).disabled = busy
        for bid in ("#stop-scrape", "#stop-offensive"):
            self.query_one(bid, Button).disabled = not busy

    def refresh_outputs(self, select_path: Path | None = None) -> None:
        """Fill the Output selects with files a run can append to."""
        for sel_id, offensive in (("#scrape-output", False), ("#off-output", True)):
            select = self.query_one(sel_id, Select)
            files = list_outputs(self.settings.output_dir, offensive)
            current = select_path if select_path in files else (
                Path(str(select.value)) if select.value != NEW_FILE else None)
            select.set_options([("New file", NEW_FILE)] + [(f"Append to {f.name}", str(f)) for f in files])
            select.value = str(current) if current in files else NEW_FILE

    def append_target(self, sel_id: str) -> Path | None:
        value = self.query_one(sel_id, Select).value
        return Path(str(value)) if value not in (NEW_FILE, Select.BLANK, None) else None

    async def get_api(self):
        async with self._api_lock:
            if self._api is None:
                from .twitter import build_api
                self._api = await build_api(self.settings, self.say)
            return self._api

    def open_stream(
        self, *terms: str, prefix: str = "", shuffle: bool = False, append_to: Path | None = None,
    ) -> CsvStream | None:
        if append_to and not append_to.is_file():
            self.say(f"[red]{escape(append_to.name)} no longer exists. Pick another Output.[/red]")
            self.refresh_outputs()
            return None
        try:
            stream = CsvStream(self.settings.output_dir, *terms, prefix=prefix, shuffle=shuffle,
                               append_to=append_to)
        except OSError as exc:
            self.say(f"[red]Cannot write to {escape(str(self.settings.output_dir))}: {exc}[/red]")
            return None
        if append_to:
            self.say(f"Appending to [u]{escape(append_to.name)}[/u] "
                     f"({sum(stream.base_counts.values())} rows already in it).")
        self.say(f"[dim]Writing rows as they arrive to {escape(str(stream.partial_path))}[/dim]")
        return stream

    def report(self, stream: CsvStream, incomplete: bool) -> None:
        """Finalize the CSV (sync) and log where it went."""
        path = stream.finish(incomplete=incomplete)
        try:
            if path is None:
                self.say("[yellow]No posts fetched; no file written.[/yellow]")
                return
            if stream.groups:
                n_off = stream.groups.get("offensive_candidate", 0)
                self.say(f"Offensive candidates: {n_off}, control: {stream.total - n_off}")
            n_tw, n_eksi = stream.counts.get("twitter", 0), stream.counts.get("eksisozluk", 0)
            what = "[yellow b]Run stopped early; saved[/yellow b]" if incomplete else "[green b]Wrote[/green b]"
            self.say(f"{what} {stream.total} anonymized posts ({n_tw} tweets, {n_eksi} entries) "
                     f"to [u]{escape(str(path))}[/u]")
            if stream.append_to:
                fc, fg = stream.file_counts(), stream.file_groups()
                line = (f"File now has {sum(fc.values())} rows ({fc.get('twitter', 0)} tweets, "
                        f"{fc.get('eksisozluk', 0)} entries)")
                if fg:
                    line += (f"; offensive candidates {fg.get('offensive_candidate', 0)}, "
                             f"control {fg.get('control', 0)}")
                self.say(line + ".")
            self.refresh_outputs(select_path=path if stream.append_to else None)
        except Exception:  # app may be shutting down (ctrl+q during a run)
            pass

    # ------------------------------------------------------------ form events
    @on(Select.Changed, "#tw-method")
    def tw_method_changed(self, event: Select.Changed) -> None:
        method = str(event.value)
        _, label, placeholder = TW_METHODS[method]
        is_trend = method == "trend"
        self.query_one("#tw-query-row").set_class(is_trend, "hidden")
        self.query_one("#tw-trend-row").set_class(not is_trend, "hidden")
        self.query_one("#tw-query-label", Label).update(label)
        self.query_one("#tw-query", Input).placeholder = placeholder

    @on(Select.Changed, "#eksi-method")
    def eksi_method_changed(self, event: Select.Changed) -> None:
        method = str(event.value)
        self.query_one("#eksi-query-row").set_class(method == "gundem", "hidden")
        self.query_one("#eksi-query-label", Label).update("Topic" if method == "topic" else "Keyword")
        self.query_one("#eksi-query", Input).placeholder = (
            "title, slug or URL" if method == "topic" else "yapay zeka"
        )
        if method == "gundem":
            self.query_one("#eksi-order", Select).value = "today"

    @on(TabbedContent.TabActivated, pane="#tab-accounts")
    def accounts_tab_opened(self) -> None:
        if self.settings.accounts and self.query_one("#accounts-table", DataTable).row_count == 0:
            self.refresh_accounts()

    @on(Button.Pressed, "#start-scrape")
    def start_scrape_pressed(self) -> None:
        self.run_scrape()

    @on(Button.Pressed, "#start-offensive")
    def start_offensive_pressed(self) -> None:
        self.run_offensive()

    @on(Button.Pressed, "#stop-scrape")
    @on(Button.Pressed, "#stop-offensive")
    def stop_pressed(self) -> None:
        self.action_stop()

    @on(Button.Pressed, "#load-trends")
    def load_trends_pressed(self) -> None:
        self.run_load_trends()

    @on(Button.Pressed, "#refresh-accounts")
    def refresh_pressed(self) -> None:
        self.refresh_accounts()

    @on(Button.Pressed, "#save-account")
    def save_account_pressed(self) -> None:
        self.save_account()

    @on(Button.Pressed, "#test-account")
    def test_account_pressed(self) -> None:
        if name := self.selected_account():
            self.test_account(name)

    @on(Button.Pressed, "#remove-account")
    def remove_account_pressed(self) -> None:
        if name := self.selected_account():
            self.remove_account(name)

    def selected_account(self) -> str | None:
        table = self.query_one("#accounts-table", DataTable)
        if table.row_count == 0:
            self.say("[yellow]No account in the list. Save one first.[/yellow]")
            return None
        return str(table.get_row_at(table.cursor_row)[0])

    def reload_accounts(self) -> None:
        """Pick up .env changes: new settings + a fresh twscrape pool sync on next use."""
        self.settings = load_settings()
        self._api = None

    def action_start(self) -> None:
        if self.busy:
            self.say("[yellow]A job is already running.[/yellow]")
            return
        active = self.query_one("#tabs", TabbedContent).active
        if active == "tab-offensive":
            self.run_offensive()
        elif active == "tab-accounts":
            self.refresh_accounts()
        else:
            self.run_scrape()

    def action_stop(self) -> None:
        if not self.busy:
            self.say("[dim]Nothing is running.[/dim]")
            return
        self.say("[yellow]Stopping… saving what was collected.[/yellow]")
        self.workers.cancel_group(self, "job")

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    # ----------------------------------------------------------------- workers
    @work(group="job")
    async def run_scrape(self) -> None:
        tw_method = str(self.query_one("#tw-method", Select).value)
        tw_count = int_value(self, "#tw-count")
        if tw_method == "trend":
            trend = self.query_one("#tw-trend", Select).value
            tw_target = trend if isinstance(trend, str) else ""
        else:
            tw_target = self.query_one("#tw-query", Input).value.strip()
        eksi_method = str(self.query_one("#eksi-method", Select).value)
        eksi_query = self.query_one("#eksi-query", Input).value.strip()
        eksi_order = str(self.query_one("#eksi-order", Select).value)
        eksi_count = int_value(self, "#eksi-count")

        if tw_count <= 0 and eksi_count <= 0:
            self.say("[red]Set a count above 0 for Twitter and/or Ekşi.[/red]")
            return
        if tw_count > 0 and not tw_target:
            self.say("[red]Twitter needs a query/target (or pick a trend).[/red]")
            return
        if eksi_count > 0 and eksi_method != "gundem" and not eksi_query:
            self.say("[red]Ekşi needs a topic or keyword.[/red]")
            return

        tw_term = tw_target if tw_method.startswith("search") or tw_method == "trend" else tw_method
        stream = self.open_stream(
            tw_term if tw_count > 0 else "", (eksi_query or eksi_method) if eksi_count > 0 else "",
            shuffle=self.query_one("#shuffle", Checkbox).value,
            append_to=self.append_target("#scrape-output"),
        )
        if stream is None:
            return

        self.set_busy(True)
        incomplete = True
        try:
            if tw_count > 0:
                from .twitter import fetch_tweets
                self.say(f"[b]Twitter[/b]: {TW_METHODS[tw_method][0]}, {tw_count} posts")
                api = await self.get_api()
                await fetch_tweets(api, tw_method, tw_target, tw_count, self.settings, self.say, stream.add)
            if eksi_count > 0:
                from .eksi import fetch_eksi
                self.say(f"[b]Ekşi[/b]: {EKSI_METHODS[eksi_method]}, {eksi_count} entries")
                await asyncio.to_thread(
                    fetch_eksi, eksi_method, eksi_query, eksi_count, eksi_order,
                    self.settings, self.thread_progress(), stream.add,
                )
            incomplete = False
        except ScrapeError as exc:
            self.say(f"[red]{exc}[/red]")
        except Exception as exc:  # keep the app alive, show what happened
            self.say(f"[red]Error: {exc.__class__.__name__}: {exc}[/red]")
        finally:
            # also runs on Stop (ctrl+x) and quit (ctrl+q): whatever was collected is kept
            try:
                self.report(stream, incomplete)
            finally:
                self.set_busy(False)

    @work(group="job")
    async def run_offensive(self) -> None:
        from .offensive import collect_eksi, collect_twitter, load_lexicon, split_counts

        n_tw = int_value(self, "#off-tw-count")
        n_eksi = int_value(self, "#off-eksi-count")
        ratio = int_value(self, "#off-ratio")
        extra = self.query_one("#off-extra", Input).value.strip()
        dedupe_on = self.query_one("#off-dedupe", Checkbox).value
        if n_tw <= 0 and n_eksi <= 0:
            self.say("[red]Set a count above 0 for Twitter and/or Ekşi.[/red]")
            return
        if not 0 <= ratio <= 100:
            self.say("[red]Offensive % must be between 0 and 100.[/red]")
            return
        try:
            lexicon = load_lexicon(self.query_one("#off-lexicon", Input).value.strip())
        except (OSError, ValueError) as exc:
            self.say(f"[red]{exc}[/red]")
            return

        # shuffle: avoid ordering bias when annotating
        stream = self.open_stream(lexicon.name, prefix="offensive", shuffle=True,
                                  append_to=self.append_target("#off-output"))
        if stream is None:
            return

        existing = stream.existing_texts() if dedupe_on else []  # skip posts already in the file
        self.set_busy(True)
        incomplete = True
        try:
            self.say(f"[b]Offensive batch[/b]: lexicon '{lexicon.name}' ({len(lexicon.terms)} terms), {ratio}% offensive")
            if n_tw > 0:
                off, ctrl = split_counts(n_tw, ratio)
                api = await self.get_api()
                await collect_twitter(api, lexicon, off, ctrl, extra, self.settings, self.say, stream.add,
                                      dedupe_on, existing)
            if n_eksi > 0:
                off, ctrl = split_counts(n_eksi, ratio)
                await asyncio.to_thread(
                    collect_eksi, lexicon, off, ctrl, self.settings, self.thread_progress(), stream.add,
                    dedupe_on, existing,
                )
            incomplete = False
        except ScrapeError as exc:
            self.say(f"[red]{exc}[/red]")
        except Exception as exc:
            self.say(f"[red]Error: {exc.__class__.__name__}: {exc}[/red]")
        finally:
            try:
                self.report(stream, incomplete)
            finally:
                self.set_busy(False)

    @work(exclusive=True, group="trends")
    async def run_load_trends(self) -> None:
        from .twitter import load_trends

        button = self.query_one("#load-trends", Button)
        button.disabled = True
        try:
            names = await load_trends(await self.get_api())
            select = self.query_one("#tw-trend", Select)
            select.set_options([(n, n) for n in names])
            self.say(f"Loaded {len(names)} trends." if names else "[yellow]No trends returned.[/yellow]")
        except Exception as exc:
            self.say(f"[red]Could not load trends: {exc}[/red]")
        finally:
            button.disabled = False

    def set_accounts_busy(self, busy: bool) -> None:
        for bid in ("#save-account", "#test-account", "#remove-account", "#refresh-accounts"):
            self.query_one(bid, Button).disabled = busy

    async def _fill_table(self) -> None:
        from .twitter import account_rows

        table = self.query_one("#accounts-table", DataTable)
        table.clear()
        try:
            rows = await account_rows(await self.get_api())
        except ScrapeError as exc:
            self.say(f"[yellow]{exc}[/yellow]")
            return
        for r in rows:
            table.add_row(*map(str, r))

    async def _check(self, username: str) -> None:
        from .twitter import check_account

        self.say(f"Testing @{username} ...")
        ok, message = await check_account(self.settings, username)
        if ok:
            self.say(f"[green]✅ @{username} works.[/green]")
        else:
            self.say(f"[red]❌ @{username}: {message}[/red]")

    @work(exclusive=True, group="accounts")
    async def refresh_accounts(self) -> None:
        self.set_accounts_busy(True)
        try:
            await self._fill_table()
        except Exception as exc:
            self.say(f"[red]{exc}[/red]")
        finally:
            self.set_accounts_busy(False)

    @work(exclusive=True, group="accounts")
    async def save_account(self) -> None:
        username_in = self.query_one("#acc-username", Input)
        token_in = self.query_one("#acc-auth-token", Input)
        ct0_in = self.query_one("#acc-ct0", Input)
        proxy_in = self.query_one("#acc-proxy", Input)
        try:
            username = envfile.validate(username_in.value, token_in.value, ct0_in.value, proxy_in.value)
        except ValueError as exc:
            self.say(f"[red]{exc}[/red]")
            return

        self.set_accounts_busy(True)
        try:
            envfile.ensure_env(env_path(), env_example_path())
            action = envfile.upsert_account(
                env_path(), username, token_in.value, ct0_in.value, proxy_in.value
            )
            for field in (username_in, token_in, ct0_in, proxy_in):
                field.value = ""
            self.say(f"[green]Account @{username} {action} in .env.[/green]")
            self.reload_accounts()
            await self._check(username)
            await self._fill_table()
        except Exception as exc:
            self.say(f"[red]Error: {exc.__class__.__name__}: {exc}[/red]")
        finally:
            self.set_accounts_busy(False)

    @work(exclusive=True, group="accounts")
    async def test_account(self, username: str) -> None:
        self.set_accounts_busy(True)
        try:
            await self._check(username)
        finally:
            self.set_accounts_busy(False)

    @work(exclusive=True, group="accounts")
    async def remove_account(self, username: str) -> None:
        self.set_accounts_busy(True)
        try:
            if envfile.remove_account(env_path(), username):
                self.say(f"Removed @{username} from .env.")
            self.reload_accounts()
            await self._fill_table()
        except Exception as exc:
            self.say(f"[red]Error: {exc.__class__.__name__}: {exc}[/red]")
        finally:
            self.set_accounts_busy(False)


def main() -> None:
    ScraperApp().run()
