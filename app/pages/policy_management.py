"""Policy Management — live policy picker and conversational drafting.

Two tabs: Policy Picker chooses the live pointer after a history preview.
Policy Builder is the existing chatbot. Save writes a draft file and does not
move the pointer. This page does not approve refunds.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import streamlit as st

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from decimal import Decimal

from utils.data.dataloader import load_rows
from utils.policy import policy_agent

from utils.policy.live_policy import (
    approvals_csv,
    auto_approval_split,
    historical_auto_approvals,
    history_outcome_rates,
    list_policy_files,
    live_file_select_index,
    make_live,
    make_live_button_disabled,
    preview_auto_approvals,
    read_live_policy,
)
from utils.policy.policy_agent_utils import (
    CLARIFY_MARKER_PATTERN,
    CLARIFY_PRESET_PATTERNS,
    CLARIFY_PRESET_VALUES,
    HISTORY_PATH,
    POLICIES_DIR,
    clarify_pick_caption,
    parse_clarify_marker,
)
from utils.policy.policy_preview import PolicyInputError, parse_policy_file

_SESSION_MESSAGES_KEY = "policy_management_messages"
# Shown as the first chat bubble. Not stored in the transcript the model sees.
_BUILDER_INTRO = (
    "Describe the refund rule you want, and be clear about amounts, reasons, "
    "and who it applies to. Suggestions here are based on the history data "
    "and do not approve a refund. To compare policies and choose which one "
    "is live, use the Policy Picker tab."
)
_SESSION_DRAFT_KEY = "policy_management_draft"
_SESSION_PREVIEW_KEY = "policy_management_preview_summary"
_SESSION_CONFIRMED_KEY = "policy_management_confirmed_thresholds"
_SESSION_PENDING_KEY = "policy_management_pending_turn"
_SESSION_NOTICE_KEY = "policy_management_save_notice"
_SESSION_PROPOSAL_KEY = "policy_management_proposal"
_SESSION_NAME_STEP_KEY = "policy_management_name_step"
_SESSION_SUGGESTED_NAME_KEY = "policy_management_suggested_name"
_SESSION_BLOCKER_KEY = "policy_management_blocker"
_READY_MARKER = "[[ready_to_build]]"
_PARTIAL_MARKER = re.compile(r"\[(\[[^\]]*\]?)?$")


def _latest_assistant_text(messages: list[dict[str, str]]) -> str:
    """Return the newest assistant message, including its hidden marker."""
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return message.get("content") or ""
    return ""


def _quick_pick_presets(kind: str, values: tuple[int, ...]) -> list[tuple[str, int]]:
    """Button labels for the choices the agent sent, or the fallback list."""
    if kind == "amount_usd_lte":
        return [(f"${value}", value) for value in values]
    if kind == "tenure_months_gte":
        return [(f"{value} months", value) for value in values]
    return [(f"{value} days", value) for value in values]


def _strip_clarify_markers(text: str) -> str:
    """Remove UI-only markers from assistant text shown to the manager."""
    return CLARIFY_MARKER_PATTERN.sub("", text).replace(_READY_MARKER, "").rstrip()

def _pending_clarify_kind(
    messages: list[dict[str, str]], confirmed: set[str]
) -> str | None:
    """Return the threshold the latest assistant message is asking for, if any.

    Uses the hidden marker when present. The model sometimes forgets it, so
    the preset list it was told to offer (e.g. "$25, $30, $50") also counts.
    Thresholds the manager already picked are skipped; if several are asked,
    the first one in the text wins.
    """
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        text = message.get("content") or ""
        found = [(m.start(), m.group(1)) for m in CLARIFY_MARKER_PATTERN.finditer(text)]
        for kind, pattern in CLARIFY_PRESET_PATTERNS.items():
            match = pattern.search(text)
            if match:
                found.append((match.start(), kind))
        for _, kind in sorted(found):
            if kind not in confirmed:
                return kind
        return None
    return None


def _markdown_safe(text: str) -> str:
    """Escape ``$`` so Streamlit does not render "$25, $30" as a math formula."""
    return text.replace("$", r"\$")


def _pick_list_reply(kind: str, value: int) -> str:
    """Build a plain-language user confirmation from a pick-list choice."""
    if kind == "amount_usd_lte":
        return f"Confirmed: refunds of ${value} or less."
    if kind == "tenure_months_gte":
        return f"Confirmed: customers for at least {value} months."
    return f"Confirmed: within {value} days of delivery."


def _render_user_text(text: str) -> None:
    """Show manager text with its line breaks (Markdown joins single newlines)."""
    st.markdown(_markdown_safe(text).replace("\n", "  \n"))


def _strip_streaming_text(text: str) -> str:
    """Hide full and half-received clarify markers while a reply streams in."""
    return _markdown_safe(_PARTIAL_MARKER.sub("", _strip_clarify_markers(text)))


def _queue_turn(user_text: str) -> None:
    """Queue a manager message; the next run renders and streams the reply."""
    st.session_state[_SESSION_PENDING_KEY] = user_text
    st.rerun()


def _confirm_pick(kind: str, value: int) -> None:
    """Record a pick so its Quick pick never reappears, then queue the turn."""
    st.session_state[_SESSION_CONFIRMED_KEY].add(kind)
    _queue_turn(_pick_list_reply(kind, value))


def _run_manager_turn(user_text: str) -> None:
    """Show the user turn, stream the agent reply, and update session state."""
    messages = st.session_state[_SESSION_MESSAGES_KEY]
    history_before = list(messages)
    previous_draft = st.session_state[_SESSION_DRAFT_KEY]
    previous_preview = st.session_state.get(_SESSION_PREVIEW_KEY)
    messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        _render_user_text(user_text)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("_Thinking…_")
        streamed: list[str] = []

        def show_delta(delta: str) -> None:
            streamed.append(delta)
            placeholder.markdown(_strip_streaming_text("".join(streamed)) + "▌")

        def clear_streamed() -> None:
            streamed.clear()
            placeholder.markdown("_Thinking…_")

        turn = policy_agent.run_policy_agent_turn(
            user_text=user_text,
            history=history_before,
            current_draft=st.session_state[_SESSION_DRAFT_KEY],
            on_text_delta=show_delta,
            on_text_reset=clear_streamed,
        )

    st.session_state[_SESSION_DRAFT_KEY] = turn["draft"]
    if turn.get("preview_summary") is not None:
        st.session_state[_SESSION_PREVIEW_KEY] = turn["preview_summary"]

    messages.append({"role": "assistant", "content": turn["assistant_text"]})

    # The page, not the model, decides what happens after a build. A draft is
    # offered for approval only when every threshold in it was stated by the
    # manager. Otherwise the working draft stays as it was and a pick list
    # asks for the missing value.
    st.session_state[_SESSION_NAME_STEP_KEY] = False
    if not turn["draft_updated"]:
        st.session_state[_SESSION_PROPOSAL_KEY] = None
        st.session_state[_SESSION_BLOCKER_KEY] = None
        return

    blockers = policy_agent.policy_confirmation_blockers(
        turn["draft"], [item for item in messages if item["role"] == "user"]
    )
    if blockers:
        st.session_state[_SESSION_DRAFT_KEY] = previous_draft
        if previous_preview is None:
            st.session_state.pop(_SESSION_PREVIEW_KEY, None)
        else:
            st.session_state[_SESSION_PREVIEW_KEY] = previous_preview
        st.session_state[_SESSION_PROPOSAL_KEY] = None
        st.session_state[_SESSION_BLOCKER_KEY] = blockers[0]
        return

    st.session_state[_SESSION_PROPOSAL_KEY] = turn["draft"]
    st.session_state[_SESSION_BLOCKER_KEY] = None


def _history_rows():
    """Load history for a read-only preview, or return an error message."""
    try:
        return load_rows(HISTORY_PATH), None
    except (OSError, UnicodeError, ValueError):
        return None, "Could not read history.csv. Nothing is shown as auto-approved."


def _load_candidate(file_name: str):
    """Parse one policy file for preview. Invalid files do not auto-approve."""
    try:
        return parse_policy_file(POLICIES_DIR / file_name), None
    except PolicyInputError:
        return None, "This file is not a usable policy. Nothing would auto-approve."


def _stored_summary_text(policy) -> str:
    """Return the summary stored on a parsed policy file.

    Display only. A missing or blank field is not filled in here.
    """
    if not isinstance(policy, dict):
        return "This file has no summary."
    summary = policy.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary
    return "This file has no summary."


def _percent_label(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _request_change_label(value: int) -> str:
    """Request-count change with no leading plus."""
    noun = "request" if abs(value) == 1 else "requests"
    if value < 0:
        return f"-{abs(value)} {noun}"
    return f"{value} {noun}"


def _money_change_label(value) -> str:
    """Dollar change with no leading plus. A decrease keeps the minus."""
    amount = Decimal(value)
    if amount < 0:
        return f"-${abs(amount):,.2f}"
    return f"${amount:,.2f}"


def _money_label(value) -> str:
    if value is None:
        return "—"
    return f"${Decimal(value):,.2f}"


def _percent_change_label(selected, other) -> str:
    """Selected policy minus the compared side, divided by the compared side."""
    if selected is None or other is None or other == 0:
        return "—"
    change = (Decimal(selected) - Decimal(other)) / Decimal(other)
    return f"{change * 100:.1f}%"


def _outcome_rates(policy, history_rows):
    """Score a policy, or no policy, on the history rows."""
    if history_rows is None:
        return None
    return history_outcome_rates(policy, history_rows)


def _render_download(matches, file_name: str) -> None:
    if not matches:
        return
    st.download_button(
        "Download",
        data=approvals_csv(matches),
        file_name=f"{Path(file_name).stem}-auto-approved.csv",
        mime="text/csv",
        key=f"download-{file_name}",
    )


def _render_policy_side(
    title, policy, matches, history_rows, file_name, *, recorded: bool = False
) -> None:
    """One summary line: name, rates, row count, and the download."""
    rates = _outcome_rates(policy, history_rows)
    parts = [f"**{title}**"]
    if rates is not None:
        parts.append(f"Auto-approved {_percent_label(rates['percent_auto_approved'])}")
        parts.append(
            f"Auto-refund dollars {_money_label(rates['auto_refund_dollars'])}"
        )
    if history_rows is not None:
        count = len(matches)
        noun = "row" if count == 1 else "rows"
        verb = "were auto-approved" if recorded else "would auto-approve"
        parts.append(f"{count} history {noun} {verb}.")
    line_col, download_col = st.columns([5, 1], vertical_alignment="center")
    line_col.markdown(_markdown_safe(" · ".join(parts)))
    if file_name and matches:
        with download_col:
            _render_download(matches, file_name)


def _render_comparison(selected_rates, other_rates) -> None:
    """Percent change beside each change. The fee is noted under the dollar change."""
    if selected_rates is None or other_rates is None:
        return
    approved_change = selected_rates["auto_approved"] - other_rates["auto_approved"]
    dollar_change = (
        selected_rates["auto_refund_dollars"] - other_rates["auto_refund_dollars"]
    )
    fee_change = selected_rates["fee_dollars"] - other_rates["fee_dollars"]
    percent_col, change_col = st.columns(2)
    percent_col.metric(
        "Auto-approved percent change",
        _percent_change_label(
            selected_rates["auto_approved"], other_rates["auto_approved"]
        ),
    )
    change_col.metric("Auto-approved change", _request_change_label(approved_change))
    percent_col, change_col = st.columns(2)
    percent_col.metric(
        "Auto-refund dollar percent change",
        _percent_change_label(
            selected_rates["auto_refund_dollars"],
            other_rates["auto_refund_dollars"],
        ),
    )
    change_col.metric(
        "Auto-refund dollar change",
        _money_change_label(dollar_change),
    )
    # The fee is $25 per auto refund. Show only its share of this dollar change.
    _, fee_col = st.columns(2)
    fee_col.markdown(_markdown_safe(f"{_money_change_label(fee_change)} of this is fees"))


def _render_auto_split(selected_matches, other_matches) -> None:
    """The net change split into new auto-approvals, ones that leave, and ones that stay."""
    split = auto_approval_split(selected_matches, other_matches)
    new_col, dropped_col, still_col = st.columns(3)
    _split_figure(new_col, "Newly auto-approved", split["new_count"], split["new_dollars"])
    _split_figure(
        dropped_col,
        "No longer auto-approved",
        split["dropped_count"],
        split["dropped_dollars"],
    )
    _split_figure(
        still_col, "Still auto-approved", split["still_count"], split["still_dollars"]
    )


def _split_figure(column, title: str, count, dollars) -> None:
    noun = "request" if count == 1 else "requests"
    column.markdown(
        _markdown_safe(f"**{title}** · {count} {noun} · {_money_label(dollars)}")
    )


_NO_COMPARE = "__no_compare__"
_WITHOUT_POLICY = "__without_policy__"


def _policy_choice_label(files, name: str) -> str:
    if name == _NO_COMPARE:
        return "Don't compare"
    if name == _WITHOUT_POLICY:
        return "Without policy"
    item = next(entry for entry in files if entry["file"] == name)
    if item.get("status"):
        return f"{item['file']} ({item['status']})"
    return f"{item['file']} (no status)"


def _render_policy_picker() -> None:
    """Select and preview a policy. Only the button writes the live pointer."""
    files = list_policy_files()
    history_rows, history_error = _history_rows()
    live = read_live_policy()
    live_file = live["file"] if live else None
    selected = None
    compare_with = _NO_COMPARE
    candidate = None
    candidate_error = None
    if not files:
        st.warning("No policy files in policies/.")
    else:
        names = [item["file"] for item in files]
        file_col, compare_col = st.columns(2)
        with file_col:
            # index= is the first-open default. Do not also assign the widget key.
            selected = st.selectbox(
                "Policy file",
                options=names,
                index=live_file_select_index(names, live_file),
                format_func=lambda name: _policy_choice_label(files, name),
                key="policy_picker_selection",
            )
            candidate, candidate_error = _load_candidate(selected)
            st.markdown(_markdown_safe(_stored_summary_text(candidate)))
            # Already-live stays disabled, and the click guard does not write the pointer.
            already_live = make_live_button_disabled(selected, live_file)
            make_clicked = st.button(
                "Make this the live policy",
                type="primary",
                key="make_this_the_live_policy",
                disabled=already_live,
            )
        with compare_col:
            compare_with = st.selectbox(
                "Compare with",
                options=[_NO_COMPARE, _WITHOUT_POLICY, *[item["file"] for item in files]],
                format_func=lambda name: _policy_choice_label(files, name),
                key="policy_picker_compare",
            )
        if selected and make_clicked and not already_live:
            try:
                make_live(selected)
            except (OSError, ValueError) as error:
                st.error(f"Not made live: {error}")
            else:
                st.rerun()

    candidate_matches = []
    if candidate is not None and history_rows is not None:
        candidate_matches = preview_auto_approvals(candidate, history_rows)

    comparing = compare_with != _NO_COMPARE
    other_policy = None
    other_error = None
    other_name = "Without policy"
    if comparing and compare_with != _WITHOUT_POLICY:
        other_name = compare_with
        other_policy, other_error = _load_candidate(compare_with)

    other_matches = []
    other_recorded = compare_with == _WITHOUT_POLICY
    if comparing and history_rows is not None and other_recorded:
        other_matches = historical_auto_approvals(history_rows)
    elif comparing and other_policy is not None and history_rows is not None:
        other_matches = preview_auto_approvals(other_policy, history_rows)

    if history_error:
        st.warning(history_error)
    if candidate_error:
        st.error(candidate_error)
    if other_error:
        st.error(other_error)

    if comparing:
        _render_comparison(
            _outcome_rates(candidate, history_rows),
            _outcome_rates(other_policy, history_rows),
        )
        if candidate is not None and history_rows is not None and (
            other_recorded or other_policy is not None
        ):
            _render_auto_split(candidate_matches, other_matches)
        _render_policy_side(
            other_name,
            other_policy,
            other_matches,
            history_rows,
            "history-auto" if other_recorded else f"compare-{compare_with}",
            recorded=other_recorded,
        )
    _render_policy_side(
        selected or "Selected",
        candidate,
        candidate_matches,
        history_rows,
        selected or "",
    )


def _render_policy_builder() -> None:
    """Existing chatbot. Save writes a draft and does not move the live pointer."""
    policy_agent.ensure_session_defaults(
        st.session_state,
        messages_key=_SESSION_MESSAGES_KEY,
        draft_key=_SESSION_DRAFT_KEY,
        preview_key=_SESSION_PREVIEW_KEY,
    )
    st.session_state.setdefault(_SESSION_CONFIRMED_KEY, set())
    st.session_state.setdefault(_SESSION_PROPOSAL_KEY, None)
    st.session_state.setdefault(_SESSION_NAME_STEP_KEY, False)
    st.session_state.setdefault(_SESSION_BLOCKER_KEY, None)

    if not policy_agent.llm_configured():
        st.info(policy_agent.offline_message())

    preview = st.session_state.get(_SESSION_PREVIEW_KEY)
    if preview:
        st.markdown(policy_agent.format_preview_summary_for_manager(preview))

    messages = st.session_state[_SESSION_MESSAGES_KEY]
    with st.chat_message("assistant"):
        st.markdown(_BUILDER_INTRO)
    for message in messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                st.markdown(_markdown_safe(_strip_clarify_markers(message["content"])))
            else:
                _render_user_text(message["content"])

    pending_text = st.session_state.pop(_SESSION_PENDING_KEY, None)
    if pending_text:
        _run_manager_turn(pending_text)
        st.rerun()

    notice = st.session_state.pop(_SESSION_NOTICE_KEY, None)
    if notice:
        level, text = notice
        getattr(st, level)(text)

    if st.session_state[_SESSION_PROPOSAL_KEY] is not None:
        proposal = st.session_state[_SESSION_PROPOSAL_KEY]
        if not st.session_state[_SESSION_NAME_STEP_KEY]:
            st.subheader("Does this policy look right?")
            yes_col, no_col = st.columns(2)
            if yes_col.button("Yes, save it", type="primary"):
                st.session_state[_SESSION_NAME_STEP_KEY] = True
                st.session_state[_SESSION_SUGGESTED_NAME_KEY] = (
                    policy_agent.suggest_policy_name(proposal)
                )
                st.rerun()
            if no_col.button("I want to change it"):
                st.session_state[_SESSION_PROPOSAL_KEY] = None
                st.rerun()
        else:
            st.subheader("Save Policy")
            st.caption("Name this policy, then click Save. It will be stored under policies/.")
            name = st.text_input(
                "Policy name",
                value=st.session_state.get(_SESSION_SUGGESTED_NAME_KEY, ""),
                key=f"policy_name_{len(messages)}",
            ).strip()
            if st.button("Save", type="primary", key=f"policy_save_{len(messages)}"):
                name_error = policy_agent.policy_name_error(name)
                if name_error:
                    st.error(name_error)
                else:
                    _save_proposal(name)

    blocker = st.session_state[_SESSION_BLOCKER_KEY]
    clarify_kind = (
        blocker["kind"]
        if blocker
        else _pending_clarify_kind(messages, st.session_state[_SESSION_CONFIRMED_KEY])
    )
    if clarify_kind and policy_agent.llm_configured() and st.session_state[_SESSION_PROPOSAL_KEY] is None:
        st.subheader("Quick pick")
        if blocker:
            st.info(blocker["detail"])
        _ask_text = blocker["detail"] if blocker else _latest_assistant_text(messages)
        _parsed = None if blocker else parse_clarify_marker(_ask_text)
        _values = (
            _parsed["values"]
            if _parsed and _parsed["values"]
            else CLARIFY_PRESET_VALUES[clarify_kind]
        )
        if _parsed and _parsed["topic"]:
            st.caption(
                f"{_parsed['topic']}. Choose one of these or enter a custom value — "
                "one clear answer so we do not invent a threshold."
            )
        else:
            st.caption(clarify_pick_caption(clarify_kind, _ask_text))
        cols = st.columns(len(_values) + 1)
        for index, (label, value) in enumerate(_quick_pick_presets(clarify_kind, _values)):
            if cols[index].button(label, key=f"clarify_preset_{clarify_kind}_{value}"):
                _confirm_pick(clarify_kind, value)

        with cols[-1]:
            custom = st.number_input(
                "Custom",
                min_value=0,
                step=1,
                value=0,
                key=f"clarify_custom_{clarify_kind}",
            )
            if st.button("Use custom", key=f"clarify_custom_btn_{clarify_kind}"):
                _confirm_pick(clarify_kind, int(custom))

    prompt = st.chat_input("Describe a policy change…")
    if prompt:
        _queue_turn(prompt)


def _save_proposal(name: str) -> None:
    """Write the approved draft to ``policies/<name>.json`` and hide Save."""
    try:
        policy_agent.save_policy_draft(name, st.session_state[_SESSION_PROPOSAL_KEY])
    except (OSError, ValueError) as error:
        st.session_state[_SESSION_NOTICE_KEY] = ("error", f"Not saved: {error}")
    else:
        st.session_state[_SESSION_PROPOSAL_KEY] = None
        st.session_state[_SESSION_NAME_STEP_KEY] = False
        st.session_state[_SESSION_NOTICE_KEY] = ("success", f"Saved “{name}” to policies")
    st.rerun()


st.title("Policy Management")
picker_tab, builder_tab = st.tabs(["Policy Picker", "Policy Builder"])
with picker_tab:
    _render_policy_picker()
with builder_tab:
    _render_policy_builder()
