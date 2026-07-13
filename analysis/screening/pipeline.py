"""
analysis/screening/pipeline.py
---------------------------------
Single entry point for the full 4-phase CoStar listing screening pipeline.
Callable directly from an MCP tool handler (see mcp_server.py's
screen_listings tool) -- no subprocess calls, no re-running of upstream
phases, no CLI orchestration.
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config as screening_config
from . import phase1_rules
from . import phase2_ranking
from . import phase3_deep_analysis
from . import phase4_verification
from . import market_utils
from . import workbook_builder


def _update_manifest(output_dir: Path, market: str, market_slug: str, timestamp: str, workbook_filename: str) -> Path:
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            manifest = {"markets": []}
    else:
        manifest = {"markets": []}

    manifest.setdefault("markets", [])
    manifest["markets"] = [m for m in manifest["markets"] if m.get("market_slug") != market_slug]
    manifest["markets"].append({
        "market": market,
        "market_slug": market_slug,
        "timestamp": timestamp,
        "workbook": workbook_filename,
    })
    manifest["markets"].sort(key=lambda m: m["timestamp"], reverse=True)

    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest_path


def run_full_screening(
    source_path: Path,
    anthropic_api_key: str,
    google_api_key: str | None,
    top_n: int = 10,
) -> dict:
    """
    Runs the full 4-phase screening pipeline against source_path (a CoStar
    export or broker spreadsheet) and returns a summary dict suitable for
    turning into a text reply for Claude.

    Steps:
      1. Read source_path with pd.read_excel
      2. Phase 1 -- phase1_rules.run_screener
      3. Phase 2 -- phase2_ranking.rank_listings
      4. Detect market via market_utils
      5. Phase 3 -- phase3_deep_analysis.get_top_listings + run_deep_analysis
      6. Phase 4 -- phase4_verification.run_verification
      7. Build the combined workbook via workbook_builder.build_combined_workbook
      8. Update manifest.json
      9. Return a summary dict
    """
    df = pd.read_excel(source_path)
    total_screened = len(df)

    screened_df = phase1_rules.run_screener(df)
    phase1_survivors = int((screened_df["Screening_Status"] != "ELIMINATED").sum())

    ranked_df = phase2_ranking.rank_listings(screened_df)

    market = market_utils.detect_market(df)
    market_slug = market_utils.slugify(market)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    top_listings = phase3_deep_analysis.get_top_listings(ranked_df, top_n)
    top10_addresses = top_listings["Property Address"].tolist() if "Property Address" in top_listings.columns else []

    deep_analyses = phase3_deep_analysis.run_deep_analysis(ranked_df, anthropic_api_key, top_n=top_n)

    final_result = phase4_verification.run_verification(
        ranked_df, deep_analyses, anthropic_api_key, google_api_key, top_n=top_n,
    )
    finalist_addresses = final_result.get("finalists", [])

    finalist_tiers = {}
    for addr in finalist_addresses:
        if addr in deep_analyses:
            finalist_tiers[addr] = phase4_verification.classify_tier(deep_analyses[addr].get("RECOMMENDATION", ""))

    output_dir = screening_config.SCREENING_OUTPUT_DIR
    workbook_filename = f"{screening_config.COMBINED_WORKBOOK_PREFIX}_{market_slug}_{timestamp}.xlsx"
    workbook_path = output_dir / workbook_filename

    workbook_builder.build_combined_workbook(
        screened_df, ranked_df, deep_analyses, final_result, workbook_path,
    )

    _update_manifest(output_dir, market, market_slug, timestamp, workbook_filename)

    top_candidates = []
    for _, row in top_listings.head(5).iterrows():
        addr = row.get("Property Address", "Unknown")
        recommendation = deep_analyses.get(addr, {}).get("RECOMMENDATION", "")
        snippet = recommendation.splitlines()[0] if recommendation else ""
        top_candidates.append({
            "address": addr,
            "composite_score": row.get("Composite_Score"),
            "recommendation_snippet": snippet,
        })

    return {
        "market": market,
        "workbook_path": str(workbook_path.resolve()),
        "total_screened": total_screened,
        "phase1_survivors": phase1_survivors,
        "top10_addresses": top10_addresses,
        "finalist_addresses": finalist_addresses,
        "finalist_tiers": finalist_tiers,
        "top_candidates": top_candidates,
    }
