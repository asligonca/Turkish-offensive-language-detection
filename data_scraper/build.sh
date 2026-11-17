#!/usr/bin/env bash
# Build a standalone executable in dist/ together with the license files that
# must ship with it. Usage: ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
    echo "No .venv found. Create it first:  python3 -m venv .venv" >&2
    exit 1
fi

echo "==> Installing runtime + build requirements into .venv"
.venv/bin/pip install -q -r requirements.txt -r requirements-build.txt

echo "==> Building dist/scraper"
.venv/bin/pyinstaller --noconfirm --clean --onefile --name scraper \
    --add-data "scraper/app.tcss:scraper" \
    --add-data "lexicons:lexicons" \
    --add-data ".env.example:." \
    --collect-data textual \
    --collect-all curl_cffi \
    --collect-data fake_useragent \
    --collect-submodules eksiapi \
    --collect-submodules textual \
    run_scraper.py

echo "==> Writing license files"
.venv/bin/python tools/third_party_licenses.py requirements.txt dist/THIRD_PARTY_LICENSES.txt
cp LICENSE dist/LICENSE

cat <<EOF

Done. Ship the whole dist/ folder together:
  dist/scraper                    the app (run: ./scraper)
  dist/LICENSE                    this project's license
  dist/THIRD_PARTY_LICENSES.txt   licenses of everything bundled inside
.env, accounts.db and data/ are created next to the executable on first use.
EOF
