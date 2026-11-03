"""Reviews page — placeholder queue plus the live-policy label.

Part 2 reviewer UI is intentionally stubbed here. This page only reads the
live pointer. A dedicated reviewer-queue brief must land before queue, case
context, decisions, or aftermath are built.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.policy.live_policy import read_live_policy

st.title("Reviews")
_live = read_live_policy()
if _live is None:
    st.write("No live policy. Every request goes to review.")
else:
    st.write(f"Live policy: {_live['file']}")
st.info(
    "Reviewer queue comes later. This page is an empty placeholder until a "
    "dedicated reviewer-queue task brief authorizes that work."
)
