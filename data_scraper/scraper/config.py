"""Settings loaded from .env (never commit .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .paths import APP_DIR, RESOURCE_DIR, project_path

PROJECT_ROOT = APP_DIR  # where .env lives (tests point this at a temp folder)


@dataclass
class TwitterAccount:
    username: str
    password: str = ""
    email: str = ""
    email_password: str = ""
    cookies: str = ""
    proxy: str = ""
    mfa_code: str = ""

    def __repr__(self) -> str:  # never print secrets by accident
        return f"TwitterAccount(username={self.username!r})"


@dataclass
class Settings:
    accounts: list[TwitterAccount]
    twscrape_db: str
    rotate_every: int
    delay_min: float
    delay_max: float
    eksi_max_per_topic: int
    output_dir: str


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_path() -> Path:
    return PROJECT_ROOT / ".env"


def env_example_path() -> Path:
    local = PROJECT_ROOT / ".env.example"
    return local if local.exists() else RESOURCE_DIR / ".env.example"


def load_settings() -> Settings:
    path = env_path()
    load_dotenv(path)
    # Accounts are read straight from the file (not os.environ) so that changes
    # made in the app's Accounts tab take effect on reload.
    source = {k: v or "" for k, v in dotenv_values(path).items()} if path.exists() else dict(os.environ)

    def acc(key: str) -> str:
        return source.get(key, "").strip()

    accounts: list[TwitterAccount] = []
    i = 1
    while f"TW_ACCOUNT_{i}_USERNAME" in source:
        prefix = f"TW_ACCOUNT_{i}_"
        username = acc(prefix + "USERNAME")
        if username and username != "your_twitter_username":
            accounts.append(
                TwitterAccount(
                    username=username,
                    password=acc(prefix + "PASSWORD"),
                    email=acc(prefix + "EMAIL"),
                    email_password=acc(prefix + "EMAIL_PASSWORD"),
                    cookies=acc(prefix + "COOKIES"),
                    proxy=acc(prefix + "PROXY"),
                    mfa_code=acc(prefix + "MFA_CODE"),
                )
            )
        i += 1

    delay_min = float(_env("TW_DELAY_MIN", "3"))
    return Settings(
        accounts=accounts,
        twscrape_db=str(project_path(_env("TWSCRAPE_DB", "accounts.db"))),
        rotate_every=max(1, int(_env("TW_ROTATE_EVERY", "40"))),
        delay_min=delay_min,
        delay_max=max(delay_min, float(_env("TW_DELAY_MAX", "8"))),
        eksi_max_per_topic=max(1, int(_env("EKSI_MAX_PER_TOPIC", "10"))),
        output_dir=str(project_path(_env("OUTPUT_DIR", "data"))),
    )
