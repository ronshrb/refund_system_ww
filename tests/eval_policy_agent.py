"""Live policy-drafter eval. Runs when this module is started and a key is set.

Does not write ``data/raw``, a policy file, or the live pointer. Each case
starts from the empty template. A missing key skips the call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from tests.policy_agent_cases import policy_cases, score_turn
from utils.policy.llm_provider import llm_configured, make_chat_model
from utils.policy.policy_agent import run_policy_agent_turn

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATE = _REPO / "policies" / "policy.template.json"
_POINTER = _REPO / "policies" / "live_pointer.json"
_POLICIES = _REPO / "policies"
_RAW = _REPO / "data" / "raw"
_COLUMNS = (
    "Case id",
    "What was tested",
    "Manager text",
    "Assistant text",
    "Draft updated",
    "Allow rules",
    "Result",
    "Failed checks",
)


def skip_reason(*, configured: bool | None = None) -> str | None:
    """Why the live eval must not call a provider, or None when it may run."""
    has_key = llm_configured() if configured is None else configured
    if not has_key:
        return "Policy agent eval skipped. The provider key is missing."
    return None


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.iterdir() if path.is_file()}


def _record(case: dict, turn: dict) -> dict:
    failures = score_turn(case, turn)
    allow = (turn.get("draft") or {}).get("allow") or []
    return {
        "id": case["id"],
        "tested": case["tested"],
        "manager": case["manager"],
        "assistant": turn.get("assistant_text") or "",
        "draft_updated": bool(turn.get("draft_updated")),
        "allow": json.dumps(allow, ensure_ascii=False),
        "result": "fail" if failures else "pass",
        "failed": ", ".join(failures),
    }


def report_lines(rows: list[dict]) -> list[str]:
    """Pass or fail for each case, then rubric accuracy."""
    lines = []
    for row in rows:
        if row["result"] == "pass":
            lines.append(f"{row['id']} pass")
            continue
        lines.append(f"{row['id']} fail: {row['failed']}")
        assistant = (row["assistant"] or "").strip()
        lines.append(assistant if assistant else "(no assistant text)")
    passed = sum(1 for row in rows if row["result"] == "pass")
    total = len(rows)
    percent = round(100 * passed / total) if total else 0
    lines.append(f"rubric accuracy: {passed}/{total} ({percent}%)")
    return lines


def write_policy_workbook(rows: list[dict], path: Path) -> Path:
    """One row per manager turn. The score is a formula over the result column."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cases"
    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    body_font = Font(name="Arial", size=10)
    wrap = Alignment(wrap_text=True, vertical="top")
    sheet.append(list(_COLUMNS))
    for cell in sheet[1]:
        cell.font = header_font
        cell.fill = header_fill
    for row in rows:
        sheet.append([
            row["id"],
            row["tested"],
            row["manager"],
            row["assistant"],
            "yes" if row["draft_updated"] else "no",
            row["allow"],
            row["result"],
            row["failed"],
        ])
    for grid in sheet.iter_rows(min_row=2, max_row=sheet.max_row, max_col=len(_COLUMNS)):
        for cell in grid:
            cell.font = body_font
            cell.alignment = wrap
    for letter, width in {
        "A": 16, "B": 56, "C": 56, "D": 56, "E": 16, "F": 56, "G": 12, "H": 42,
    }.items():
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(_COLUMNS))}{sheet.max_row}"
    last = sheet.max_row
    score = workbook.create_sheet("Score")
    score["A1"] = "Rubric accuracy"
    score["B1"] = f'=COUNTIF(Cases!G2:G{last},"pass")/COUNTA(Cases!A2:A{last})'
    score["A3"] = (
        "A case passes when the draft stays inside what the manager asked. "
        "These turns are invented. They are not data/raw. "
        "The score is a formula over the Cases sheet. "
        "This run does not activate a policy."
    )
    score["A1"].font = Font(name="Arial", bold=True, size=11)
    score["B1"].font = Font(name="Arial", size=11)
    score["B1"].number_format = "0%"
    score["A3"].font = Font(name="Arial", size=10, italic=True)
    score["A3"].alignment = wrap
    score.column_dimensions["A"].width = 22
    score.column_dimensions["B"].width = 14
    score.row_dimensions[3].height = 48
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def eval_workbook_path() -> Path:
    """Where a live run writes the workbook. Not under ``data/raw``."""
    return _REPO / "eval-results" / "policy_agent_eval.xlsx"


def main(argv: list[str] | None = None) -> int:
    """Run the five turns, or print the skip line and return 0."""
    del argv
    reason = skip_reason()
    if reason:
        print(reason)
        return 0
    before_policies = _snapshot(_POLICIES)
    before_raw = _snapshot(_RAW)
    template = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
    model = make_chat_model()
    rows = []
    for case in policy_cases():
        turn = run_policy_agent_turn(
            user_text=case["manager"],
            history=[],
            current_draft=template,
            chat_model=model,
        )
        rows.append(_record(case, turn))
    lines = report_lines(rows)
    for line in lines:
        print(line)
    saved = write_policy_workbook(rows, eval_workbook_path())
    print(f"saved {saved}")
    if _snapshot(_POLICIES) != before_policies or _snapshot(_RAW) != before_raw:
        print("fail: a policy file or data/raw changed")
        return 1
    return 1 if any(row["result"] == "fail" for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
