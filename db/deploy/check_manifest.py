#!/usr/bin/env python3
"""Lint db/deploy/manifest.txt and reject destructive SQL.

The deploy workflow runs only the scripts listed in the manifest. This checker
is the gate for that list: every path has to be a re-runnable file under
db/deploy/, and none of those files may drop, truncate, or delete rows.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path


class LintError(Exception):
    pass


# File the task explicitly keeps off the automated path, even if someone
# copies it under db/deploy/ and lists it.
DENIED_BASENAMES = {"001_backend_alignment.sql"}

FORBIDDEN = (
    (re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE), "DROP TABLE"),
    (re.compile(r"\bTRUNCATE\b", re.IGNORECASE), "TRUNCATE"),
    (re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE), "DELETE FROM"),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def emit_error(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)


def strip_sql_comments(text: str) -> str:
    """Remove -- and /* */ comments. String literals and q-quotes stay, so a
    destructive statement hidden in dynamic SQL is still visible."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("--", i):
            newline = text.find("\n", i)
            if newline < 0:
                break
            i = newline
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                # Unclosed comment: drop the rest so a comment cannot hide the tail.
                break
            i = end + 2
            continue
        if text[i] == "'":
            out.append(text[i])
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        out.append(text[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def forbidden_in_sql(text: str) -> str | None:
    visible = strip_sql_comments(text)
    collapsed = re.sub(r"\s+", " ", visible)
    for pattern, label in FORBIDDEN:
        if pattern.search(collapsed):
            return label
    return None


def read_manifest_lines(manifest: Path) -> list[str]:
    try:
        raw = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise LintError(f"Cannot read manifest {manifest}: {exc}") from exc
    entries: list[str] = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        if "\\" in body or body.startswith("/"):
            raise LintError(
                f"{manifest}:{line_no}: path must be repo-relative with forward slashes, got {body!r}"
            )
        parts = Path(body).parts
        if ".." in parts:
            raise LintError(f"{manifest}:{line_no}: path must not contain '..': {body}")
        entries.append(body)
    if not entries:
        raise LintError(f"{manifest} does not list any scripts")
    return entries


def lint_manifest(root: Path, manifest: Path) -> list[str]:
    """Return the ordered script paths. Raise LintError when the list is unsafe."""
    root = root.resolve()
    deploy_dir = (root / "db" / "deploy").resolve()
    entries = read_manifest_lines(manifest)
    seen: set[str] = set()
    ordered: list[str] = []

    for rel in entries:
        name = Path(rel).name
        if name in DENIED_BASENAMES or rel.endswith("db/migrations/001_backend_alignment.sql"):
            raise LintError(
                f"{rel} is excluded from automated deploy. "
                "db/migrations/001_backend_alignment.sql must not be listed in the manifest."
            )
        if not rel.endswith(".sql"):
            raise LintError(f"{rel} is not a .sql file")
        if not rel.startswith("db/deploy/"):
            raise LintError(
                f"{rel} is outside db/deploy/. Only scripts in that directory may be listed."
            )
        path = (root / rel).resolve()
        if not path.is_relative_to(deploy_dir):
            raise LintError(f"{rel} resolves outside db/deploy/")
        if not path.is_file():
            raise LintError(f"{rel} is listed in the manifest but the file does not exist")
        if rel in seen:
            raise LintError(f"{rel} is listed more than once")
        seen.add(rel)
        hit = forbidden_in_sql(path.read_text(encoding="utf-8"))
        if hit:
            raise LintError(
                f"{rel} contains {hit}. Manifest scripts must be re-runnable and must not "
                "drop tables, truncate, or delete rows. Remove the statement or keep the "
                "script out of db/deploy/manifest.txt."
            )
        ordered.append(rel)

    extras = sorted(
        p.relative_to(root).as_posix()
        for p in deploy_dir.glob("*.sql")
        if p.is_file() and p.relative_to(root).as_posix() not in seen
    )
    if extras:
        joined = ", ".join(extras)
        raise LintError(
            f"SQL files under db/deploy/ are missing from the manifest: {joined}. "
            "Append them in run order, or remove them. Unlisted scripts are not applied."
        )
    return ordered


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _expect_fail(root: Path, manifest: Path, needle: str) -> None:
    try:
        lint_manifest(root, manifest)
    except LintError as exc:
        if needle not in str(exc):
            raise AssertionError(f"expected {needle!r} in {exc}") from exc
        return
    raise AssertionError(f"expected failure containing {needle!r}")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deploy = root / "db" / "deploy"
        good = "db/deploy/010_ok.sql"
        _write(
            root / good,
            "-- DROP TABLE should be ignored in a comment\n"
            "/* TRUNCATE TABLE also ignored */\n"
            "CREATE TABLE IF NOT EXISTS climate_indices_monthly (year NUMBER);\n"
            "CREATE OR REPLACE VIEW v_ok AS SELECT 1 AS n FROM dual;\n",
        )
        _write(root / "db/deploy/manifest.txt", f"# header\n\n{good}\n")
        found = lint_manifest(root, root / "db/deploy/manifest.txt")
        if found != [good]:
            raise AssertionError(found)

        _write(root / "db/deploy/020_drop.sql", "BEGIN EXECUTE IMMEDIATE 'DROP TABLE wards'; END;\n/\n")
        _write(
            root / "db/deploy/manifest.txt",
            f"{good}\ndb/deploy/020_drop.sql\n",
        )
        _expect_fail(root, root / "db/deploy/manifest.txt", "DROP TABLE")

        _write(root / "db/deploy/020_drop.sql", "delete\nfrom wards;\n")
        _expect_fail(root, root / "db/deploy/manifest.txt", "DELETE FROM")

        _write(root / "db/deploy/020_drop.sql", "TRUNCATE TABLE climate_indices_monthly;\n")
        _expect_fail(root, root / "db/deploy/manifest.txt", "TRUNCATE")

        _write(root / "db/deploy/020_drop.sql", "CREATE OR REPLACE VIEW v_ok AS SELECT 1 AS n FROM dual;\n")
        _write(
            root / "db/deploy/manifest.txt",
            f"{good}\ndb/deploy/001_backend_alignment.sql\n",
        )
        _write(root / "db/deploy/001_backend_alignment.sql", "CREATE OR REPLACE VIEW v_ok AS SELECT 1 FROM dual;\n")
        # 020_drop.sql is now an unlisted extra as well, but the denylist is the point.
        _write(root / "db/deploy/020_drop.sql", "")
        (root / "db/deploy/020_drop.sql").unlink()
        _expect_fail(root, root / "db/deploy/manifest.txt", "001_backend_alignment.sql")

        _write(root / "db/deploy/manifest.txt", f"{good}\ndb/migrations/001_backend_alignment.sql\n")
        _expect_fail(root, root / "db/deploy/manifest.txt", "001_backend_alignment.sql")

        _write(root / "db/deploy/manifest.txt", f"{good}\ndb/schema.sql\n")
        _expect_fail(root, root / "db/deploy/manifest.txt", "outside db/deploy/")

        _write(root / "db/deploy/manifest.txt", f"{good}\ndb/deploy/../deploy/010_ok.sql\n")
        _expect_fail(root, root / "db/deploy/manifest.txt", "'..'")

        _write(root / "db/deploy/manifest.txt", f"{good}\n{good}\n")
        _expect_fail(root, root / "db/deploy/manifest.txt", "more than once")

        _write(root / "db/deploy/orphan.sql", "CREATE OR REPLACE VIEW v_orphan AS SELECT 1 FROM dual;\n")
        _write(root / "db/deploy/manifest.txt", f"{good}\n")
        _expect_fail(root, root / "db/deploy/manifest.txt", "missing from the manifest")

    # The committed manifest and scripts have to pass too.
    root = repo_root()
    live = lint_manifest(root, root / "db/deploy/manifest.txt")
    if live != [
        "db/deploy/000_live_objects.sql",
        "db/deploy/010_climate_seasonal.sql",
        "db/deploy/020_backend_bridge.sql",
        "db/deploy/030_seasonal_alert.sql",
        "db/deploy/040_cooperatives_registry.sql",
    ]:
        raise AssertionError(live)
    print("check_manifest self-test ok")
    for rel in live:
        print(f"  {rel}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint the NgaoDB deploy manifest")
    parser.add_argument("--manifest", type=Path, help="Manifest path (default: db/deploy/manifest.txt)")
    parser.add_argument("--root", type=Path, help="Repository root (default: this repo)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    root = (args.root or repo_root()).resolve()
    manifest = args.manifest or (root / "db/deploy/manifest.txt")
    try:
        ordered = lint_manifest(root, manifest)
    except LintError as exc:
        emit_error(str(exc))
        return 1
    print(f"Manifest ok ({len(ordered)} scripts). No database connection was made.")
    for rel in ordered:
        print(f"  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
