"""Live policy-drafter eval. Runs when this module is started and a key is set.

Does not write ``data/raw``, a policy file, or the live pointer. Each case
starts from the empty template. A case whose agent asks to confirm first gets
one scripted "yes" turn. Replies to the negative cases are graded by the
offline judge in ``tests/summary_judge.py``; the draft checks stay exact.
A missing key skips the call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from tests.policy_agent_cases import policy_cases, score_turn
from tests.summary_judge import calibrate, calibration_line, failed_checks, judge_summary
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


def _record(case: dict, turn: dict, assistant: str | None = None, judge_failures=None) -> dict:
    failures = score_turn(case, turn) + list(judge_failures or [])
    allow = (turn.get("draft") or {}).get("allow") or []
    return {
        "id": case["id"],
        "tested": case["tested"],
        "manager": case["manager"],
        "assistant": assistant if assistant is not None else (turn.get("assistant_text") or ""),
        "draft_updated": bool(turn.get("draft_updated")),
        "allow": json.dumps(allow, ensure_ascii=False),
        "result": "fail" if failures else "pass",
        "failed": "; ".join(failures),
    }


def run_case(case: dict, template: dict, model) -> tuple[dict, str]:
    """Run one manager turn, plus the scripted confirmation when the agent asks first.

    The agent may ask "is that correct?" before drafting. A case with a
    ``confirm`` message then sends it as a second turn, as the manager would.
    Returns the last turn and the text shown for the whole exchange.
    """
    first = run_policy_agent_turn(
        user_text=case["manager"],
        history=[],
        current_draft=template,
        chat_model=model,
    )
    first_text = first.get("assistant_text") or ""
    confirm = case.get("confirm")
    if not confirm or ((first.get("draft") or {}).get("allow")):
        return first, first_text
    second = run_policy_agent_turn(
        user_text=confirm,
        history=[
            {"role": "user", "content": case["manager"]},
            {"role": "assistant", "content": first_text},
        ],
        current_draft=first.get("draft") or template,
        chat_model=model,
    )
    shown = (
        f"Turn 1: {first_text}\n\nManager: {confirm}\n\n"
        f"Turn 2: {second.get('assistant_text') or ''}"
    )
    return second, shown


def judge_policy_reply(case: dict, text: str, judge_model) -> tuple[list[str], dict | None]:
    """Judge failures for a case's reply, and the judge check on its labelled replies.

    Only cases with criteria are judged. The draft checks stay exact.
    """
    if not case.get("convey"):
        return [], None
    verdicts = judge_summary(case, text, chat_model=judge_model)
    return failed_checks(verdicts), calibrate(case, chat_model=judge_model)


def report_lines(rows: list[dict], calibration: dict | None = None) -> list[str]:
    """Pass or fail for each case, rubric accuracy, then the judge check."""
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
    check = calibration_line(calibration)
    if check:
        lines.append(check)
    return lines


def write_policy_workbook(rows: list[dict], path: Path, calibration: dict | None = None) -> Path:
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
    if calibration:
        check = workbook.create_sheet("Judge check")
        check.append(["Case id", "Judge accepted the good reply", "Judge rejected the bad reply"])
        for cell in check[1]:
            cell.font = header_font
            cell.fill = header_fill
        for case_id, flags in calibration.items():
            check.append([case_id, flags["good_accepted"], flags["bad_rejected"]])
        total = check.max_row
        check["E1"] = "Agreement with hand labels"
        check["F1"] = (
            f"=(COUNTIF(B2:B{total},TRUE)+COUNTIF(C2:C{total},TRUE))/(2*COUNTA(A2:A{total}))"
        )
        check["F1"].number_format = "0%"
        check.column_dimensions["A"].width = 16
        check.column_dimensions["B"].width = 30
        check.column_dimensions["C"].width = 30
        check.column_dimensions["E"].width = 30
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
    calibration = {}
    for case in policy_cases():
        turn, shown = run_case(case, template, model)
        judge_failures, check = judge_policy_reply(case, turn.get("assistant_text") or "", model)
        if check is not None:
            calibration[case["id"]] = check
        rows.append(_record(case, turn, shown, judge_failures))
    lines = report_lines(rows, calibration)
    for line in lines:
        print(line)
    saved = write_policy_workbook(rows, eval_workbook_path(), calibration)
    print(f"saved {saved}")
    if _snapshot(_POLICIES) != before_policies or _snapshot(_RAW) != before_raw:
        print("fail: a policy file or data/raw changed")
        return 1
    return 1 if any(row["result"] == "fail" for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
