"""Refund System — Streamlit multipage entry.

Run from the repository root:

    streamlit run app/main.py

Or with the project venv:

    .\\.venv\\Scripts\\python.exe -m streamlit run app/main.py

This entry wires navigation only. It does not evaluate refunds, call
``build_policy``, activate policy, or contact a model provider.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Refund System",
    layout="wide",
)

reviews_page = st.Page(
    "pages/reviews.py",
    title="Reviews",
    default=True,
)
policy_page = st.Page(
    "pages/policy_management.py",
    title="Policy Management",
)

navigation = st.navigation([reviews_page, policy_page])
navigation.run()
