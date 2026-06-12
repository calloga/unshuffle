#!/usr/bin/env python
from __future__ import annotations

"""Maintained review utility for regenerating GUI style/ownership issue trackers.

Generated markdown and SQLite outputs are review artifacts under `data/gui-review/`
and are not part of the V1 release package.
"""

import argparse
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data" / "unshuffle_inventory.md"
REVIEW_DIR = ROOT / "data" / "gui-review"
TRACKER_MD = REVIEW_DIR / "gui_live_tracker.md"
DB_PATH = REVIEW_DIR / ".tmp_gui_issue_tracker.db"

ISSUE_PATTERNS = [
    ("Centralizeable style (setStyleSheet)", re.compile(r"setStyleSheet\s*\(")),
    ("Magic number (fixed/min/max size)", re.compile(r"set(?:Fixed|Minimum|Maximum)(?:Size|Height|Width)\s*\(")),
    ("Magic number (margins/spacing)", re.compile(r"setContentsMargins\s*\(|setSpacing\s*\(")),
    ("Inline color literal", re.compile(r"#[0-9A-Fa-f]{3,8}|QColor\s*\(")),
    ("Direct settings usage", re.compile(r"QSettings\s*\(")),
]

STATUS_FLOW = ("Open", "In Progress", "Blocked", "Done", "Verified")


@dataclass
class IssueRow:
    file: str
    line_start: int
    line_end: int
    issue_type: str
    exact_finding: str
    required_action: str
    central_target: str
    status: str = "Open"
    done_criteria: str = "Replace with centralized token/helper and verify no duplicate unlisted occurrences remain in file."


def gui_files_from_inventory(inv_path: Path) -> list[str]:
    lines = inv_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        m = re.match(r"^- \[[ xX]\] `(?P<p>gui/[^`]+)`", line.strip())
        if m:
            out.append(m.group("p"))
    seen = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def finding_to_action(issue_type: str) -> tuple[str, str]:
    if issue_type == "Centralizeable style (setStyleSheet)":
        return (
            "Move inline stylesheet into gui/styles module and consume via unified style interface.",
            "gui/styles/*",
        )
    if issue_type == "Magic number (fixed/min/max size)":
        return (
            "Replace literal size values with shared geometry token constants.",
            "gui/styles/tokens_geometry.py",
        )
    if issue_type == "Magic number (margins/spacing)":
        return (
            "Replace literal spacing/margin values with shared spacing scale tokens.",
            "gui/styles/tokens_geometry.py",
        )
    if issue_type == "Inline color literal":
        return (
            "Replace raw color literal with semantic theme token.",
            "gui/styles/tokens_semantic.py",
        )
    if issue_type == "Direct settings usage":
        return (
            "Route settings access through SettingsController or a dedicated settings facade.",
            "gui/core/settings_controller.py",
        )
    return ("Review and centralize.", "gui/styles/*")


def scan_file_for_issues(abs_path: Path, rel_path: str) -> list[IssueRow]:
    lines = abs_path.read_text(encoding="utf-8").splitlines()
    issues: list[IssueRow] = []
    for i, line in enumerate(lines, start=1):
        for issue_name, pattern in ISSUE_PATTERNS:
            if pattern.search(line):
                req, target = finding_to_action(issue_name)
                exact = line.strip()
                issues.append(
                    IssueRow(
                        file=rel_path,
                        line_start=i,
                        line_end=i,
                        issue_type=issue_name,
                        exact_finding=exact if exact else "(blank line context)",
                        required_action=req,
                        central_target=target,
                    )
                )
    return issues


def scan_all() -> list[IssueRow]:
    rows: list[IssueRow] = []
    for rel in gui_files_from_inventory(INVENTORY):
        abs_path = ROOT / rel
        if not abs_path.exists() or abs_path.is_dir():
            continue
        rows.extend(scan_file_for_issues(abs_path, rel))
    return rows


def write_tracker_md(rows: list[IssueRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# GUI Live Tracker",
        "",
        "Generated from `data/unshuffle_inventory.md` GUI entries.",
        "Atomic rows are line-scoped and beginner-closable.",
        "",
        "| ID | File | Line start | Line end | Issue type | Exact finding | Required action | Central target | Status | Done criteria |",
        "|---:|---|---:|---:|---|---|---|---|---|---|",
    ]
    for idx, r in enumerate(rows, start=1):
        def esc(s: str) -> str:
            return s.replace("|", "\\|").replace("`", "\\`")

        lines.append(
            "| {id} | `{file}` | {ls} | {le} | {it} | `{ef}` | {ra} | `{ct}` | {st} | {dc} |".format(
                id=idx,
                file=esc(r.file),
                ls=r.line_start,
                le=r.line_end,
                it=esc(r.issue_type),
                ef=esc(r.exact_finding),
                ra=esc(r.required_action),
                ct=esc(r.central_target),
                st=esc(r.status),
                dc=esc(r.done_criteria),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY,
            file TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            issue_type TEXT NOT NULL,
            exact_finding TEXT NOT NULL,
            required_action TEXT NOT NULL,
            central_target TEXT NOT NULL,
            status TEXT NOT NULL,
            done_criteria TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_file ON issues(file)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status)")
    conn.commit()
    return conn


def reset_db_from_rows(conn: sqlite3.Connection, rows: list[IssueRow]) -> None:
    conn.execute("DELETE FROM issues")
    for idx, r in enumerate(rows, start=1):
        conn.execute(
            """
            INSERT INTO issues (id, file, line_start, line_end, issue_type, exact_finding, required_action, central_target, status, done_criteria)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idx,
                r.file,
                r.line_start,
                r.line_end,
                r.issue_type,
                r.exact_finding,
                r.required_action,
                r.central_target,
                r.status,
                r.done_criteria,
            ),
        )
    conn.commit()


def list_file(conn: sqlite3.Connection, rel_file: str) -> None:
    cur = conn.execute(
        "SELECT id, line_start, line_end, issue_type, status FROM issues WHERE file=? ORDER BY line_start, id",
        (rel_file,),
    )
    rows = cur.fetchall()
    if not rows:
        print(f"No issues for {rel_file}")
        return
    print(f"Issues for {rel_file}")
    for r in rows:
        print(f"#{r[0]} L{r[1]}-{r[2]} [{r[4]}] {r[3]}")


def update_status(conn: sqlite3.Connection, issue_id: int, status: str) -> None:
    if status not in STATUS_FLOW:
        raise ValueError(f"Invalid status: {status}. Allowed: {', '.join(STATUS_FLOW)}")
    conn.execute("UPDATE issues SET status=? WHERE id=?", (status, issue_id))
    conn.commit()
    print(f"Updated issue #{issue_id} -> {status}")


def add_issue(conn: sqlite3.Connection, row: IssueRow) -> None:
    cur = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM issues")
    next_id = cur.fetchone()[0]
    conn.execute(
        """
        INSERT INTO issues (id, file, line_start, line_end, issue_type, exact_finding, required_action, central_target, status, done_criteria)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            next_id,
            row.file,
            row.line_start,
            row.line_end,
            row.issue_type,
            row.exact_finding,
            row.required_action,
            row.central_target,
            row.status,
            row.done_criteria,
        ),
    )
    conn.commit()
    print(f"Added issue #{next_id}")


def stats(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    print(f"Total issues: {total}")
    for status, count in conn.execute("SELECT status, COUNT(*) FROM issues GROUP BY status ORDER BY status"):
        print(f"{status}: {count}")


def verify_file(conn: sqlite3.Connection, rel_file: str) -> int:
    """Scan file now and report occurrences that have no corresponding open row by (line, issue_type)."""
    abs_path = ROOT / rel_file
    if not abs_path.exists():
        print(f"Missing file: {rel_file}")
        return 2

    current = scan_file_for_issues(abs_path, rel_file)
    current_keys = {(r.line_start, r.issue_type) for r in current}

    db_rows = conn.execute(
        "SELECT line_start, issue_type FROM issues WHERE file=?", (rel_file,)
    ).fetchall()
    db_keys = {(int(ls), it) for ls, it in db_rows}

    missing = sorted(current_keys - db_keys)
    if not missing:
        print(f"No unlisted occurrences for {rel_file}.")
        return 0

    print(f"Unlisted occurrences found for {rel_file}:")
    for ls, it in missing:
        print(f"  L{ls}: {it}")
    return 1


def export_tracker_md_from_db(conn: sqlite3.Connection, path: Path) -> None:
    cur = conn.execute(
        "SELECT file, line_start, line_end, issue_type, exact_finding, required_action, central_target, status, done_criteria FROM issues ORDER BY id"
    )
    rows = [IssueRow(*r) for r in cur.fetchall()]
    write_tracker_md(rows, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="GUI issue tracker utility")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("rebuild", help="Rescan GUI files from inventory and rebuild markdown + sqlite db")

    p_list = sub.add_parser("list-file", help="List issues for a file")
    p_list.add_argument("file", help="e.g. gui/widgets/sidebar.py")

    p_upd = sub.add_parser("set-status", help="Update issue status")
    p_upd.add_argument("id", type=int)
    p_upd.add_argument("status", choices=STATUS_FLOW)

    p_add = sub.add_parser("add", help="Add manual issue row")
    p_add.add_argument("file")
    p_add.add_argument("line_start", type=int)
    p_add.add_argument("line_end", type=int)
    p_add.add_argument("issue_type")
    p_add.add_argument("exact_finding")
    p_add.add_argument("required_action")
    p_add.add_argument("central_target")

    sub.add_parser("stats", help="Show tracker stats")

    p_verify = sub.add_parser("verify-file", help="Verify no unlisted occurrences remain in file")
    p_verify.add_argument("file")

    sub.add_parser("export-md", help="Export tracker markdown from sqlite db")

    args = parser.parse_args()

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db(DB_PATH)

    if args.cmd == "rebuild":
        rows = scan_all()
        write_tracker_md(rows, TRACKER_MD)
        reset_db_from_rows(conn, rows)
        print(f"Rebuilt tracker: {TRACKER_MD}")
        print(f"DB: {DB_PATH}")
        print(f"Rows: {len(rows)}")
        return

    if args.cmd == "list-file":
        list_file(conn, args.file)
        return

    if args.cmd == "set-status":
        update_status(conn, args.id, args.status)
        return

    if args.cmd == "add":
        row = IssueRow(
            file=args.file,
            line_start=args.line_start,
            line_end=args.line_end,
            issue_type=args.issue_type,
            exact_finding=args.exact_finding,
            required_action=args.required_action,
            central_target=args.central_target,
        )
        add_issue(conn, row)
        return

    if args.cmd == "stats":
        stats(conn)
        return

    if args.cmd == "verify-file":
        raise SystemExit(verify_file(conn, args.file))

    if args.cmd == "export-md":
        export_tracker_md_from_db(conn, TRACKER_MD)
        print(f"Exported markdown tracker: {TRACKER_MD}")
        return


if __name__ == "__main__":
    main()
