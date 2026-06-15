"""
AI Routes — tool-based strategy generation using explicit reasoning steps.

Tool chain:
  analyze_customer_base()
      → select_target_segment()
      → choose_best_channel()
      → generate_message_variants()
      → forecast_revenue()
      → generate_final_strategy()   ← orchestrates all tools
"""
import json
import logging
import os
import re
from flask import Blueprint, request, jsonify
from sqlalchemy import func
from models import db, Customer, Campaign, Communication, Order, Attribution

logger = logging.getLogger(__name__)
ai_bp = Blueprint("ai", __name__)


# ─────────────────────────────────────────────────────────────
# Gemini helpers
# ─────────────────────────────────────────────────────────────

def get_gemini_client():
        from google import genai
        from config import get_gemini_api_key

        key = get_gemini_api_key()
        if not key:
            # Return None so callers can handle absence of a key gracefully.
            logger.warning("get_gemini_client: GEMINI_API_KEY missing — skipping Gemini client creation")
            return None
        return genai.Client(api_key=key)


def clean_json_response(raw: str) -> str:
    if raw is None:
        return ""
    cleaned = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
    cleaned = re.sub(r"```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```", "", cleaned)
    cleaned = cleaned.strip().strip("\ufeff")
    cleaned = cleaned.replace("“", '"').replace("”", '"')
    cleaned = cleaned.replace("‘", "'").replace("’", "'")

    # Extract the first JSON object or array if the model returned extra text
    obj_start = cleaned.find("{")
    arr_start = cleaned.find("[")
    if obj_start == -1 and arr_start == -1:
        return cleaned

    if obj_start == -1 or (arr_start != -1 and arr_start < obj_start):
        start, open_char, close_char = arr_start, "[", "]"
    else:
        start, open_char, close_char = obj_start, "{", "}"

    end = cleaned.rfind(close_char)
    if end != -1 and start < end:
        cleaned = cleaned[start:end + 1]

    return cleaned.strip()


def _gemini_json(prompt: str, max_tokens: int = 4096) -> dict | list:
    from google.genai import types
    client = get_gemini_client()
    if client is None:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    raw = response.text or ""
    cleaned = clean_json_response(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Gemini JSON parse failed: %s", e)
        logger.error("Raw Gemini response: %s", raw)
        logger.error("Cleaned Gemini response: %s", cleaned)
        raise


def generate_content_with_fallback(prompt: str, max_tokens: int = 4096, models=None) -> str:
    """Try a list of Gemini models in order until one succeeds.

    Returns the raw response text.
    """
    from google.genai import types
    if models is None:
        models = ["gemini-2.5-flash", "gemini-2.5-mini", "gemini-2.1-mini"]

    last_err = None
    for model in models:
        try:
            client = get_gemini_client()
            if client is None:
                raise RuntimeError("GEMINI_API_KEY is not set.")
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=max_tokens),
            )
            return resp.text or ""
        except Exception as e:
            last_err = e
            msg = str(e)
            logger.warning("Gemini model %s failed: %s", model, msg)
            # Try next model on any failure (quota, parse, etc.)
            continue

    # If we reach here, all models failed — raise the last error for callers to handle
    if last_err:
        raise last_err
    return ""


# ─────────────────────────────────────────────────────────────
# TOOL 1: analyze_customer_base
# ─────────────────────────────────────────────────────────────

def analyze_customer_base() -> dict:
    """Query live CRM data and return structured customer analytics."""
    stats = db.session.query(
        func.count(Customer.id).label("total"),
        func.sum(func.cast(Customer.tag == "Dormant", db.Integer)).label("dormant"),
        func.sum(func.cast(Customer.tag == "AtRisk", db.Integer)).label("at_risk"),
        func.sum(func.cast(Customer.tag == "Loyal", db.Integer)).label("loyal"),
        func.sum(func.cast(Customer.tag == "New", db.Integer)).label("new_customers"),
        func.coalesce(func.avg(Customer.total_spend), 0).label("avg_spend"),
    ).one()

    avg_order = float(db.session.query(func.coalesce(func.avg(Order.amount), 0)).scalar())
    total_revenue = float(db.session.query(func.coalesce(func.sum(Order.amount), 0)).scalar())

    recent_campaigns = Campaign.query.order_by(Campaign.created_at.desc()).limit(5).all()
    campaign_history = [
        {"goal": c.goal, "channel": c.channel, "segment": c.segment_name}
        for c in recent_campaigns
    ]

    total = int(stats.total or 0)
    dormant = int(stats.dormant or 0)
    at_risk = int(stats.at_risk or 0)
    loyal = int(stats.loyal or 0)
    new_cust = int(stats.new_customers or 0)
    avg_spend = float(stats.avg_spend or 0)

    return {
        "total_customers": total,
        "segments": {
            "Dormant": dormant,
            "AtRisk": at_risk,
            "Loyal": loyal,
            "New": new_cust,
            "All": total,
        },
        "avg_spend": avg_spend,
        "avg_order": avg_order,
        "total_revenue": total_revenue,
        "campaign_history": campaign_history,
        "win_back_potential": round(dormant * avg_spend * 0.3),
    }


# ─────────────────────────────────────────────────────────────
# TOOL 2: select_target_segment
# ─────────────────────────────────────────────────────────────

_GOAL_SEGMENT_RULES = [
    (["dormant", "inactive", "lapsed", "win back", "re-engage", "haven't purchased", "lost"],  "Dormant"),
    (["churn", "at risk", "atrisk", "declining", "losing", "retain"],                          "AtRisk"),
    (["loyal", "vip", "high value", "best customers", "premium", "upsell", "top customers"],   "Loyal"),
    (["new", "welcome", "onboard", "first purchase", "acquisition", "fresh"],                  "New"),
]


def select_target_segment(base: dict, goal: str) -> dict:
    """Map a natural-language goal to the best customer segment using rule-based logic."""
    goal_lower = goal.lower()
    for keywords, tag in _GOAL_SEGMENT_RULES:
        if any(kw in goal_lower for kw in keywords):
            count = base["segments"].get(tag, base["total_customers"])
            return {
                "tag": tag,
                "size": count,
                "selection_method": "rule_based",
                "reasoning": f"Goal contains keywords matching {tag} segment ({count} customers).",
            }

    best_tag = max(base["segments"].items(), key=lambda kv: kv[1] if kv[0] != "All" else -1)
    return {
        "tag": best_tag[0],
        "size": best_tag[1],
        "selection_method": "largest_segment",
        "reasoning": f"No strong keyword match — defaulting to largest segment: {best_tag[0]} ({best_tag[1]} customers).",
    }


# ─────────────────────────────────────────────────────────────
# TOOL 3: choose_best_channel
# ─────────────────────────────────────────────────────────────

_DEFAULT_OPEN_RATES = {"WhatsApp": 0.70, "Email": 0.40, "SMS": 0.50, "Push": 0.25}
_DEFAULT_CLICK_RATES = {"WhatsApp": 0.21, "Email": 0.12, "SMS": 0.15, "Push": 0.07}


def _get_channel_performance() -> dict:
    rows = (
        db.session.query(
            Campaign.channel,
            Communication.status,
            func.count(Communication.id).label("cnt"),
        )
        .join(Communication, Communication.campaign_id == Campaign.id)
        .group_by(Campaign.channel, Communication.status)
        .all()
    )
    perf = {}
    for row in rows:
        ch = row.channel
        if ch not in perf:
            perf[ch] = {"total": 0, "opened": 0, "clicked": 0}
        perf[ch]["total"] += row.cnt
        if row.status in ("opened", "clicked"):
            perf[ch]["opened"] += row.cnt
        if row.status == "clicked":
            perf[ch]["clicked"] += row.cnt

    result = {}
    for ch in ["WhatsApp", "Email", "SMS", "Push"]:
        data = perf.get(ch, {})
        total = data.get("total", 0)
        if total > 0:
            open_rate = round(data["opened"] / total, 3)
            click_rate = round(data["clicked"] / total, 3)
            campaigns_run = Campaign.query.filter_by(channel=ch).count()
        else:
            open_rate = _DEFAULT_OPEN_RATES[ch]
            click_rate = _DEFAULT_CLICK_RATES[ch]
            campaigns_run = 0
        result[ch] = {
            "open_rate": open_rate,
            "click_rate": click_rate,
            "campaigns_run": campaigns_run,
            "data_source": "historical" if total > 0 else "default",
        }
    return result


def choose_best_channel(segment_tag: str, goal: str = "") -> dict:
    """Select the channel with the highest open rate for this segment, with goal overrides."""
    perf = _get_channel_performance()
    goal_lower = goal.lower()

    # Goal-driven overrides (immediacy signals)
    if any(kw in goal_lower for kw in ["weekend", "flash", "tonight", "urgent", "today"]):
        chosen = "WhatsApp"
        reason = "High-urgency goal — WhatsApp chosen for immediacy (70% open rate)"
    elif any(kw in goal_lower for kw in ["email newsletter", "announcement", "blog", "digest"]):
        chosen = "Email"
        reason = "Informational goal — Email chosen for rich content delivery"
    else:
        chosen = max(perf.keys(), key=lambda ch: perf[ch]["open_rate"])
        rate = perf[chosen]["open_rate"]
        src = perf[chosen]["data_source"]
        reason = f"{chosen} has the highest open rate ({rate * 100:.0f}%) — source: {src} data"

    ch_data = perf[chosen]
    return {
        "channel": chosen,
        "open_rate": ch_data["open_rate"],
        "open_rate_pct": round(ch_data["open_rate"] * 100),
        "click_rate_pct": round(ch_data["click_rate"] * 100),
        "campaigns_run": ch_data["campaigns_run"],
        "reason": reason,
        "all_channels": perf,
    }


# ─────────────────────────────────────────────────────────────
# TOOL 4: generate_message_variants
# ─────────────────────────────────────────────────────────────

def generate_message_variants(goal: str, segment: dict, channel: str, offer: str = "") -> dict:
    """Call Gemini to produce 3 message variants with distinct tones."""
    prompt = f"""You are a CRM copywriter for XenoPilot. Write 3 short campaign message variants for a {channel} campaign.

Goal: {goal}
Audience Segment: {segment['tag']} ({segment['size']} customers)
Offer (if any): {offer or 'decide the best offer'}

Rules:
- Max 160 characters per message
- Variant A: direct/urgent tone
- Variant B: emotional/storytelling tone  
- Variant C: curiosity/mystery tone
- Include a clear CTA in each
- Never repeat the same phrasing across variants

Respond ONLY with valid JSON (no markdown):
{{"variants":["Variant A text","Variant B text","Variant C text"],"primary":"best single message for this goal","offer":"the specific offer or incentive"}}"""

    try:
        result = _gemini_json(prompt, max_tokens=1024)
        return {
            "variants": result.get("variants", [])[:3],
            "primary": result.get("primary", ""),
            "offer": result.get("offer", offer),
            "generated_by": "gemini",
        }
    except Exception as e:
        logger.error(f"generate_message_variants failed: {e}")
        fallback = f"Exclusive offer for {segment['tag']} customers — act now!"
        return {
            "variants": [fallback, fallback, fallback],
            "primary": fallback,
            "offer": offer or "Special offer",
            "generated_by": "fallback",
        }


# ─────────────────────────────────────────────────────────────
# TOOL 5: forecast_revenue
# ─────────────────────────────────────────────────────────────

def forecast_revenue(audience_size: int, channel_data: dict, avg_order: float, avg_spend: float) -> dict:
    """Compute expected reach and revenue from live CRM metrics."""
    open_rate = channel_data["open_rate"]
    click_rate = channel_data["click_rate_pct"] / 100 if "click_rate_pct" in channel_data else channel_data.get("click_rate", 0.15)

    expected_reach = round(audience_size * open_rate)
    expected_conversions = round(expected_reach * click_rate * 0.25)
    expected_revenue = round(expected_conversions * avg_order)
    win_back_potential = round(audience_size * avg_spend * 0.30)

    return {
        "audience_size": audience_size,
        "expected_reach": expected_reach,
        "expected_conversions": expected_conversions,
        "expected_revenue": expected_revenue,
        "win_back_potential": win_back_potential,
        "avg_order": avg_order,
        "avg_spend": avg_spend,
        "open_rate_pct": round(open_rate * 100),
        "calculation": {
            "formula": "audience × open_rate × click_rate × conversion_rate × avg_order",
            "steps": [
                f"{audience_size} customers × {open_rate * 100:.0f}% open rate = {expected_reach} opens",
                f"{expected_reach} opens × {click_rate * 100:.0f}% click rate = {round(expected_reach * click_rate)} clicks",
                f"{round(expected_reach * click_rate)} clicks × 25% conversion = {expected_conversions} orders",
                f"{expected_conversions} orders × ₹{avg_order:,.0f} avg order = ₹{expected_revenue:,}",
            ],
        },
    }


# ─────────────────────────────────────────────────────────────
# TOOL 6: generate_final_strategy  (orchestrates all tools)
# ─────────────────────────────────────────────────────────────

def generate_final_strategy(goal: str) -> dict:
    """
    Full tool chain:
      analyze_customer_base → select_target_segment → choose_best_channel
        → generate_message_variants → forecast_revenue → assemble output
    """
    # Step 1
    base = analyze_customer_base()

    # Step 2
    segment = select_target_segment(base, goal)

    # Step 3
    channel_data = choose_best_channel(segment["tag"], goal)

    # Step 4
    messages = generate_message_variants(goal, segment, channel_data["channel"])

    # Step 5
    forecast = forecast_revenue(
        segment["size"], channel_data, base["avg_order"], base["avg_spend"]
    )

    return {
        "goal": goal,
        "tools_used": [
            "analyze_customer_base",
            "select_target_segment",
            "choose_best_channel",
            "generate_message_variants",
            "forecast_revenue",
        ],
        "customer_base": base,
        "segment": segment,
        "channel": channel_data,
        "messages": messages,
        "forecast": forecast,
        "audience": f"{segment['tag']} customers ({segment['size']:,} total)",
        "audienceTag": segment["tag"],
        "audienceSize": segment["size"],
        "channel_name": channel_data["channel"],
        "expectedReach": forecast["expected_reach"],
        "expectedRevenue": forecast["expected_revenue"],
        "message": messages["primary"],
        "offer": messages["offer"],
    }


# ─────────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────────

@ai_bp.post("/ai/strategy")
def generate_strategy():
    data = request.get_json(silent=True) or {}
    goal = (data.get("goal") or "").strip()
    if not goal:
        return jsonify({"error": "goal is required"}), 400

    try:
        result = generate_final_strategy(goal)
        return jsonify(result)
    except Exception as e:
        logger.error(f"AI strategy generation failed: {e}")
        return jsonify({"error": "Failed to generate strategy. Check your Gemini API key."}), 500


@ai_bp.get("/ai/tools/customer-base")
def tool_customer_base():
    """Expose analyze_customer_base as a standalone API tool."""
    try:
        return jsonify(analyze_customer_base())
    except Exception as e:
        logger.error(f"customer-base tool failed: {e}")
        return jsonify({"error": str(e)}), 500


@ai_bp.post("/ai/tools/segment")
def tool_select_segment():
    """Expose select_target_segment as a standalone API tool."""
    data = request.get_json(silent=True) or {}
    goal = data.get("goal", "")
    try:
        base = analyze_customer_base()
        segment = select_target_segment(base, goal)
        return jsonify({"goal": goal, "segment": segment, "customer_base": base})
    except Exception as e:
        logger.error(f"segment tool failed: {e}")
        return jsonify({"error": str(e)}), 500


@ai_bp.post("/ai/tools/channel")
def tool_choose_channel():
    """Expose choose_best_channel as a standalone API tool."""
    data = request.get_json(silent=True) or {}
    segment_tag = data.get("segmentTag", "All")
    goal = data.get("goal", "")
    try:
        return jsonify(choose_best_channel(segment_tag, goal))
    except Exception as e:
        logger.error(f"channel tool failed: {e}")
        return jsonify({"error": str(e)}), 500


@ai_bp.post("/ai/tools/messages")
def tool_generate_messages():
    """Expose generate_message_variants as a standalone API tool."""
    data = request.get_json(silent=True) or {}
    goal = data.get("goal", "")
    segment = data.get("segment", {"tag": "All", "size": 0})
    channel = data.get("channel", "Email")
    offer = data.get("offer", "")
    try:
        return jsonify(generate_message_variants(goal, segment, channel, offer))
    except Exception as e:
        logger.error(f"messages tool failed: {e}")
        return jsonify({"error": str(e)}), 500


@ai_bp.post("/ai/tools/forecast")
def tool_forecast():
    """Expose forecast_revenue as a standalone API tool."""
    data = request.get_json(silent=True) or {}
    try:
        base = analyze_customer_base()
        segment_tag = data.get("segmentTag", "All")
        channel_name = data.get("channel", "Email")
        audience_size = data.get("audienceSize", base["segments"].get(segment_tag, base["total_customers"]))
        channel_data = choose_best_channel(segment_tag)
        channel_data["channel"] = channel_name
        return jsonify(forecast_revenue(audience_size, channel_data, base["avg_order"], base["avg_spend"]))
    except Exception as e:
        logger.error(f"forecast tool failed: {e}")
        return jsonify({"error": str(e)}), 500


@ai_bp.post("/ai/insights")
@ai_bp.get("/ai/insights")
def get_insights():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).limit(20).all()

    comm_stats = (
        db.session.query(
            Communication.campaign_id,
            Communication.status,
            func.count(Communication.id).label("cnt"),
        )
        .group_by(Communication.campaign_id, Communication.status)
        .all()
    )

    stats_map: dict = {}
    for row in comm_stats:
        if row.campaign_id not in stats_map:
            stats_map[row.campaign_id] = {}
        stats_map[row.campaign_id][row.status] = row.cnt

    campaign_data = []
    for c in campaigns:
        m = stats_map.get(c.id, {})
        total = sum(m.values())
        delivered = m.get("delivered", 0) + m.get("opened", 0) + m.get("clicked", 0)
        opened = m.get("opened", 0) + m.get("clicked", 0)
        clicked = m.get("clicked", 0)
        campaign_data.append({
            "goal": c.goal,
            "channel": c.channel,
            "segment": c.segment_name,
            "total": total,
            "delivered": delivered,
            "opened": opened,
            "clicked": clicked,
            "deliveryRate": round(delivered / total * 100, 1) if total > 0 else 0,
            "openRate": round(opened / total * 100, 1) if total > 0 else 0,
            "clickRate": round(clicked / total * 100, 1) if total > 0 else 0,
        })

    seg_counts = db.session.query(Customer.tag, func.count(Customer.id)).group_by(Customer.tag).all()
    total_rev = db.session.query(func.coalesce(func.sum(Order.amount), 0)).scalar()
    attr_rev = db.session.query(func.coalesce(func.sum(Attribution.attributed_revenue), 0)).scalar()

    if not campaign_data:
        return jsonify([{
            "id": "insight-no-data",
            "title": "No Campaign Data Yet",
            "insight": "Launch your first campaign via the AI Strategist to start generating insights.",
            "category": "engagement",
            "metric": None,
        }])

    prompt = f"""You are a marketing analytics AI for XenoPilot CRM. Analyze this real campaign performance data and generate exactly 4 concise, data-driven insight cards.

Campaign performance data:
{json.dumps(campaign_data, indent=2)}

Customer segments:
{json.dumps({tag: count for tag, count in seg_counts}, indent=2)}

Total platform revenue: ₹{float(total_rev or 0):,.2f}
Campaign attributed revenue: ₹{float(attr_rev or 0):,.2f}

Respond ONLY with a valid JSON array (no markdown) of exactly 4 insight objects:
[
  {{
    "id": "insight-1",
    "title": "Short insight title (5-8 words)",
    "insight": "One specific sentence with real numbers and an actionable finding from the data",
    "category": "engagement",
    "metric": "Key metric value as a string (e.g. '68% open rate') or null"
  }}
]

Categories must be from: engagement, revenue, channel, segment
Make each insight specific with real numbers from the data above. Each category must appear once."""

    try:
        from config import get_gemini_api_key
        if not get_gemini_api_key():
            logger.warning("get_insights: GEMINI_API_KEY missing — returning fallback insight")
            raise RuntimeError("GEMINI_API_KEY is not set.")
        client = get_gemini_client()
        from google.genai import types
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=8192),
        )
        raw = response.text or ""
        insights = json.loads(clean_json_response(raw))
        return jsonify(insights)
    except Exception as e:
        logger.error(f"AI insights generation failed: {e}")
        return jsonify([{
            "id": "insight-error",
            "title": "Insights Unavailable",
            "insight": "Could not generate insights at this time. Ensure your GEMINI_API_KEY is valid.",
            "category": "engagement",
            "metric": None,
        }])
