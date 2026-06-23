"""
analysis/__init__.py
---------------------
Vaulter AI Stage 3 — Claude Analysis Layer

Public API — import these in the dashboard or anywhere else:

    from analysis.analyzer import (
        answer_question,
        get_property_summary,
        get_risk_scan,
        get_market_summary,
        get_email_highlights,
        get_portfolio_overview,
    )
"""

from analysis.analyzer import (
    answer_question,
    get_property_summary,
    get_risk_scan,
    get_market_summary,
    get_email_highlights,
    get_portfolio_overview,
)

__all__ = [
    "answer_question",
    "get_property_summary",
    "get_risk_scan",
    "get_market_summary",
    "get_email_highlights",
    "get_portfolio_overview",
]
