"""Write THIRD_PARTY_LICENSES.txt for everything bundled into the build.

Usage: python tools/third_party_licenses.py requirements.txt dist/THIRD_PARTY_LICENSES.txt

Walks the runtime dependency tree starting from requirements.txt (so build-only
tools such as PyInstaller are not listed) and copies every license / notice
file each package ships. Also includes the Python interpreter's license,
because PyInstaller bundles Python itself. Standard library only; `packaging`
(installed with PyInstaller) is used for environment markers when available.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover - fallback without packaging
    Requirement = None  # type: ignore[assignment]

LICENSE_FILE_RE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.IGNORECASE)
RULE = "=" * 78


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def top_level_names(requirements: Path) -> list[str]:
    names = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            if m := re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line):
                names.append(m.group())
    return names


def runtime_deps(dist: metadata.Distribution) -> list[str]:
    out = []
    for spec in dist.requires or []:
        if Requirement is not None:
            req = Requirement(spec)
            # markers evaluated without extras -> optional extras are skipped
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                out.append(req.name)
        elif "extra ==" not in spec:
            name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec).group()
            if ";" in spec:  # platform marker we can't evaluate: keep only if installed
                try:
                    metadata.distribution(name)
                except metadata.PackageNotFoundError:
                    continue
            out.append(name)
    return out


def closure(roots: list[str]) -> list[metadata.Distribution]:
    seen: dict[str, metadata.Distribution] = {}
    stack = list(roots)
    while stack:
        name = stack.pop()
        key = norm(name)
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            print(f"warning: {name} is not installed; skipped", file=sys.stderr)
            continue
        seen[key] = dist
        stack.extend(runtime_deps(dist))
    return sorted(seen.values(), key=lambda d: norm(d.metadata["Name"]))


def license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    texts = []
    for f in dist.files or []:
        parts = Path(str(f)).parts
        if LICENSE_FILE_RE.match(parts[-1]) or "licenses" in parts[:-1]:
            path = Path(dist.locate_file(f))
            if path.is_file():
                try:
                    texts.append((str(f), path.read_text(encoding="utf-8", errors="replace").strip()))
                except OSError:
                    pass
    return texts


def homepage(dist: metadata.Distribution) -> str:
    md = dist.metadata
    if md.get("Home-page"):
        return md["Home-page"]
    for url in md.get_all("Project-URL") or []:
        label, _, link = url.partition(",")
        if label.strip().lower() in ("homepage", "source", "repository", "source code"):
            return link.strip()
    return ""


def python_license() -> tuple[str, str]:
    v = f"{sys.version_info.major}.{sys.version_info.minor}"
    for candidate in (
        Path(sys.base_prefix) / "lib" / f"python{v}" / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(f"/usr/lib/python{v}/LICENSE.txt"),
        Path(f"/usr/share/doc/python{v}/copyright"),
    ):
        if candidate.is_file():
            return str(candidate), candidate.read_text(encoding="utf-8", errors="replace").strip()
    return "", "See https://docs.python.org/3/license.html"


def main(requirements: str, output: str) -> int:
    dists = closure(top_level_names(Path(requirements)))
    lines = [
        "THIRD-PARTY SOFTWARE NOTICES AND LICENSES",
        f"Generated {date.today().isoformat()} by tools/third_party_licenses.py",
        "",
        "This executable bundles the software listed below. Each component is",
        "licensed under its own terms, reproduced here in full.",
        "",
        "Contents:",
        f"  - Python {sys.version.split()[0]} (interpreter)",
        *[f"  - {d.metadata['Name']} {d.version} ({d.metadata.get('License-Expression') or d.metadata.get('License') or 'see below'})"
          for d in dists],
        "",
        "The PyInstaller bootloader is GPL-2.0 with an exception that allows",
        "distributing bundled applications under any license.",
        "",
    ]

    src, text = python_license()
    lines += [RULE, f"Python {sys.version.split()[0]}", "https://www.python.org", RULE, text, ""]

    missing = []
    for d in dists:
        md = d.metadata
        license_name = md.get("License-Expression") or md.get("License") or ""
        if len(license_name) > 100:  # some packages paste the full text into this field
            license_name = license_name.splitlines()[0][:100] + "..."
        lines += [RULE, f"{md['Name']} {d.version}"]
        if license_name:
            lines.append(f"License: {license_name}")
        if url := homepage(d):
            lines.append(url)
        lines.append(RULE)
        texts = license_texts(d)
        if not texts:
            missing.append(md["Name"])
            lines.append(f"(No license file shipped in the package. License: {license_name or 'unknown'}; see {url or 'PyPI'})")
        for name, body in texts:
            lines += [f"--- {Path(name).name} ---", body, ""]
        lines.append("")

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(dists)} packages + Python).")
    if missing:
        print(f"warning: no license file shipped by: {', '.join(missing)} (license field + link written instead)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
