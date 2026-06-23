"""
pipeline/property_matcher.py
-----------------------------
Vaulter AI Stage 2 — Property Matcher

Matches any piece of text (web scrape, email, document) to relevant
Vaulter AI properties from the Project Master.

Matching logic (in priority order):
  1. Property name mention  — "Magic Ranch" found in text → Magic Ranch 10, 80, 50
  2. City mention           — "Florence" found → all Florence properties
  3. State + category match — Arizona article about subdivision → all AZ Pre-Plat/Final Eng
  4. State match only       — Arizona article → all Arizona properties

Used by:
  pipeline/web_scraper.py   — tags scraped articles to relevant properties
  pipeline/email_reader.py  — tags broker emails to relevant properties
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Category relevance map ─────────────────────────────────────────
# Keywords in text that suggest relevance to a category
CATEGORY_SIGNALS = {
    "Acquisition":       ["acquisition", "acquire", "purchase", "buying land", "land deal",
                          "under contract", "due diligence"],
    "Pre-Plat":          ["pre-plat", "preplat", "zoning", "entitlement", "rezoning",
                          "general plan", "land use", "subdivision application"],
    "Final Engineering": ["final engineering", "subdivision", "grading", "infrastructure",
                          "permits", "plat approval", "site plan", "civil engineering"],
    "Disposition":       ["disposition", "for sale", "land sale", "listing", "sold",
                          "sale price", "cap rate", "buyer", "closing"],
    "Site Maintenance":  ["site maintenance", "holding cost", "carrying cost", "property tax",
                          "caretaker", "weed control"],
    "Rezone":            ["rezone", "rezoning", "zone change", "land use amendment",
                          "general plan amendment", "entitlement"],
    "Development":       ["development", "homebuilder", "new homes", "construction",
                          "building permits", "starts", "deliveries"],
}


def load_properties() -> list[dict]:
    """Load properties from the Project Master (same logic as property_scraper)."""
    try:
        from pipeline.property_scraper import load_properties as _load
        props, _ = _load()
        return props
    except Exception:
        # Minimal fallback if property_scraper isn't available
        return []


def match_properties(text: str, properties: list[dict] | None = None) -> list[dict]:
    """
    Match a piece of text to relevant properties.
    Returns a list of matching property dicts, each with a 'match_reason' key.

    Match tiers (all matching tiers are included, not just the best):
      - name_match    : property name explicitly mentioned in text
      - city_match    : property city mentioned in text
      - category_match: text contains signals for the property's category + same state
      - state_match   : text mentions the property's state
    """
    if properties is None:
        properties = load_properties()

    text_lower = text.lower()
    matches    = {}   # name → {prop + match_reasons}

    for prop in properties:
        name     = prop["name"]
        city     = prop["city"]
        state    = prop["state"]
        category = prop["category"]
        reasons  = []

        # Tier 1 — property name mentioned
        # Check key parts of the name (handles "Magic Ranch" matching "Magic Ranch 10")
        name_parts = [p for p in name.lower().split() if len(p) > 3]
        name_phrase = name.lower()
        if name_phrase in text_lower:
            reasons.append("name_match")
        elif len(name_parts) >= 2:
            # Check if at least 2 significant words of the name appear together
            if all(p in text_lower for p in name_parts[:2]):
                reasons.append("name_match")

        # Tier 2 — city mentioned (skip if city is just the state name)
        if city.lower() != state.lower() and len(city) > 3:
            if city.lower() in text_lower:
                reasons.append("city_match")

        # Tier 3 — category signals + state match
        if state.lower() in text_lower:
            signals = CATEGORY_SIGNALS.get(category, [])
            if any(sig in text_lower for sig in signals):
                reasons.append("category_match")
            else:
                reasons.append("state_match")

        if reasons:
            matches[name] = {**prop, "match_reasons": reasons}

    # Sort: name matches first, then city, then category, then state
    priority = {"name_match": 0, "city_match": 1, "category_match": 2, "state_match": 3}
    result = list(matches.values())
    result.sort(key=lambda p: min(priority.get(r, 9) for r in p["match_reasons"]))

    return result


def format_matched_properties(matches: list[dict]) -> str:
    """Format matched properties for logging."""
    if not matches:
        return "no properties matched"
    parts = []
    for m in matches[:5]:   # log up to 5
        best = min(m["match_reasons"], key=lambda r: {"name_match":0,"city_match":1,"category_match":2,"state_match":3}.get(r,9))
        parts.append(f"{m['name']} ({best})")
    suffix = f" +{len(matches)-5} more" if len(matches) > 5 else ""
    return ", ".join(parts) + suffix


def matched_property_tags(matches: list[dict]) -> dict:
    """
    Return ChromaDB metadata tags summarizing the matched properties.
    Stored on every chunk so Stage 3 can filter by property.
    """
    if not matches:
        return {
            "matched_properties": "",
            "matched_states":     "",
            "matched_categories": "",
            "match_count":        0,
        }

    names      = [m["name"]     for m in matches]
    states     = list({m["state"]    for m in matches})
    categories = list({m["category"] for m in matches})

    return {
        "matched_properties": "|".join(names),        # pipe-separated for ChromaDB
        "matched_states":     "|".join(states),
        "matched_categories": "|".join(categories),
        "match_count":        len(matches),
    }

def clean_filename_for_matching(filename: str) -> str:
    """
    Convert a filename into readable text for property matching.
    Handles underscores, dashes, camelCase, and common separators.
    Strips extensions, leading timestamps, and document reference numbers.

    Examples:
      251125_Seller_2016_Commit_Tr_12-B_13___15_MDS.pdf
        → "Seller Commit Tr MDS"
      Mesa_del_Sol_-_DD_Memo_-_V2.docx
        → "Mesa del Sol DD Memo"
      Forney_-_DD_Report_-_V2___PN_-_20260526.docx
        → "Forney DD Report PN"
    """
    import re
    name = Path(filename).stem

    # Remove leading timestamp patterns like "20260622_130601_"
    name = re.sub(r'^\d{8}_\d{6}_', '', name)
    name = re.sub(r'^email_\d{8}_\d{6}_', '', name)

    # Replace separators with spaces
    name = name.replace('_', ' ').replace('-', ' ')

    # Remove version tags like V2, PN, V2+PN
    name = re.sub(r'\b(V\d+|PN|Rev\d*|Final|Draft)\b', '', name, flags=re.IGNORECASE)

    # Remove standalone years and date strings
    name = re.sub(r'\b(19|20)\d{2}\b', '', name)
    name = re.sub(r'\b\d{8}\b', '', name)  # yyyymmdd

    # Remove common noise words that aren't property identifiers
    noise = ['Seller', 'Commit', 'Survey', 'Report', 'Memo', 'Analysis',
             'Market', 'DD', 'ALTA', 'Phase', 'ESA', 'GeoTech', 'Soils',
             'Map', 'Land', 'Cover', 'Data', 'Preliminary', 'Assessment',
             'Site', 'Acres', 'Tr', 'Plat', 'Doc', 'Document', 'Reviewed']
    for word in noise:
        name = re.sub(rf'\b{re.escape(word)}\b', ' ', name, flags=re.IGNORECASE)

    # Remove leading document reference numbers like "110408" or "251125"
    name = re.sub(r'^\d{5,}\s*', '', name).strip()

    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def match_from_filename(filename: str, properties: list[dict] | None = None) -> list[dict]:
    """
    Match a filename to relevant properties.
    Cleans the filename first, then runs standard property matching.
    Falls back to raw filename if cleaned version yields no matches.
    """
    if properties is None:
        properties = load_properties()

    cleaned = clean_filename_for_matching(filename)

    # Try cleaned filename first
    matches = match_properties(cleaned, properties)
    if matches:
        return matches

    # Fallback: try the raw stem (without extension) in case cleaning removed too much
    stem = Path(filename).stem.replace('_', ' ').replace('-', ' ')
    return match_properties(stem, properties)
