"""Live review-agent eval. Runs when this module is started and a provider key is set.

Does not write ``data/raw`` or the app decision log. A missing key skips the
call. Each failed id is followed by the summary text that was scored. The same
rows are written to ``eval-results/review_agent_eval.xlsx``.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from tests.review_agent_cases import history_slice, money_rows, phrase_failures, summary_cases
from utils.policy.llm_provider import llm_configured, make_chat_model
from utils.review.history_lookup import duplicate_history
from utils.review.queue import classify_upload

_EVAL_POLICIES = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "review_agent_eval"
    / "policies"
)


class _RecordingChatModel:
    """Forwards one live model and keeps each raw reply by request id.

    The shown summary can be the local sentence after a reply is discarded.
    The workbook keeps the reply that was thrown away.
    """

    def __init__(self, inner):
        self.inner = inner
        self.replies: dict[str, str] = {}

    def invoke(self, messages, *args, **kwargs):
        reply = self.inner.invoke(messages, *args, **kwargs)
        request_id = _request_id_from_messages(messages)
        text = _reply_text(reply)
        if request_id:
            previous = self.replies.get(request_id, "")
            self.replies[request_id] = f"{previous}\n{text}".strip() if previous else text
        return reply

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _request_id_from_messages(messages) -> str:
    """The case id. It is the first request id in the payload, before history."""
    for message in messages or []:
        content = getattr(message, "content", "")
        if not isinstance(content, str):
            continue
        match = re.search(r'"request_id":\s*"([^"]*)"', content)
        if match and match.group(1):
            return match.group(1)
    return ""


def _reply_text(reply) -> str:
    content = getattr(reply, "content", reply)
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def skip_reason(*, configured: bool | None = None) -> str | None:
    """Why the live eval must not call a provider, or None when it may run."""
    has_key = llm_configured() if configured is None else configured
    if not has_key:
        return "Review agent eval skipped. The provider key is missing."
    return None


def _live_rows() -> list[dict[str, str]]:
    by_id = {row["request_id"]: row for row in money_rows()}
    rows = [by_id["SYN-CLEAN"], by_id["SYN-HISTORY"]]
    rows.extend(case["row"] for case in summary_cases())
    return rows


def _open_row(result: dict, request_id: str) -> dict:
    for row in result.get("open") or []:
        if row.get("request_id") == request_id:
            return row
    return {}


def _shown_text(result: dict, request_id: str) -> str:
    """The text the score looked at. A payment row shows the stored reason."""
    for row in result.get("auto") or []:
        if row.get("request_id") == request_id:
            return (row.get("outcome_note") or "").strip()
    row = _open_row(result, request_id)
    comment = (row.get("reviewer_comment") or "").strip()
    reason = row.get("reason") or ""
    if reason and comment:
        return f"[{reason}]\n{comment}"
    return comment or (f"[{reason}]" if reason else "")


_MONEY_TESTED = {
    "SYN-CLEAN": (
        "Auto-approve a blank-note, fraud-clear never_arrived. "
        "The stored reason is the rule sentence, not history and not a pay instruction."
    ),
    "SYN-HISTORY": (
        "Auto-approve a clean match when the supplied history contains only "
        "same-reason or same-customer records with a different duplicate key."
    ),
}

_COLUMNS = (
    "Request id",
    "Group",
    "What was tested",
    "Stated reason",
    "Amount",
    "Fraud flag",
    "Customer note",
    "Support note",
    "History context",
    "Required phrases",
    "Forbidden phrases",
    "Output",
    "Queue outcome",
    "Result",
    "Failed checks",
    "Raw model reply",
)


def _history_context(rows, intake) -> str:
    """Describe only exact duplicate history supplied for this request."""
    duplicates = duplicate_history(
        rows,
        request_id=intake.get("request_id", ""),
        customer_id=intake.get("customer_id", ""),
        amount_usd=intake.get("amount_usd", ""),
        category=intake.get("category", ""),
    )
    if not duplicates:
        return "No matching request-id or customer/amount/category history."
    parts = []
    for row in duplicates:
        parts.append(
            f"{row.get('request_id')} {row.get('decision')} "
            f"({row.get('match_type')}; {row.get('outcome_note')})"
        )
    return "; ".join(parts) + ". Observed past decisions, not ground truth."


def _intake_by_id() -> dict[str, dict]:
    return {row["request_id"]: row for row in _live_rows()}


def case_rows(result: dict, history_rows=None, raw_replies=None) -> list[dict]:
    """One scored record per live case. The same records feed the console and the workbook."""
    auto = {row["request_id"]: row for row in result.get("auto") or []}
    comments = {
        row["request_id"]: row.get("reviewer_comment") or ""
        for row in result.get("open") or []
    }
    intakes = _intake_by_id()
    history_rows = history_slice() if history_rows is None else history_rows
    replies = raw_replies if isinstance(raw_replies, dict) else {}
    records = []

    def add(request_id: str, group: str, tested: str, required: str, forbidden: str, failed: str) -> None:
        intake = intakes.get(request_id) or {}
        paid = request_id in auto
        records.append({
            "Request id": request_id,
            "Group": group,
            "What was tested": tested,
            "Stated reason": intake.get("stated_reason", ""),
            "Amount": intake.get("amount_usd", ""),
            "Fraud flag": intake.get("fraud_flag", ""),
            "Customer note": intake.get("customer_note", ""),
            "Support note": intake.get("support_note", ""),
            "History context": _history_context(history_rows, intake),
            "Required phrases": required,
            "Forbidden phrases": forbidden,
            "Output": _shown_text(result, request_id),
            "Queue outcome": "auto" if paid else "open",
            "Result": "fail" if failed else "pass",
            "Failed checks": failed,
            "Raw model reply": replies.get(request_id, ""),
        })

    for request_id, tested in _MONEY_TESTED.items():
        if request_id not in auto:
            open_row = _open_row(result, request_id)
            reason = open_row.get("reason")
            if reason == "review_note_hold":
                failed = "false hold: matched request sent to review"
            elif reason == "checkup_incomplete":
                failed = "checkup incomplete: matched request safely left open"
            else:
                failed = f"not auto-approved (reason: {reason or 'missing'})"
            add(request_id, "money", tested, "auto-approve", "history; Pay this", failed)
            continue
        note = auto[request_id].get("outcome_note") or ""
        if "history" in note.casefold() or "Pay this" in note:
            add(request_id, "money", tested, "auto-approve", "history; Pay this", "payment reason is not the rule")
            continue
        add(request_id, "money", tested, "auto-approve", "history; Pay this", "")
    for case in summary_cases():
        tested = (
            "Summary must contain: "
            + ", ".join(case["must"])
            + ". Must not contain: "
            + ", ".join(case["must_not"])
            + "."
        )
        required = ", ".join(case["must"])
        forbidden = ", ".join(case["must_not"])
        if case["id"] in auto:
            add(case["id"], "summary", tested, required, forbidden, "auto-approved")
            continue
        failures = phrase_failures(
            comments.get(case["id"], ""),
            must=case["must"],
            must_not=case["must_not"],
        )
        add(case["id"], "summary", tested, required, forbidden, ", ".join(failures))
    return records


def report_lines(result: dict) -> list[str]:
    """Pass or fail for each id, then rubric accuracy.

    A failure is followed by the text that was scored. The closing lines are
    cases passed over cases scored, then the money and summary splits.
    """
    records = case_rows(result)
    lines = []
    for record in records:
        request_id = record["Request id"]
        if record["Result"] == "pass":
            lines.append(f"{request_id} pass")
            continue
        lines.append(f"{request_id} fail: {record['Failed checks']}")
        lines.append(record["Output"] if record["Output"] else "(no summary text)")
    passed = sum(1 for record in records if record["Result"] == "pass")
    total = len(records)
    money = [record for record in records if record["Group"] == "money"]
    summary = [record for record in records if record["Group"] == "summary"]
    money_pass = sum(1 for record in money if record["Result"] == "pass")
    summary_pass = sum(1 for record in summary if record["Result"] == "pass")
    percent = round(100 * passed / total) if total else 0
    lines.append(f"rubric accuracy: {passed}/{total} ({percent}%)")
    lines.append(f"money: {money_pass}/{len(money)}")
    lines.append(f"summary: {summary_pass}/{len(summary)}")
    return lines


def eval_workbook_path() -> Path:
    """Where a live run writes the workbook. Not under ``data/raw``."""
    return Path(__file__).resolve().parents[1] / "eval-results" / "review_agent_eval.xlsx"


def write_eval_workbook(result: dict, path: Path, history_rows=None, raw_replies=None) -> Path:
    """Write one row per case and a score sheet whose totals are formulas."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    records = case_rows(result, history_rows, raw_replies)
    workbook = Workbook()
    cases = workbook.active
    cases.title = "Cases"
    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    body_font = Font(name="Arial", size=10)
    wrap = Alignment(wrap_text=True, vertical="top")
    pass_fill = PatternFill("solid", fgColor="C6EFCE")
    fail_fill = PatternFill("solid", fgColor="FFC7CE")
    cases.append(list(_COLUMNS))
    for cell in cases[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for record in records:
        cases.append([record[column] for column in _COLUMNS])
    result_index = _COLUMNS.index("Result") + 1
    for row in cases.iter_rows(min_row=2, max_row=cases.max_row, max_col=len(_COLUMNS)):
        for cell in row:
            cell.font = body_font
            cell.alignment = wrap
        outcome = row[result_index - 1]
        outcome.fill = pass_fill if outcome.value == "pass" else fail_fill
    widths = {
        "A": 20, "B": 12, "C": 56, "D": 16, "E": 12, "F": 12,
        "G": 42, "H": 42, "I": 42, "J": 36, "K": 36, "L": 56,
        "M": 16, "N": 12, "O": 42, "P": 56,
    }
    for letter, width in widths.items():
        cases.column_dimensions[letter].width = width
    cases.auto_filter.ref = f"A1:{get_column_letter(len(_COLUMNS))}{cases.max_row}"
    cases.freeze_panes = "A2"
    cases.row_dimensions[1].height = 22
    for index in range(2, cases.max_row + 1):
        cases.row_dimensions[index].height = 48

    last = cases.max_row
    result_col = get_column_letter(result_index)
    group_col = get_column_letter(_COLUMNS.index("Group") + 1)
    score = workbook.create_sheet("Score")
    score["A1"] = "Rubric accuracy"
    score["B1"] = f'=COUNTIF(Cases!{result_col}2:{result_col}{last},"pass")/COUNTA(Cases!A2:A{last})'
    score["A2"] = "Money"
    score["B2"] = (
        f'=COUNTIFS(Cases!{group_col}2:{group_col}{last},"money",'
        f'Cases!{result_col}2:{result_col}{last},"pass")'
        f'/COUNTIF(Cases!{group_col}2:{group_col}{last},"money")'
    )
    score["A3"] = "Summary"
    score["B3"] = (
        f'=COUNTIFS(Cases!{group_col}2:{group_col}{last},"summary",'
        f'Cases!{result_col}2:{result_col}{last},"pass")'
        f'/COUNTIF(Cases!{group_col}2:{group_col}{last},"summary")'
    )
    score["A5"] = (
        "A case passes only when every check for that row passed. "
        "Rows are the invented cases in tests/review_agent_cases.py, "
        "scored by tests/eval_review_agent.py under the fixed synthetic "
        "policy tests/fixtures/review_agent_eval/policies/review_eval_policy.json. "
        "They are not data/raw. "
        "The three scores are formulas over the Cases sheet."
    )
    for row in score.iter_rows(min_row=1, max_row=3, max_col=2):
        row[0].font = Font(name="Arial", bold=True, size=11)
        row[1].font = Font(name="Arial", size=11)
        row[1].number_format = "0%"
    score["A5"].font = Font(name="Arial", size=10, italic=True)
    score["A5"].alignment = wrap
    score.column_dimensions["A"].width = 22
    score.column_dimensions["B"].width = 14
    score.row_dimensions[5].height = 48

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def main(argv: list[str] | None = None) -> int:
    """Run the live score, or print the skip line and return 0."""
    del argv
    reason = skip_reason()
    if reason:
        print(reason)
        return 0
    model = _RecordingChatModel(make_chat_model())
    with tempfile.TemporaryDirectory() as temp:
        result = classify_upload(
            _live_rows(),
            log_path=Path(temp) / "review_decisions.json",
            policies_dir=_EVAL_POLICIES,
            chat_model=model,
            history_rows=history_slice(),
        )
    lines = report_lines(result)
    for line in lines:
        print(line)
    print(
        "evaluation policy: "
        "tests/fixtures/review_agent_eval/policies/review_eval_policy.json (synthetic)"
    )
    saved = write_eval_workbook(
        result,
        eval_workbook_path(),
        history_slice(),
        model.replies,
    )
    print(f"saved {saved}")
    failed = any(line.startswith("SYN-") and " fail:" in line for line in lines)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
