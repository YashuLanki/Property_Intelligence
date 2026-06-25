"""
analysis/screener.py
--------------------
Vaulter AI — Adaptive Four-Stage Listing Screener

Stage 0 — Calibration  : Claude skims a sample of the export and generates
                         hard rules (Stage 1) and scoring dimensions (Stage 2)
                         specific to this file's structure and content.
                         No hardcoded rules — adapts to any CoStar export.

Stage 1 — Hard Rules   : Python applies Claude-generated rules instantly.
                         Any listing hitting 2+ rules is auto-eliminated.

Stage 2 — Scoring      : Python scores survivors on Claude-generated dimensions.
                         Top N advance to Stage 4.

Stage 3 — Safety Net   : Claude quickly checks rejects for wrongly-eliminated
                         listings (partial flood, hidden entitlement path,
                         portfolio adjacency, rising submarket signal).

Stage 4 — Deep Analysis: Claude fully analyzes finalists + any Stage 3 rescues.
                         Renders interactive React dashboard.

Stages 0–2 run in Python inside the MCP tool.
Stages 3–4 run in Claude Desktop after the tool returns.
"""

import json
import logging
import re

log = logging.getLogger("vaulter.screener")

CALIBRATION_SAMPLE_SIZE = 15   # rows sent to Claude for Stage 0
DEFAULT_TOP_N           = 30   # finalists forwarded to Stage 4


# ══════════════════════════════════════════════════════════════════
# Utilities — extract rows and parse price/acres
# ══════════════════════════════════════════════════════════════════

def extract_rows(chunks: list) -> list[str]:
    """
    Break CoStar chunks into individual listing rows.
    Each row is a pipe-separated string with 5+ separators.
    Deduplicates on the first 80 characters.
    """
    rows: list[str] = []
    seen: set[str]  = set()
    for chunk in chunks:
        text = chunk["text"] if isinstance(chunk, dict) else str(chunk)
        for line in text.splitlines():
            line = line.strip()
            if line.count("|") >= 5:
                key = line[:80].lower()
                if key not in seen:
                    seen.add(key)
                    rows.append(line)
    return rows


def _get_address(row: str) -> str:
    """Return the first readable text segment from a pipe-separated row."""
    for part in [p.strip() for p in row.split("|")][:5]:
        if (
            len(part) >= 8
            and not part.replace(".", "").replace(",", "").isdigit()
            and not (len(part) == 2 and part.isalpha())
            and not re.fullmatch(r"\d{5}", part)
        ):
            return part[:80]
    return "Unknown listing"


def _get_price_acres(row: str) -> tuple:
    """Extract price, acreage, and $/acre from a raw row."""
    price = None
    acres = None
    for pm in re.findall(r"\|\s*(\d{6,8})\s*\|", row):
        p = int(pm)
        if 100_000 <= p <= 50_000_000:
            price = p
            break
    for pa in re.findall(r"\|\s*(\d+\.?\d*)\s*\|", row):
        try:
            a = float(pa)
            if 1.0 <= a <= 800.0:
                acres = a
                break
        except ValueError:
            pass
    ppa = int(price / acres) if price and acres and acres > 0 else None
    return price, acres, ppa


# ══════════════════════════════════════════════════════════════════
# Stage 0 — Claude calibration
# ══════════════════════════════════════════════════════════════════

CALIBRATION_PROMPT = """You are setting up an automated screening pipeline for commercial real estate listings.

Here are {n} sample rows from a CoStar export (each row is pipe-separated fields):

{sample}

Your job: analyze this data and define screening criteria specific to THIS file.

STAGE 1 — HARD RULES (automatic dealbreakers, no AI involved):
Define 4-8 rules that instantly eliminate listings. Rules must be binary and
checkable from keywords in the raw row text. A listing triggering 2+ rules is cut.
Good rules catch: flood zones, zoning mismatches, outlying markets with no
infrastructure, residential brokers selling commercial land, missing utility data
in risky locations, unrealistic pricing for the use type.

STAGE 2 — SCORING DIMENSIONS (ranking survivors):
Define 3-6 dimensions to score surviving listings 0 to max_points each.
Dimensions must be measurable from keywords in this data.
Good dimensions measure: submarket growth tier, zoning match to use,
utility provider reliability, flood risk absence, pricing vs market norms.

Return ONLY valid JSON — no markdown fences, no explanation outside the JSON:
{{
  "data_fields_observed": ["list what fields you actually see in these rows"],
  "hard_rules": [
    {{
      "id": "snake_case_id",
      "description": "Plain English: what this catches and why it matters as a dealbreaker",
      "keywords": ["keyword1", "keyword2"],
      "match_type": "any_present | none_present | conflict",
      "conflict_keywords": ["only needed for conflict type"]
    }}
  ],
  "scoring_dimensions": [
    {{
      "id": "snake_case_id",
      "description": "Plain English: what this measures and why it matters",
      "max_points": 2,
      "high_score_keywords": ["keywords indicating a GOOD score"],
      "low_score_keywords": ["keywords indicating a BAD score"]
    }}
  ]
}}

match_type values:
  any_present  — flag if ANY keyword in 'keywords' appears in the listing text
  none_present — flag if NONE of the keywords appear (catches missing data like no utility)
                 if conflict_keywords provided, also requires one of those to be present
  conflict     — flag if ANY keyword from 'keywords' AND ANY from 'conflict_keywords' both appear

IMPORTANT: calibrate to THIS dataset. Use the actual submarket names, zoning codes,
utility providers, and use types you see in these rows. Do not use generic defaults."""


CALIBRATION_PROMPT = """You are setting up an automated screening pipeline for commercial real estate listings.

Here are {n} sample rows from a CoStar export (pipe-separated fields):

{sample}

Analyze this data and define screening criteria specific to THIS file.

Output your response using ONLY the line formats below — no JSON, no markdown, no extra explanation.
Use >>> as the separator between fields on each line.

FORMAT:
FIELDS: field1, field2, field3, ...
RULE: rule_id >>> plain English description of what this catches and why >>> match_type >>> keyword1, keyword2, keyword3 >>> conflict_keyword1, conflict_keyword2
DIM: dim_id >>> plain English description of what this measures >>> max_points >>> high_kw1, high_kw2 >>> low_kw1, low_kw2

FIELDS line: list the data fields you actually see in these rows (one line).

RULE lines (4-8 rules): automatic dealbreakers. A listing hitting 2+ rules is cut.
  match_type options:
    any_present  — flag if ANY keyword appears in the listing
    none_present — flag if NONE of the keywords appear (use conflict column to require a condition)
    conflict     — flag if ANY keyword from column 4 AND ANY from column 5 both appear
  Leave the last column empty if not needed.

DIM lines (3-6 dimensions): scoring criteria for survivors.
  max_points: integer (2 or 3)
  Column 4: keywords that indicate a HIGH (good) score on this dimension
  Column 5: keywords that indicate a LOW (bad) score on this dimension

IMPORTANT: use the actual submarket names, zoning codes, utility providers, and use types
you see in these rows. Do not use generic placeholders.

Example output (do not copy — generate from the actual data above):
FIELDS: flood zone, zoning, submarket, utility provider, proposed land use
RULE: flood_high >>> 100-year floodplain creates lender resistance and mitigation cost >>> any_present >>> 100-year floodplain, sfha, high risk areas >>>
RULE: zoning_mismatch >>> Residential zoning marketed as commercial with no entitlement path >>> conflict >>> r-43, r-1, r-2 >>> commercial, retail, office, industrial
DIM: submarket_tier >>> Growth tier of the submarket >>> 3 >>> loop 303, west i-10, east valley >>> outlying, far west, gila bend"""


def _parse_calibration_response(response: str) -> dict:
    """
    Parse the line-based calibration response from Claude.
    Format uses >>> as separator — no JSON, no escaping issues.
    """
    fields: list[str] = []
    rules:  list[dict] = []
    dims:   list[dict] = []

    valid_match_types = {"any_present", "none_present", "conflict"}

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # ── FIELDS line ───────────────────────────────────────────
        if line.upper().startswith("FIELDS:"):
            fields = [f.strip() for f in line[7:].split(",") if f.strip()]

        # ── RULE line ─────────────────────────────────────────────
        elif line.upper().startswith("RULE:"):
            parts = [p.strip() for p in line[5:].split(">>>")]
            if len(parts) < 4:
                continue   # skip malformed lines
            rule_id     = re.sub(r"\W+", "_", parts[0].lower()).strip("_") or f"rule_{len(rules)}"
            description = parts[1] if len(parts) > 1 else rule_id
            match_type  = parts[2].strip().lower() if len(parts) > 2 else "any_present"
            if match_type not in valid_match_types:
                match_type = "any_present"
            keywords         = [k.strip().lower() for k in parts[3].split(",") if k.strip()]
            conflict_keywords= [k.strip().lower() for k in parts[4].split(",") if k.strip()] \
                               if len(parts) > 4 else []
            if keywords:   # only add if there are actual keywords
                rules.append({
                    "id":                rule_id,
                    "description":       description,
                    "match_type":        match_type,
                    "keywords":          keywords,
                    "conflict_keywords": conflict_keywords,
                })

        # ── DIM line ──────────────────────────────────────────────
        elif line.upper().startswith("DIM:"):
            parts = [p.strip() for p in line[4:].split(">>>")]
            if len(parts) < 3:
                continue   # skip malformed lines
            dim_id      = re.sub(r"\W+", "_", parts[0].lower()).strip("_") or f"dim_{len(dims)}"
            description = parts[1] if len(parts) > 1 else dim_id
            try:
                max_pts = int(parts[2])
                max_pts = max(1, min(max_pts, 5))   # clamp to 1-5
            except (ValueError, IndexError):
                max_pts = 2
            high_kw = [k.strip().lower() for k in parts[3].split(",") if k.strip()] \
                      if len(parts) > 3 else []
            low_kw  = [k.strip().lower() for k in parts[4].split(",") if k.strip()] \
                      if len(parts) > 4 else []
            dims.append({
                "id":                  dim_id,
                "description":         description,
                "max_points":          max_pts,
                "high_score_keywords": high_kw,
                "low_score_keywords":  low_kw,
            })

    return {
        "data_fields_observed": fields,
        "hard_rules":           rules,
        "scoring_dimensions":   dims,
    }


def _clean_row_for_prompt(row: str) -> str:
    """
    Sanitise a sample row before embedding in the calibration prompt.
    Remove characters that could confuse the parser or balloon the prompt.
    """
    return (
        row.replace(">>>", "---")   # avoid collision with our separator
           .replace("\n", " ")
           .replace("\r", " ")
    )[:350]


def calibrate_pipeline(sample_rows: list[str], api_key: str) -> dict:
    """
    Stage 0: send a sample of rows to Claude and ask it to generate
    hard rules (Stage 1) and scoring dimensions (Stage 2).

    Uses a simple line-based format (not JSON) so there are no
    escaping or structural issues regardless of data content.

    Falls back to sensible Phoenix-area defaults if the API call fails
    or returns no usable rules.
    """
    import anthropic

    sample_text = "\n".join(
        f"Row {i+1}: {_clean_row_for_prompt(row)}"
        for i, row in enumerate(sample_rows[:CALIBRATION_SAMPLE_SIZE])
    )
    prompt = CALIBRATION_PROMPT.format(
        n=min(len(sample_rows), CALIBRATION_SAMPLE_SIZE),
        sample=sample_text,
    )

    try:
        client  = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw          = message.content[0].text.strip()
        calibration  = _parse_calibration_response(raw)

        # If parsing produced nothing useful, fall back
        if not calibration["hard_rules"] and not calibration["scoring_dimensions"]:
            log.warning("[SCREENER] Stage 0 response parsed but yielded no rules — using fallback")
            return _fallback_calibration()

        log.info(
            f"[SCREENER] Stage 0 complete: "
            f"{len(calibration['hard_rules'])} rules, "
            f"{len(calibration['scoring_dimensions'])} dimensions"
        )
        return calibration

    except Exception as e:
        log.warning(f"[SCREENER] Stage 0 failed ({e}) — using fallback defaults")
        return _fallback_calibration()


def _fallback_calibration() -> dict:
    """
    Fallback if Stage 0 API call fails.
    Phoenix-area defaults — better than nothing.
    """
    return {
        "data_fields_observed": ["flood_zone", "zoning", "use_type", "utility", "submarket"],
        "hard_rules": [
            {
                "id": "flood_high",
                "description": "100-year floodplain / SFHA — expensive mitigation, lender resistance",
                "keywords": ["100-year floodplain", "sfha", "high risk areas", "1% annual chance"],
                "match_type": "any_present",
                "conflict_keywords": [],
            },
            {
                "id": "zoning_use_mismatch",
                "description": "Residential zoning marketed as commercial — no entitlement path",
                "keywords": ["r-43", "r-1", "r-2", "r-3", "single family residential"],
                "match_type": "conflict",
                "conflict_keywords": ["commercial", "retail", "office", "industrial"],
            },
            {
                "id": "agricultural_outlying",
                "description": "Agricultural use in outlying market — no development signal",
                "keywords": ["agricultural"],
                "match_type": "conflict",
                "conflict_keywords": ["outlying", "gila bend", "tonopah", "wittmann"],
            },
            {
                "id": "no_utility_outlying",
                "description": "No major utility in outlying market — extension cost risk",
                "keywords": ["arizona public service", "aps", "salt river project", "srp"],
                "match_type": "none_present",
                "conflict_keywords": ["outlying", "gila bend", "tonopah", "wittmann"],
            },
            {
                "id": "residential_broker_commercial",
                "description": "Residential brokerage selling commercial land",
                "keywords": ["keller williams", "kw realty", "remax", "coldwell banker"],
                "match_type": "conflict",
                "conflict_keywords": ["commercial", "industrial", "retail", "office"],
            },
        ],
        "scoring_dimensions": [
            {
                "id": "submarket_tier",
                "description": "Submarket growth tier — high-growth corridors score highest",
                "max_points": 3,
                "high_score_keywords": ["loop 303", "west i-10", "east valley", "surprise",
                                        "peoria", "queen creek", "gilbert", "goodyear", "buckeye"],
                "low_score_keywords": ["outlying", "gila bend", "tonopah", "southwest outlying"],
            },
            {
                "id": "zoning_match",
                "description": "Zoning match to marketed use — PAD and exact matches score highest",
                "max_points": 3,
                "high_score_keywords": ["pad", "planned area development", "g-i",
                                        "general industrial", "c-2", "c-3"],
                "low_score_keywords": ["r-43", "r-1", "r-2", "agricultural", "a-1"],
            },
            {
                "id": "utility_provider",
                "description": "Utility provider — APS/SRP = industrial-capable power",
                "max_points": 2,
                "high_score_keywords": ["arizona public service", "aps", "salt river project", "srp"],
                "low_score_keywords": [],
            },
            {
                "id": "flood_risk",
                "description": "Flood risk absence — clean parcels score higher",
                "max_points": 2,
                "high_score_keywords": ["minimal flood hazard", "500-year floodplain",
                                        "moderate to low risk"],
                "low_score_keywords": ["100-year floodplain", "high risk areas", "sfha"],
            },
        ],
    }


# ══════════════════════════════════════════════════════════════════
# Stage 1 — Apply Claude-generated hard rules
# ══════════════════════════════════════════════════════════════════

def _matches_rule(text_lower: str, rule: dict) -> bool:
    """Check whether a listing row triggers one hard rule."""
    keywords    = [k.lower() for k in rule.get("keywords", [])]
    conflict_kw = [k.lower() for k in rule.get("conflict_keywords", [])]
    match_type  = rule.get("match_type", "any_present")

    if match_type == "any_present":
        return any(k in text_lower for k in keywords)

    elif match_type == "none_present":
        absent = not any(k in text_lower for k in keywords)
        if conflict_kw:
            return absent and any(k in text_lower for k in conflict_kw)
        return absent

    elif match_type == "conflict":
        primary_hit  = any(k in text_lower for k in keywords)
        conflict_hit = any(k in text_lower for k in conflict_kw)
        return primary_hit and conflict_hit

    return False


def stage1_hard_rules(
    listing_text: str, rules: list[dict]
) -> tuple[bool, list[str]]:
    """
    Apply all hard rules to one listing row.
    Returns (eliminated, [triggered rule descriptions]).
    Eliminated = True when 2+ rules trigger.
    """
    triggered: list[str] = []
    t = listing_text.lower()
    for rule in rules:
        if _matches_rule(t, rule):
            # .get() guards against a missing description key
            triggered.append(rule.get("description", rule.get("id", "Unknown rule")))
    return len(triggered) >= 2, triggered


# ══════════════════════════════════════════════════════════════════
# Stage 2 — Score with Claude-generated dimensions
# ══════════════════════════════════════════════════════════════════

def stage2_score(
    listing_text: str, dimensions: list[dict]
) -> tuple[int, int, dict]:
    """
    Score one listing across all Claude-generated dimensions.
    Returns (total_score, max_possible_score, breakdown_dict).
    """
    t         = listing_text.lower()
    breakdown: dict[str, int] = {}
    total     = 0
    max_total = 0

    for i, dim in enumerate(dimensions):
        max_pts = dim.get("max_points", 2)
        high_kw = [k.lower() for k in dim.get("high_score_keywords", [])]
        low_kw  = [k.lower() for k in dim.get("low_score_keywords", [])]
        dim_id  = dim.get("id", f"dim_{i}")   # safe fallback if id missing
        max_total += max_pts

        has_high = any(k in t for k in high_kw)
        has_low  = any(k in t for k in low_kw)

        if has_high and not has_low:
            pts = max_pts
        elif has_high and has_low:
            pts = max(1, max_pts // 2)   # mixed signals
        elif has_low:
            pts = 0
        else:
            pts = max(1, max_pts // 2)   # unknown — neutral

        breakdown[dim_id] = pts
        total += pts

    return total, max_total, breakdown


# ══════════════════════════════════════════════════════════════════
# Orchestrate Stages 0 → 1 → 2
# ══════════════════════════════════════════════════════════════════

def run_pipeline(
    costar_chunks: list,
    api_key: str,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """
    Run Stages 0, 1, and 2 on a set of CoStar chunks.

    Stage 0: One Claude API call — generates rules + dimensions from a sample.
    Stage 1: Python hard-rule elimination using those rules.
    Stage 2: Python scoring + ranking using those dimensions.

    Returns structured result dict for format_output().
    """
    rows = extract_rows(costar_chunks)
    log.info(f"[SCREENER] {len(rows)} rows extracted from {len(costar_chunks)} chunks")

    if not rows:
        return {
            "total": 0, "calibration": {}, "hard_rules": [], "scoring_dimensions": [],
            "max_score": 10, "finalists": [], "stage1_rejects": [], "stage2_rejects": [],
            "stage1_eliminated": 0, "stage2_eliminated": 0,
            "error": "No listing rows found. Run check_inbox_now to ingest the email first.",
        }

    # ── Stage 0 ───────────────────────────────────────────────────
    log.info(f"[SCREENER] Stage 0: calibrating on {min(len(rows), CALIBRATION_SAMPLE_SIZE)} rows")
    calibration = calibrate_pipeline(rows, api_key)
    hard_rules  = calibration.get("hard_rules") or []
    score_dims  = calibration.get("scoring_dimensions") or []
    max_score   = sum(d.get("max_points", 2) for d in score_dims) or 10

    # ── Stage 1 ───────────────────────────────────────────────────
    survivors:      list[dict] = []
    stage1_rejects: list[dict] = []

    for row in rows:
        eliminated, flags = stage1_hard_rules(row, hard_rules)
        price, acres, ppa = _get_price_acres(row)
        record = {
            "address": _get_address(row),
            "raw":     row,
            "price":   price,
            "acres":   acres,
            "ppa":     ppa,
            "flags":   flags,
        }
        if eliminated:
            record["stage"]              = "stage1_reject"
            record["elimination_reason"] = "; ".join(flags)
            stage1_rejects.append(record)
        else:
            if flags:
                record["single_flag"] = flags[0]   # one flag — note but don't eliminate
            survivors.append(record)

    log.info(f"[SCREENER] Stage 1: {len(stage1_rejects)} out, {len(survivors)} survive")

    # ── Stage 2 ───────────────────────────────────────────────────
    for record in survivors:
        total, _, breakdown = stage2_score(record["raw"], score_dims)
        record["score"]           = total
        record["max_score"]       = max_score
        record["score_breakdown"] = breakdown

    survivors.sort(key=lambda x: x["score"], reverse=True)
    finalists      = survivors[:top_n]
    stage2_rejects = survivors[top_n:]

    for r in finalists:      r["stage"] = "finalist"
    for r in stage2_rejects:
        r["stage"]              = "stage2_reject"
        r["elimination_reason"] = f"Score {r['score']}/{max_score} — below threshold"

    log.info(f"[SCREENER] Stage 2: {len(finalists)} finalists, {len(stage2_rejects)} below threshold")

    return {
        "total":              len(rows),
        "calibration":        calibration,
        "hard_rules":         hard_rules,
        "scoring_dimensions": score_dims,
        "max_score":          max_score,
        "finalists":          finalists,
        "stage1_rejects":     stage1_rejects,
        "stage2_rejects":     stage2_rejects,
        "stage1_eliminated":  len(stage1_rejects),
        "stage2_eliminated":  len(stage2_rejects),
        "error":              None,
    }


# ══════════════════════════════════════════════════════════════════
# Format output for Claude — Stages 3, 4, and dashboard
# ══════════════════════════════════════════════════════════════════

def format_output(
    result: dict,
    portfolio: list,
    web_intel: str,
    email_intel: str,
) -> str:
    """
    Assemble pipeline results + enrichment into a structured string
    for Claude to run Stages 3 & 4 and render the dashboard.
    """
    SEP  = "═" * 62
    out  = []
    ms   = result.get("max_score", 10)

    # ── Pipeline summary ──────────────────────────────────────────
    out.append(SEP)
    out.append("  PIPELINE SUMMARY")
    out.append(SEP)
    out.append(f"  Total listings       : {result['total']}")
    out.append(f"  Stage 0              : Calibration complete — rules and dimensions generated")
    out.append(f"  Stage 1 eliminated   : {result['stage1_eliminated']}  (hard rules, Python, no AI)")
    out.append(f"  Stage 2 cut          : {result['stage2_eliminated']}  (below score threshold, Python)")
    out.append(f"  Finalists            : {len(result['finalists'])}  (ready for Stage 4)")
    if result.get("error"):
        out.append(f"  ⚠  {result['error']}")
    out.append("")

    # ── Stage 0 calibration summary ───────────────────────────────
    cal   = result.get("calibration", {})
    rules = result.get("hard_rules", [])
    dims  = result.get("scoring_dimensions", [])

    out.append(SEP)
    out.append("  STAGE 0 — WHAT CLAUDE FOUND IN THIS FILE")
    out.append(SEP)
    if cal.get("data_fields_observed"):
        out.append(f"  Fields present: {', '.join(cal['data_fields_observed'])}\n")
    out.append(f"  Hard rules generated ({len(rules)}):")
    for i, r in enumerate(rules, 1):
        kw_preview = str(r.get("keywords", [])[:3])[1:-1]
        out.append(f"    {i}. {r.get('description', r.get('id', 'Rule ' + str(i)))}")
        out.append(f"       type={r.get('match_type','any_present')}  keywords=[{kw_preview}...]")
    out.append("")
    out.append(f"  Scoring dimensions generated ({len(dims)}):")
    for i, d in enumerate(dims, 1):
        out.append(f"    {i}. {d.get('description', d.get('id', 'Dimension ' + str(i)))}  (0–{d.get('max_points', 2)} pts)")
    out.append("")

    # ── Finalists ─────────────────────────────────────────────────
    out.append(SEP)
    out.append(f"  FINALISTS — {len(result['finalists'])} listings for Stage 4 deep analysis")
    out.append(SEP)
    for i, lst in enumerate(result["finalists"], 1):
        out.append(f"[F{i}]  {lst['address']}")
        if lst["price"]:
            out.append(f"  Price   : ${lst['price']:,}" +
                       (f"  |  ${lst['ppa']:,}/ac" if lst["ppa"] else ""))
        if lst["acres"]:
            out.append(f"  Acres   : {lst['acres']}")
        out.append(f"  Score   : {lst['score']}/{ms}")
        bd = lst.get("score_breakdown", {})
        if bd:
            out.append(f"  Detail  : {' | '.join(f'{k}: {v}' for k, v in bd.items())}")
        if lst.get("single_flag"):
            out.append(f"  Note    : {lst['single_flag']}")
        out.append(f"  Raw     : {lst['raw'][:350]}...")
        out.append("")

    # ── Stage 1 rejects ───────────────────────────────────────────
    out.append(SEP)
    out.append(f"  STAGE 1 REJECTS — {len(result['stage1_rejects'])} listings  (Stage 3 safety net review)")
    out.append(SEP)
    for i, lst in enumerate(result["stage1_rejects"], 1):
        out.append(f"[R1-{i}]  {lst['address']}")
        out.append(f"  Flags : {lst['elimination_reason']}")
        if lst["price"]: out.append(f"  Price : ${lst['price']:,}")
        if lst["acres"]: out.append(f"  Acres : {lst['acres']}")
        out.append(f"  Raw   : {lst['raw'][:200]}...")
        out.append("")

    # ── Stage 2 rejects ───────────────────────────────────────────
    cap = 25
    out.append(SEP)
    out.append(f"  STAGE 2 REJECTS — {len(result['stage2_rejects'])} listings  (Stage 3 safety net review)")
    out.append(SEP)
    for i, lst in enumerate(result["stage2_rejects"][:cap], 1):
        out.append(f"[R2-{i}]  {lst['address']}  — Score {lst['score']}/{ms}")
        if lst["price"]: out.append(f"  Price : ${lst['price']:,}")
        if lst["acres"]: out.append(f"  Acres : {lst['acres']}")
        out.append("")
    if len(result["stage2_rejects"]) > cap:
        out.append(f"  ... {len(result['stage2_rejects']) - cap} more (all visible on dashboard)\n")

    # ── Portfolio ─────────────────────────────────────────────────
    out.append(SEP)
    out.append(f"  VAULTER PORTFOLIO ({len(portfolio)} active properties)")
    out.append(SEP)
    by_state: dict = {}
    for p in portfolio:
        by_state.setdefault(p.get("state", "Unknown"), []).append(p)
    for state in sorted(by_state):
        out.append(f"  {state}:")
        for p in by_state[state]:
            out.append(f"    {p['name']:<32} | {p.get('category',''):<18} | {p.get('city','')}")
    out.append("")

    # ── Market intelligence ───────────────────────────────────────
    out.append(SEP)
    out.append("  MARKET INTELLIGENCE")
    out.append(SEP)
    out.append((web_intel or "No market data — run 'python main.py scrape' to populate.")[:3500])
    out.append("")

    # ── Email signals ─────────────────────────────────────────────
    if email_intel:
        out.append(SEP)
        out.append("  BROKER EMAIL SIGNALS")
        out.append(SEP)
        out.append(email_intel[:2000])
        out.append("")

    # ── Instructions for Claude ───────────────────────────────────
    out.append(SEP)
    out.append("  INSTRUCTIONS — STAGES 3 & 4")
    out.append(SEP)
    out.append(f"""
STAGE 3 — SAFETY NET  (quick pass, do this first)
──────────────────────────────────────────────────
Scan all Stage 1 and Stage 2 rejects above.
For each one ask: was it wrongly eliminated by the automated rules?

Look specifically for:
  • Flood risk only on a small corner — the main buildable area is clean
  • Zoning mismatch flagged but there is a clear rezoning precedent nearby
    or the jurisdiction has a track record of approvals for this conversion
  • Low score but the listing sits adjacent to an existing Vaulter property
    (check the portfolio above — same city or submarket = flag it)
  • Low score on pricing but market intelligence above shows this submarket
    trending up — worth a closer look before discarding

Mark any rescue as RESCUED with a one-line reason.
If nothing stands out, note "no rescues" and move on.


STAGE 4 — DEEP ANALYSIS  (all finalists + any rescued listings)
────────────────────────────────────────────────────────────────
For each listing use the portfolio, market intel, and email signals above.
Produce:

  LISTING: [address]
  SCORE: [n/{ms}]
  QUICK FILTER:
    ✓/✗  Price/acre rational vs submarket comps
    ✓/✗  Flood risk manageable (not unfixable)
    ✓/✗  Viable zoning path for intended use
    ✓/✗  Infrastructure buildable at reasonable cost
  SIGNALS:
    • [only signals supported by the data above]
    • [note any Vaulter portfolio adjacency or submarket overlap]
  VERDICT: Pursue | Scrutinize | Pass
  ONE-LINE TAKE: [direct recommendation]


DASHBOARD OUTPUT  (render as React artifact after Stages 3 & 4)
────────────────────────────────────────────────────────────────
Requirements:
  • Header: counts by verdict  (Pursue N  |  Scrutinize N  |  Pass N  |  Rescued N)
  • Filter tabs: All | Pursue | Scrutinize | Pass | Rescued
  • Compact card per listing — visible all at once, minimal scrolling
  • Card shows: address, verdict badge, score/{ms}, one or two key signal tags
  • Click to expand: signals, quick filter checklist ✓/✗, one-line take
  • Sort controls: by verdict · by score · by price per acre
  • ALL listings including Stage 1/2 rejects visible in Pass tab
    with "Auto-eliminated" badge and the specific rules that triggered
  • Calibration card: show what Stage 0 generated — fields observed,
    rules used, dimensions used — so the team can audit the pipeline
  • Dark navy / teal palette (Vaulter branding)
""")

    return "\n".join(out)
