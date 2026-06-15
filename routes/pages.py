import csv
import io
import json
import logging
import os
from datetime import date, timedelta

from flask import (
    Blueprint, render_template, request, session,
    redirect, url_for, make_response, jsonify
)
from sqlalchemy import func, or_, asc, desc

from models import db, Customer, Order, Campaign, Communication, Receipt

logger = logging.getLogger(__name__)
pages_bp = Blueprint("pages", __name__)

VALID_SORT = {
    "totalSpend": Customer.total_spend,
    "lastOrderDate": Customer.last_order_date,
    "name": Customer.name,
}
CHANNEL_RATES = {"WhatsApp": 0.7, "Email": 0.4, "SMS": 0.5, "Push": 0.25}


@pages_bp.before_request
def require_auth():
    path = request.path
    if (path.startswith("/login") or
            path.startswith("/api") or
            path.startswith("/static") or
            path == "/favicon.ico"):
        return None
    if not session.get("user_id"):
        return redirect(url_for("pages.login_page"))
    return None


@pages_bp.get("/")
def index():
    return redirect(url_for("pages.dashboard"))


@pages_bp.get("/login")
def login_page():
    if session.get("user_id"):
        return redirect(url_for("pages.dashboard"))
    return render_template("login.html", error=request.args.get("error", ""))


@pages_bp.post("/login")
def login_submit():
    from routes.auth import check_password
    from models import User
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    user = User.query.filter_by(email=email).first()
    if not user or not check_password(password, user.password_hash):
        return redirect(url_for("pages.login_page", error="Invalid email or password"))
    session.permanent = True
    session["user_id"] = user.id
    session["user_email"] = user.email
    return redirect(url_for("pages.dashboard"))


@pages_bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("pages.login_page"))


@pages_bp.get("/dashboard")
def dashboard():
    return render_template("dashboard.html", active="dashboard")


@pages_bp.get("/strategist")
def strategist():
    dormant_customers = Customer.query.filter_by(tag="Dormant").order_by(Customer.total_spend.desc()).all()
    avg_spend = float(db.session.query(func.coalesce(func.avg(Customer.total_spend), 0)).scalar())
    recovery_potential = len(dormant_customers) * avg_spend * 0.3
    return render_template("strategist.html", active="strategist",
                           dormant_customers=dormant_customers,
                           dormant_count=len(dormant_customers),
                           recovery_potential=recovery_potential)


@pages_bp.get("/customer-360")
def customer_360_index():
    first_customer = Customer.query.first()
    if first_customer:
        return redirect(url_for("pages.customer_360", customer_id=first_customer.id))
    return redirect(url_for("pages.customers"))


@pages_bp.get("/customers")
def customers():
    search = request.args.get("search", "")
    tag = request.args.get("tag", "")
    sort_by = request.args.get("sortBy", "totalSpend")
    sort_order = request.args.get("sortOrder", "desc")
    return render_template("customers.html", active="customers",
                           search=search, tag=tag,
                           sort_by=sort_by, sort_order=sort_order)


@pages_bp.get("/customers/<int:customer_id>")
def customer_360(customer_id: int):
    c = Customer.query.get_or_404(customer_id)
    all_orders = Order.query.filter_by(customer_id=customer_id).order_by(Order.order_date.asc()).all()
    orders = sorted(all_orders, key=lambda o: o.order_date, reverse=True)[:10]
    order_count = len(all_orders)
    avg_order = float(db.session.query(func.coalesce(func.avg(Order.amount), 0)).filter(Order.customer_id == customer_id).scalar())
    days_since = None
    if c.last_order_date:
        days_since = (date.today() - c.last_order_date).days

    # Build customer journey timeline from orders + communications
    comms_q = (
        db.session.query(Communication, Campaign.channel, Campaign.goal)
        .join(Campaign, Communication.campaign_id == Campaign.id)
        .filter(Communication.customer_id == customer_id)
        .filter(Communication.status.in_(["delivered", "opened", "clicked"]))
        .order_by(Communication.updated_at.asc())
        .limit(20)
        .all()
    )
    timeline = []
    for order in all_orders:
        timeline.append({
            "date": order.order_date,
            "type": "purchase",
            "label": f"Purchased ₹{float(order.amount):,.0f}",
            "detail": f"Order #{order.id}",
        })
    for comm, channel, goal in comms_q:
        event_date = (comm.updated_at.date() if comm.updated_at else
                      comm.created_at.date() if comm.created_at else None)
        if event_date is None:
            continue
        if comm.status == "clicked":
            label = f"Clicked offer in {channel} campaign"
        elif comm.status == "opened":
            label = f"Opened {channel} campaign"
        else:
            label = f"Received {channel} campaign"
        timeline.append({
            "date": event_date,
            "type": comm.status,
            "label": label,
            "detail": (goal or "")[:55],
        })
    timeline.sort(key=lambda x: x["date"])

    return render_template("customer_360.html", active="customers",
                           customer=c, orders=orders,
                           order_count=order_count,
                           avg_order=avg_order,
                           days_since=days_since,
                           timeline=timeline)


@pages_bp.get("/campaigns")
def campaigns():
    return render_template("campaigns.html", active="campaigns")


@pages_bp.get("/insights")
def insights():
    total_campaigns = Campaign.query.count()
    total_customers = Customer.query.count()
    return render_template("insights.html", active="insights",
                           total_campaigns=total_campaigns,
                           total_customers=total_customers)


@pages_bp.get("/architecture")
def architecture():
    return render_template("architecture.html", active="architecture")


@pages_bp.get("/htmx/dashboard/stats")
def htmx_stats():
    total_customers = Customer.query.count()
    total_revenue = float(db.session.query(func.coalesce(func.sum(Order.amount), 0)).scalar())
    active_campaigns = Campaign.query.filter_by(status="active").count()
    comm_sent = Communication.query.filter(
        Communication.status.in_(["sent", "delivered", "opened", "clicked", "failed"])
    ).count()
    comm_clicked = Communication.query.filter_by(status="clicked").count()
    success_rate = round((comm_clicked / comm_sent) * 100) if comm_sent > 0 else 0
    seg_counts = dict(
        db.session.query(Customer.tag, func.count(Customer.id)).group_by(Customer.tag).all()
    )
    return render_template("htmx/stats_cards.html",
                           total_customers=total_customers,
                           total_revenue=total_revenue,
                           active_campaigns=active_campaigns,
                           success_rate=success_rate,
                           seg_counts=seg_counts)


@pages_bp.get("/htmx/dashboard/executive-summary")
def htmx_executive_summary():
    total_rev = float(db.session.query(func.coalesce(func.sum(Order.amount), 0)).scalar())
    total_customers = Customer.query.count()
    seg_counts = dict(
        db.session.query(Customer.tag, func.count(Customer.id)).group_by(Customer.tag).all()
    )
    loyal_count = seg_counts.get("Loyal", 0)
    dormant_count = seg_counts.get("Dormant", 0)
    at_risk_count = seg_counts.get("AtRisk", 0)
    new_count = seg_counts.get("New", 0)

    channel_stats = _get_channel_stats()
    best_channel = max(channel_stats.items(), key=lambda x: x[1]["openRate"]) if channel_stats else ("WhatsApp", {"openRate": 70})

    city_rev = (
        db.session.query(Customer.city, func.coalesce(func.sum(Order.amount), 0).label("rev"))
        .join(Order, Order.customer_id == Customer.id)
        .group_by(Customer.city)
        .order_by(func.coalesce(func.sum(Order.amount), 0).desc())
        .first()
    )
    top_city = city_rev.city if city_rev else "N/A"
    top_city_rev = float(city_rev.rev) if city_rev else 0

    avg_spend = float(db.session.query(func.coalesce(func.avg(Customer.total_spend), 0)).scalar())
    win_back_potential = round(dormant_count * avg_spend * 0.3)

    top_segment = max(seg_counts.items(), key=lambda x: x[1])[0] if seg_counts else "Loyal"
    top_segment_pct = round(seg_counts.get(top_segment, 0) / total_customers * 100) if total_customers > 0 else 0
    total_campaigns = Campaign.query.count()

    loyal_rev = float(
        db.session.query(func.coalesce(func.sum(Order.amount), 0))
        .join(Customer, Order.customer_id == Customer.id)
        .filter(Customer.tag == "Loyal")
        .scalar()
    )
    loyal_rev_pct = round(loyal_rev / total_rev * 100) if total_rev > 0 else 0

    return render_template("htmx/executive_summary.html",
                           total_rev=total_rev,
                           loyal_count=loyal_count,
                           dormant_count=dormant_count,
                           at_risk_count=at_risk_count,
                           new_count=new_count,
                           best_channel=best_channel[0],
                           best_channel_rate=best_channel[1]["openRate"],
                           top_city=top_city,
                           top_city_rev=top_city_rev,
                           win_back_potential=win_back_potential,
                           top_segment=top_segment,
                           top_segment_pct=top_segment_pct,
                           total_campaigns=total_campaigns,
                           total_customers=total_customers,
                           avg_spend=avg_spend,
                           loyal_rev_pct=loyal_rev_pct)


@pages_bp.get("/htmx/dashboard/opportunities")
def htmx_opportunities():
    today = date.today()
    dormant_cutoff = today - timedelta(days=90)
    at_risk_cutoff = today - timedelta(days=60)

    dormant_customers = Customer.query.filter(
        Customer.tag == "Dormant"
    ).all()
    dormant_count = len(dormant_customers)

    at_risk_count = Customer.query.filter(Customer.tag == "AtRisk").count()
    loyal_count = Customer.query.filter(Customer.tag == "Loyal").count()
    total_customers = Customer.query.count()

    avg_spend = float(db.session.query(func.coalesce(func.avg(Customer.total_spend), 0)).scalar())
    avg_order = float(db.session.query(func.coalesce(func.avg(Order.amount), 0)).scalar())

    comm_stats_by_channel = (
        db.session.query(
            Campaign.channel,
            Communication.status,
            func.count(Communication.id).label("cnt"),
        )
        .join(Communication, Communication.campaign_id == Campaign.id)
        .group_by(Campaign.channel, Communication.status)
        .all()
    )

    channel_perf = {}
    for row in comm_stats_by_channel:
        ch = row.channel
        if ch not in channel_perf:
            channel_perf[ch] = {"total": 0, "opened": 0, "clicked": 0}
        channel_perf[ch]["total"] += row.cnt
        if row.status in ("opened", "clicked"):
            channel_perf[ch]["opened"] += row.cnt
        if row.status == "clicked":
            channel_perf[ch]["clicked"] += row.cnt

    opps = []
    if dormant_count > 0:
        potential_rev = round(dormant_count * avg_spend * 0.3)
        confidence = min(95, 70 + min(dormant_count, 50))
        opps.append({
            "id": "opp-dormant", "severity": "critical",
            "title": "Win Back Opportunity",
            "description": f"{dormant_count} customers haven't purchased in 90+ days.",
            "action": "Launch Win Back Campaign",
            "goal_prompt": "Re-engage dormant customers with a win-back offer",
            "customers": dormant_count,
            "potential_revenue": f"₹{potential_rev:,}",
            "confidence": confidence,
            "priority": "High",
            "metric": f"₹{potential_rev:,}",
        })
    if at_risk_count > 0:
        potential_rev = round(at_risk_count * avg_spend * 0.2)
        confidence = min(90, 65 + min(at_risk_count, 40))
        opps.append({
            "id": "opp-at-risk", "severity": "warning",
            "title": "Retain At-Risk Customers",
            "description": f"{at_risk_count} customers show declining purchase frequency.",
            "action": "Send a Loyalty Reward",
            "goal_prompt": "Retain at-risk customers before they churn with an exclusive loyalty reward",
            "customers": at_risk_count,
            "potential_revenue": f"₹{potential_rev:,}",
            "confidence": confidence,
            "priority": "Medium",
            "metric": f"₹{potential_rev:,}",
        })
    if loyal_count > 0:
        top_20_pct = max(1, round(total_customers * 0.2))
        hv_threshold = (
            db.session.query(Customer.total_spend)
            .order_by(Customer.total_spend.desc())
            .offset(top_20_pct - 1)
            .limit(1)
            .scalar()
        )
        high_value_count = 0
        if hv_threshold is not None:
            high_value_count = Customer.query.filter(Customer.total_spend >= float(hv_threshold)).count()
        potential_rev = round(high_value_count * avg_order * 1.5)
        opps.append({
            "id": "opp-high-value", "severity": "info",
            "title": "Upsell High-Value Customers",
            "description": f"{high_value_count} customers are in your top 20% by spend.",
            "action": "Launch an Exclusive Offer",
            "goal_prompt": "Target high-value customers in the top 20% by spend with a premium exclusive offer",
            "customers": high_value_count,
            "potential_revenue": f"₹{potential_rev:,}",
            "confidence": 82,
            "priority": "Low",
            "metric": f"₹{potential_rev:,}",
        })
    return render_template("htmx/opportunities.html", opportunities=opps)


@pages_bp.get("/htmx/customers/table")
def htmx_customers_table():
    search = request.args.get("search", "").strip()
    tag = request.args.get("tag", "").strip()
    sort_by = request.args.get("sortBy", "totalSpend")
    sort_order = request.args.get("sortOrder", "desc")
    try:
        page = int(request.args.get("page", 1))
        page_size = min(int(request.args.get("pageSize", 50)), 200)
    except (ValueError, TypeError):
        page, page_size = 1, 50
    q = Customer.query
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Customer.name.ilike(like), Customer.email.ilike(like)))
    if tag and tag in {"Loyal", "AtRisk", "Dormant", "New"}:
        q = q.filter(Customer.tag == tag)
    sort_col = VALID_SORT.get(sort_by, Customer.total_spend)
    q = q.order_by(desc(sort_col) if sort_order == "desc" else asc(sort_col))
    total = q.count()
    customers = q.offset((page - 1) * page_size).limit(page_size).all()
    return render_template("htmx/customers_table.html",
                           customers=customers, total=total,
                           page=page, page_size=page_size,
                           search=search, tag=tag,
                           sort_by=sort_by, sort_order=sort_order)


def _parse_csv_rows(raw_text: str) -> list[dict]:
    """Parse CSV text into normalised row dicts."""
    rows = []
    reader = csv.DictReader(io.StringIO(raw_text))
    for idx, r in enumerate(reader, start=2):   # row 2 = first data row (1 = header)
        r = {k.strip(): v.strip() for k, v in r.items() if k}
        rows.append({
            "_row": idx,
            "name": r.get("name") or r.get("Name", ""),
            "email": r.get("email") or r.get("Email", ""),
            "phone": r.get("phone") or r.get("Phone", ""),
            "city": r.get("city") or r.get("City", ""),
            "totalSpend": r.get("totalSpend") or r.get("total_spend") or "0",
            "lastOrderDate": r.get("lastOrderDate") or r.get("last_order_date") or "",
        })
    return rows


@pages_bp.post("/htmx/customers/import")
def htmx_customers_import():
    from datetime import datetime as _dt
    from routes.customers import classify_tag, _days_since

    rows = []
    content_type = request.content_type or ""
    if "multipart/form-data" in content_type:
        f = request.files.get("file")
        if f:
            raw = f.read().decode("utf-8", errors="replace")
            rows = _parse_csv_rows(raw)
    csv_data = request.form.get("csv_data", "")
    if not rows and csv_data.strip():
        rows = _parse_csv_rows(csv_data)

    imported = 0
    duplicates = 0
    skipped = 0        # non-duplicate skips (missing fields, bad date, error)
    row_errors = []    # list of {row, email, reason, kind}

    seen_emails = set()   # catch in-batch duplicates too

    for row in rows:
        row_num = row.get("_row", "?")
        email = str(row.get("email") or "").strip().lower()
        name = str(row.get("name") or "").strip()

        # --- missing required fields ---
        if not name and not email:
            skipped += 1
            row_errors.append({"row": row_num, "email": "—", "kind": "missing_fields",
                                "reason": "Both name and email are empty"})
            continue
        if not name:
            skipped += 1
            row_errors.append({"row": row_num, "email": email, "kind": "missing_fields",
                                "reason": "Name is missing"})
            continue
        if not email:
            skipped += 1
            row_errors.append({"row": row_num, "email": "—", "kind": "missing_fields",
                                "reason": f"Email is missing for '{name}'"})
            continue

        # --- duplicate checks ---
        if email in seen_emails:
            duplicates += 1
            row_errors.append({"row": row_num, "email": email, "kind": "duplicate",
                                "reason": f"Duplicate email in this file: {email}"})
            continue

        if Customer.query.filter_by(email=email).first():
            duplicates += 1
            row_errors.append({"row": row_num, "email": email, "kind": "duplicate",
                                "reason": f"Already in database: {email}"})
            continue

        # --- date parsing ---
        date_raw = str(row.get("lastOrderDate") or "").strip()
        parsed_date = None
        if date_raw:
            try:
                from datetime import date as _date
                parsed_date = _date.fromisoformat(date_raw)
            except ValueError:
                for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                    try:
                        parsed_date = _dt.strptime(date_raw, fmt).date()
                        break
                    except ValueError:
                        continue
                if parsed_date is None:
                    skipped += 1
                    row_errors.append({"row": row_num, "email": email, "kind": "invalid_date",
                                       "reason": f"Invalid date '{date_raw}' — use YYYY-MM-DD"})
                    continue

        # --- spend parsing ---
        try:
            total_spend = float(str(row.get("totalSpend") or "0").replace(",", "").strip())
        except ValueError:
            skipped += 1
            row_errors.append({"row": row_num, "email": email, "kind": "invalid_field",
                                "reason": f"Invalid totalSpend: '{row.get('totalSpend')}'"})
            continue

        # --- insert ---
        try:
            days_ago_val = _days_since(date_raw) if date_raw else None
            tag_val = classify_tag(total_spend, days_ago_val, 1 if total_spend > 0 else 0)
            c = Customer(
                name=name, email=email,
                phone=str(row.get("phone") or ""),
                city=str(row.get("city") or ""),
                total_spend=total_spend,
                last_order_date=parsed_date,
                tag=tag_val,
            )
            db.session.add(c)
            seen_emails.add(email)
            imported += 1
        except Exception as e:
            skipped += 1
            row_errors.append({"row": row_num, "email": email, "kind": "error",
                                "reason": f"DB error: {e}"})

    db.session.commit()
    return render_template("htmx/import_result.html",
                           imported=imported, duplicates=duplicates, skipped=skipped,
                           row_errors=row_errors, total_rows=len(rows))


@pages_bp.get("/htmx/campaigns/list")
def htmx_campaigns_list():
    from routes.campaigns import campaign_to_dict
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    return render_template("htmx/campaigns_list.html",
                           campaigns=[campaign_to_dict(c) for c in campaigns])


def _get_channel_stats() -> dict:
    comm_stats_by_channel = (
        db.session.query(
            Campaign.channel,
            Communication.status,
            func.count(Communication.id).label("cnt"),
        )
        .join(Communication, Communication.campaign_id == Campaign.id)
        .group_by(Campaign.channel, Communication.status)
        .all()
    )
    channel_perf = {}
    for row in comm_stats_by_channel:
        ch = row.channel
        if ch not in channel_perf:
            channel_perf[ch] = {"total": 0, "opened": 0, "clicked": 0, "delivered": 0}
        channel_perf[ch]["total"] += row.cnt
        if row.status in ("delivered", "opened", "clicked"):
            channel_perf[ch]["delivered"] += row.cnt
        if row.status in ("opened", "clicked"):
            channel_perf[ch]["opened"] += row.cnt
        if row.status == "clicked":
            channel_perf[ch]["clicked"] += row.cnt

    result = {}
    for ch, data in channel_perf.items():
        total = data["total"]
        result[ch] = {
            "openRate": round(data["opened"] / total * 100) if total > 0 else int(CHANNEL_RATES.get(ch, 0.4) * 100),
            "clickRate": round(data["clicked"] / total * 100) if total > 0 else int(CHANNEL_RATES.get(ch, 0.4) * 30),
            "deliveryRate": round(data["delivered"] / total * 100) if total > 0 else 90,
            "campaigns": Campaign.query.filter_by(channel=ch).count(),
        }

    for ch in ["WhatsApp", "Email", "SMS", "Push"]:
        if ch not in result:
            result[ch] = {
                "openRate": int(CHANNEL_RATES[ch] * 100),
                "clickRate": int(CHANNEL_RATES[ch] * 30),
                "deliveryRate": 90,
                "campaigns": 0,
            }
    return result


def _build_strategy(goal: str) -> dict:
    from routes.ai_routes import get_gemini_client, clean_json_response
    from google.genai import types

    stats = db.session.query(
        func.count(Customer.id).label("total"),
        func.sum(func.cast(Customer.tag == "Dormant", db.Integer)).label("dormant"),
        func.sum(func.cast(Customer.tag == "AtRisk", db.Integer)).label("at_risk"),
        func.sum(func.cast(Customer.tag == "Loyal", db.Integer)).label("loyal"),
        func.sum(func.cast(Customer.tag == "New", db.Integer)).label("new_customers"),
        func.coalesce(func.avg(Customer.total_spend), 0).label("avg_spend"),
    ).one()

    recent = Campaign.query.order_by(Campaign.created_at.desc()).limit(5).all()
    campaign_history = [{"goal": c.goal, "channel": c.channel} for c in recent]
    avg_order = float(db.session.query(func.coalesce(func.avg(Order.amount), 0)).scalar())
    channel_stats = _get_channel_stats()

    total = int(stats.total or 0)
    dormant_count = int(stats.dormant or 0)
    at_risk_count = int(stats.at_risk or 0)
    loyal_count = int(stats.loyal or 0)
    new_count = int(stats.new_customers or 0)
    avg_spend = float(stats.avg_spend or 0)

    tag_counts = {
        "Dormant": dormant_count,
        "AtRisk": at_risk_count,
        "Loyal": loyal_count,
        "New": new_count,
        "All": total,
    }

    prompt = f"""You are an AI marketing strategist for XenoPilot CRM. Analyze the user's natural-language goal and map it to the best campaign strategy using real CRM data.

CUSTOMER DATABASE (use these exact numbers):
- Total: {total} | Dormant (no purchase 60+ days): {dormant_count} | AtRisk (declining frequency): {at_risk_count} | Loyal (frequent buyers): {loyal_count} | New (recent): {new_count}
- Avg customer spend: ₹{avg_spend:.2f} | Avg order value: ₹{avg_order:.2f}

CHANNEL PERFORMANCE (from historical campaigns):
{json.dumps(channel_stats, indent=2)}

RECENT CAMPAIGN HISTORY:
{json.dumps(campaign_history)}

USER GOAL: "{goal}"

INTENT MAPPING — always apply this logic to interpret the goal:
- "churn", "at risk", "losing customers", "declining" → audienceTag = "AtRisk"
- "win back", "dormant", "inactive", "lapsed", "haven't purchased", "re-engage" → audienceTag = "Dormant"
- "loyal", "VIP", "high value", "top customers", "best customers", "premium", "upsell" → audienceTag = "Loyal"
- "new", "welcome", "onboard", "first purchase", "acquisition" → audienceTag = "New"
- "weekend", "flash", "urgent", "tonight" → prefer WhatsApp or SMS for immediacy
- "email newsletter", "announcement" → prefer Email
- "all customers", "everyone", "entire base" → audienceTag = "All"
- Spend threshold queries (e.g. "above ₹20000") → audienceTag = "Loyal" (high-spend segment)
- Geographic queries (e.g. "Bangalore", "Mumbai") → audienceTag = "All" with geographic focus in reason
- "most revenue", "best ROI", "highest return" → audienceTag = "Loyal"
- Analytical queries ("who is likely to churn?") → treat as a campaign goal to re-engage that segment

CHANNEL SELECTION — pick the channel with the highest open rate for this segment from the performance data above. If no history exists, use: WhatsApp=70%, Email=40%, SMS=50%, Push=25%.

Respond ONLY with valid JSON (no markdown, no code fences):
{{"audience":"descriptive segment name","reason":"specific explanation referencing real CRM numbers","channel":"WhatsApp","offer":"concrete offer or action e.g. 20% off, free shipping, loyalty points","audienceTag":"Dormant","audienceSize":0,"expectedReach":0,"expectedRevenue":0,"message":"primary ready-to-send campaign message, max 160 chars","messages":["Variant A — direct offer tone, max 160 chars","Variant B — emotional/storytelling tone, max 160 chars","Variant C — urgency/scarcity tone, max 160 chars"],"confidence":85,"confidenceReasons":["reason citing real data point 1","reason citing real data point 2","reason citing channel performance"],"channelReason":"why this channel was selected — cite open rate from performance data or CRM patterns"}}

Rules:
- audienceTag must be exactly one of: Loyal | AtRisk | Dormant | New | All
- channel must be exactly one of: WhatsApp | Email | SMS | Push
- audienceSize = EXACT count from the database numbers above for the selected tag
- confidence: integer 60–98 (higher when audience is large AND channel has strong historical data)
- confidenceReasons: always 3 items, each citing specific numbers from this data
- messages: exactly 3 distinct variants with meaningfully different tones — do NOT repeat the same message"""

    from routes.ai_routes import generate_content_with_fallback

    response_text = generate_content_with_fallback(prompt, max_tokens=4096)
    s = json.loads(clean_json_response(response_text or ""))
    audience_tag = s.get("audienceTag", "All")
    if audience_tag not in {"Loyal", "AtRisk", "Dormant", "New", "All"}:
        audience_tag = "All"
    channel = s.get("channel", "Email")
    if channel not in CHANNEL_RATES:
        channel = "Email"

    real_size = tag_counts.get(audience_tag, total)
    real_reach = round(real_size * CHANNEL_RATES[channel])
    real_revenue = round(real_reach * avg_order)
    engagement_rate = int(CHANNEL_RATES[channel] * 100)

    ch_data = channel_stats.get(channel, {"openRate": engagement_rate, "clickRate": int(engagement_rate * 0.3), "campaigns": 0})
    if ch_data.get("campaigns", 0) > 0:
        open_rate = ch_data["openRate"]
    else:
        open_rate = engagement_rate

    confidence = int(s.get("confidence", 80))
    confidence = max(60, min(98, confidence))
    confidence_reasons = s.get("confidenceReasons", [
        f"Strong audience size of {real_size} customers",
        f"{channel} has {open_rate}% open rate",
        "Historical data supports this approach"
    ])

    # Build data-driven "Why This Audience?" bullets from CRM data (no extra AI call)
    avg_order_v = avg_order
    if audience_tag == "Dormant":
        why_audience = [
            f"{dormant_count} customers inactive for 60+ days — high re-engagement potential",
            f"Avg recoverable revenue ₹{avg_spend:,.0f} per customer at 30% response rate",
            f"Win-back potential: ₹{round(dormant_count * avg_spend * 0.3):,} total revenue",
            f"Lower cost than acquiring {dormant_count} new customers from scratch",
        ]
    elif audience_tag == "AtRisk":
        why_audience = [
            f"{at_risk_count} customers showing declining purchase frequency",
            f"Early retention costs ~10% of avg order (₹{avg_order_v * 0.1:,.0f}) vs full churn loss",
            f"Revenue at risk if lost: ₹{round(at_risk_count * avg_spend):,}",
            f"{channel} has {open_rate}% open rate — best reach for immediate intervention",
        ]
    elif audience_tag == "Loyal":
        why_audience = [
            f"{loyal_count} loyal customers hold the highest lifetime value in your base",
            f"Avg spend ₹{avg_spend:,.0f} — {round(avg_spend / avg_order_v) if avg_order_v > 0 else 'multiple'}x avg order value",
            f"Upsell conversion rate 2–3× higher vs cold or dormant segments",
            f"Reward campaigns increase referral likelihood and average basket size",
        ]
    elif audience_tag == "New":
        why_audience = [
            f"{new_count} new customers who have not yet formed a repeat-purchase habit",
            f"First 90 days critical — early engagement raises LTV by 30%+",
            f"Onboarding offers convert at 2× vs standard campaign rate",
            f"Avg first order ₹{avg_order_v:,.0f} — immediate upsell opportunity",
        ]
    else:
        why_audience = [
            f"Full base of {real_size} customers maximises campaign reach",
            f"Total addressable revenue: ₹{round(real_size * avg_spend):,}",
            f"{channel} reaches all active customer segments simultaneously",
            f"Broad campaigns build brand recall across Loyal, AtRisk, and Dormant tiers",
        ]

    # Parse message variants
    raw_messages = s.get("messages", [])
    if not isinstance(raw_messages, list) or not raw_messages:
        raw_messages = [str(s.get("message", ""))]
    messages = [str(m) for m in raw_messages[:3]]
    while len(messages) < 3:
        messages.append(messages[0] if messages else "")
    primary_message = str(s.get("message", messages[0]))

    return {
        "audience": str(s.get("audience", "")),
        "reason": str(s.get("reason", "")),
        "channel": channel,
        "channel_reason": str(s.get("channelReason", f"{channel} has the highest engagement rate for this segment.")),
        "channel_open_rate": open_rate,
        "channel_click_rate": ch_data.get("clickRate", int(open_rate * 0.3)),
        "channel_campaigns": ch_data.get("campaigns", 0),
        "offer": str(s.get("offer", "")),
        "audienceTag": audience_tag,
        "audienceSize": real_size,
        "expectedReach": real_reach,
        "expectedRevenue": real_revenue,
        "avg_order": avg_order,
        "avg_spend": avg_spend,
        "engagement_rate": open_rate,
        "message": primary_message,
        "messages": messages,
        "goal": goal,
        "confidence": confidence,
        "confidence_reasons": confidence_reasons,
        "why_audience": why_audience,
    }


@pages_bp.get("/htmx/ai/reasoning-data")
def htmx_ai_reasoning_data():
    stats = db.session.query(
        func.count(Customer.id).label("total"),
        func.sum(func.cast(Customer.tag == "Dormant", db.Integer)).label("dormant"),
        func.sum(func.cast(Customer.tag == "AtRisk", db.Integer)).label("at_risk"),
        func.sum(func.cast(Customer.tag == "Loyal", db.Integer)).label("loyal"),
        func.coalesce(func.avg(Customer.total_spend), 0).label("avg_spend"),
    ).one()

    recent_campaigns = Campaign.query.order_by(Campaign.created_at.desc()).limit(5).all()
    channel_stats = _get_channel_stats()

    best_channel = max(channel_stats.items(), key=lambda x: x[1]["openRate"]) if channel_stats else ("WhatsApp", {"openRate": 70})

    avg_order = float(db.session.query(func.coalesce(func.avg(Order.amount), 0)).scalar())
    dormant = int(stats.dormant or 0)
    potential_rev = round(dormant * float(stats.avg_spend or 0) * 0.3)

    return jsonify({
        "totalCustomers": int(stats.total or 0),
        "dormantCount": dormant,
        "atRiskCount": int(stats.at_risk or 0),
        "loyalCount": int(stats.loyal or 0),
        "avgSpend": float(stats.avg_spend or 0),
        "avgOrder": avg_order,
        "campaignCount": len(recent_campaigns),
        "bestChannel": best_channel[0],
        "bestChannelOpenRate": best_channel[1]["openRate"],
        "potentialRevenue": potential_rev,
        "channelStats": channel_stats,
    })


@pages_bp.post("/htmx/ai/strategy")
def htmx_ai_strategy():
    goal = request.form.get("goal", "").strip()
    if not goal:
        return '<p class="text-red-500 p-4 text-sm">Please enter a marketing goal first.</p>'
    try:
        strat = _build_strategy(goal)
        return render_template("htmx/strategy_result.html", strategy=strat)
    except Exception as e:
        logger.error(f"HTMX strategy failed: {e}")
        return render_template("htmx/strategy_result.html", error=True,
                               error_msg="Could not generate strategy. Check your GEMINI_API_KEY.")


@pages_bp.post("/htmx/ai/launch")
def htmx_ai_launch():
    from channel_service import simulate_campaign
    import flask

    goal = request.form.get("goal", "").strip()
    audience = request.form.get("audience", "").strip()
    channel = request.form.get("channel", "Email")
    message = request.form.get("message", "").strip()
    audience_tag = request.form.get("audienceTag", "All")
    offer = request.form.get("offer", "")

    VALID_CHANNELS = {"WhatsApp", "Email", "SMS", "Push"}
    VALID_TAGS = {"Loyal", "AtRisk", "Dormant", "New", "All"}
    if channel not in VALID_CHANNELS:
        channel = "Email"
    if audience_tag not in VALID_TAGS:
        audience_tag = "All"

    campaign = Campaign(
        goal=goal or "Campaign",
        segment_name=audience or audience_tag,
        channel=channel, message=message,
        audience_tag=audience_tag, offer=offer,
        status="active",
    )
    db.session.add(campaign)
    db.session.flush()

    q = Customer.query
    if audience_tag != "All":
        q = q.filter(Customer.tag == audience_tag)
    customers = q.all()

    comms = []
    for cust in customers:
        comm = Communication(campaign_id=campaign.id, customer_id=cust.id, status="queued")
        db.session.add(comm)
        comms.append(comm)
    db.session.commit()

    app_obj = flask.current_app._get_current_object()
    comm_jobs = [
        {"id": c.id, "message": message, "channel": channel,
         "campaign_id": campaign.id, "customer_id": c.customer_id}
        for c in comms
    ]
    simulate_campaign(app_obj, comm_jobs)

    resp = make_response("", 204)
    resp.headers["HX-Redirect"] = "/campaigns"
    return resp


@pages_bp.post("/htmx/ai/insights")
def htmx_ai_insights():
    from routes.ai_routes import get_gemini_client, clean_json_response
    from google.genai import types

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
            "goal": c.goal, "channel": c.channel, "total": total,
            "deliveryRate": round(delivered / total * 100, 1) if total > 0 else 0,
            "openRate": round(opened / total * 100, 1) if total > 0 else 0,
            "clickRate": round(clicked / total * 100, 1) if total > 0 else 0,
        })

    if not campaign_data:
        return render_template("htmx/insights_cards.html", insights=[{
            "id": "insight-no-data",
            "title": "No Campaign Data Yet",
            "observation": "No campaigns have been launched yet.",
            "evidence": "Launch your first campaign via the AI Strategist to start generating insights.",
            "recommendation": "Use the AI Strategist to create and launch your first campaign.",
            "category": "engagement",
            "metric": None,
        }])

    seg_counts = dict(
        db.session.query(Customer.tag, func.count(Customer.id)).group_by(Customer.tag).all()
    )
    total_rev = float(db.session.query(func.coalesce(func.sum(Order.amount), 0)).scalar())

    prompt = f"""You are a marketing analytics AI for XenoPilot CRM. Analyze this real campaign and customer data. Generate exactly 4 executive-level insight cards. Write like a senior analyst advising a CMO — specific, data-backed, actionable. Avoid generic or vague statements.

Campaign performance data:
{json.dumps(campaign_data, indent=2)}

Customer segments: {json.dumps(seg_counts)}
Total platform revenue: ₹{total_rev:,.2f}

Return ONLY a valid JSON array (no markdown) of exactly 4 objects:
[
  {{
    "id": "insight-1",
    "title": "5-8 word insight title (specific, not generic)",
    "observation": "One precise sentence stating what the data shows — always include real numbers (e.g. '68% open rate', '₹4.2L revenue', '23 campaigns')",
    "evidence": "The key supporting data points — compare channels, segments, or time periods where possible",
    "recommendation": "One concrete, specific action — name the segment, channel, and expected outcome",
    "category": "engagement",
    "metric": "The single most important metric as a short string (e.g. '68% open rate') or null"
  }}
]

Rules:
- Use exactly the categories: engagement, revenue, channel, segment — one each
- Never say "substantial", "significant", or other vague modifiers — use exact numbers
- Each recommendation must name a specific segment (Dormant/Loyal/AtRisk/New) and channel
- Observation + evidence + recommendation should flow as a logical argument
- Avoid repeating the same finding across cards"""

    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=4096),
        )
        insights = json.loads(clean_json_response(response.text or "[]"))
        return render_template("htmx/insights_cards.html", insights=insights)
    except Exception as e:
        logger.error(f"HTMX insights failed: {e}")
        return render_template("htmx/insights_cards.html", error=True,
                               error_msg="Could not generate insights. Check your GEMINI_API_KEY.")


@pages_bp.post("/htmx/customer/<int:customer_id>/ai-summary")
def htmx_customer_ai_summary(customer_id: int):
    from routes.ai_routes import get_gemini_client, clean_json_response
    from google.genai import types

    c = Customer.query.get_or_404(customer_id)
    orders = Order.query.filter_by(customer_id=customer_id).order_by(Order.order_date.desc()).all()
    order_count = len(orders)
    avg_order = float(db.session.query(func.coalesce(func.avg(Order.amount), 0)).filter(Order.customer_id == customer_id).scalar())
    days_since = None
    if c.last_order_date:
        days_since = (date.today() - c.last_order_date).days

    prompt = f"""You are an AI analyst for XenoPilot CRM. Write a 2-3 sentence customer summary.

Customer: {c.name}
Email: {c.email}
City: {c.city}
Segment: {c.tag}
Total Spend: ₹{float(c.total_spend):,.0f}
Total Orders: {order_count}
Average Order Value: ₹{avg_order:,.0f}
Days Since Last Purchase: {days_since if days_since is not None else 'Never purchased'}

Write a factual, data-driven summary. Mention their value, recency, and suggest one specific action (e.g. upsell, win-back, loyalty reward). Be direct and specific with numbers."""

    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=512),
        )
        summary = (response.text or "").strip()
        return render_template("htmx/customer_ai_summary.html", summary=summary, error=False)
    except Exception as e:
        logger.error(f"Customer AI summary failed: {e}")
        return render_template("htmx/customer_ai_summary.html",
                               summary="", error=True,
                               error_msg="Could not generate summary. Check your GEMINI_API_KEY.")



@pages_bp.get("/htmx/customers/intelligence")
def htmx_customers_intelligence():
    """Natural-language customer query → filtered customer list."""
    from routes.ai_routes import get_gemini_client, clean_json_response, analyze_customer_base
    import re as _re

    q = request.args.get("q", "").strip()
    if not q:
        return render_template("htmx/customer_intelligence.html", error=True,
                               error_msg="Please enter a query.")

    q_lower = q.lower()

    # ── Rule-based filter mapping ─────────────────────────────────────────────
    tag_filter = None
    city_filter = None
    min_spend = None
    max_spend = None
    sort_col = Customer.total_spend
    sort_dir = "desc"
    limit = 50
    insight_title = f'Results for "{q}"'
    insight_summary = ""

    KNOWN_CITIES = {"chennai", "bangalore", "mumbai", "delhi", "hyderabad", "pune",
                    "kolkata", "ahmedabad", "jaipur", "surat", "lucknow", "kochi",
                    "indore", "nagpur", "bhopal"}

    # Segment keywords
    if any(kw in q_lower for kw in ["churn", "at risk", "atrisk", "likely to churn", "at-risk", "losing"]):
        tag_filter = "AtRisk"
        insight_title = "Customers Likely to Churn"
        insight_summary = "Showing customers with declining purchase frequency — intervene before they leave."
    elif any(kw in q_lower for kw in ["dormant", "inactive", "lapsed", "haven't purchased", "win back", "lost"]):
        tag_filter = "Dormant"
        insight_title = "Dormant Customers"
        insight_summary = "No purchases in 90+ days — prime candidates for a win-back campaign."
    elif any(kw in q_lower for kw in ["loyal", "vip", "best customers", "top customers", "high value", "loyal customer"]):
        tag_filter = "Loyal"
        insight_title = "Loyal Customers"
        insight_summary = "Your most valuable customers — ideal for upsells and exclusive offers."
    elif any(kw in q_lower for kw in ["new customer", "new customers", "fresh", "onboard", "first purchase", "recent acquisition"]):
        tag_filter = "New"
        insight_title = "New Customers"
        insight_summary = "Recently acquired customers — engage early to drive a repeat purchase."

    # City keywords
    for city in KNOWN_CITIES:
        if city in q_lower:
            city_filter = city.capitalize()
            insight_title = f"{city_filter} Customers"
            insight_summary = f"All customers from {city_filter}."
            break

    # Spend threshold keywords: "spend above 20000" / "spend > 20000"
    spend_match = _re.search(r"spend[^\d]*(\d[\d,]*)", q_lower)
    if not spend_match:
        spend_match = _re.search(r"(above|over|more than|greater than|>)\s*[₹rs\s]*(\d[\d,]*)", q_lower)
        if spend_match:
            spend_match = type('M', (), {'group': lambda s, n: spend_match.group(2)})()
    if spend_match:
        try:
            min_spend = float(spend_match.group(1).replace(",", ""))
            insight_title = f"Customers with Spend > ₹{min_spend:,.0f}"
            insight_summary = f"Showing high-value customers who have spent more than ₹{min_spend:,.0f}."
        except Exception:
            pass

    # Sort / ranking keywords
    if any(kw in q_lower for kw in ["highest value", "top spenders", "most valuable", "highest spend", "best roi", "most revenue"]):
        sort_col = Customer.total_spend
        sort_dir = "desc"
        limit = 20
        insight_title = "Highest-Value Customers"
        insight_summary = "Top 20 customers ranked by lifetime spend."

    if any(kw in q_lower for kw in ["weekend", "this weekend", "target this weekend", "who should i target"]):
        tag_filter = "AtRisk"
        insight_title = "Weekend Campaign Targets"
        insight_summary = "At-risk customers are ideal weekend re-engagement targets — high urgency, high ROI."

    # ── Build query ───────────────────────────────────────────────────────────
    use_gemini = False
    cust_q = Customer.query

    if tag_filter:
        cust_q = cust_q.filter(Customer.tag == tag_filter)
    if city_filter:
        cust_q = cust_q.filter(Customer.city.ilike(city_filter))
    if min_spend is not None:
        cust_q = cust_q.filter(Customer.total_spend >= min_spend)
    if max_spend is not None:
        cust_q = cust_q.filter(Customer.total_spend <= max_spend)

    if sort_dir == "desc":
        cust_q = cust_q.order_by(sort_col.desc())
    else:
        cust_q = cust_q.order_by(sort_col.asc())

    # If nothing matched the rules, fall back to Gemini for interpretation
    if not tag_filter and not city_filter and min_spend is None and insight_title == f'Results for "{q}"':
        use_gemini = True

    if use_gemini:
        try:
            base = analyze_customer_base()
            prompt = f"""You are a CRM assistant. A user typed this customer query: "{q}"

Map it to ONE of these filter parameters and respond with valid JSON only (no markdown):
{{"tag":"Dormant|AtRisk|Loyal|New|All","city":"city name or null","min_spend":number_or_null,"sort":"spend_desc|spend_asc|name_asc","limit":50,"insight_title":"Short descriptive title","insight_summary":"One-sentence explanation of what this query shows"}}

Available data: {base['total_customers']} total customers across segments Dormant({base['segments']['Dormant']}), AtRisk({base['segments']['AtRisk']}), Loyal({base['segments']['Loyal']}), New({base['segments']['New']}).
Cities: Chennai, Bangalore, Mumbai, Delhi, Hyderabad.
Return null for fields that don't apply."""

            client = get_gemini_client()
            from google.genai import types as _types
            resp = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=_types.GenerateContentConfig(max_output_tokens=512),
            )
            parsed = json.loads(clean_json_response(resp.text or "{}"))
            ai_tag = parsed.get("tag")
            if ai_tag and ai_tag != "All" and ai_tag in {"Dormant", "AtRisk", "Loyal", "New"}:
                cust_q = cust_q.filter(Customer.tag == ai_tag)
            ai_city = parsed.get("city")
            if ai_city:
                cust_q = cust_q.filter(Customer.city.ilike(ai_city))
            ai_min = parsed.get("min_spend")
            if ai_min:
                cust_q = cust_q.filter(Customer.total_spend >= float(ai_min))
            insight_title = parsed.get("insight_title", insight_title)
            insight_summary = parsed.get("insight_summary", insight_summary)
        except Exception as e:
            logger.error(f"NL intelligence Gemini fallback failed: {e}")
            cust_q = Customer.query.order_by(Customer.total_spend.desc())
            insight_summary = "Showing all customers by spend (AI fallback)."

    customers = cust_q.limit(limit).all()

    return render_template("htmx/customer_intelligence.html",
                           customers=customers,
                           insight_title=insight_title,
                           insight_summary=insight_summary,
                           query=q,
                           error=False)


@pages_bp.get("/customers/sample.csv")
def download_sample_csv():
    # Emails use @xp-import.demo — a domain that will never collide with seed data
    # (seed uses {name}{index}@{city}.in patterns)
    sample_data = """name,email,phone,city,totalSpend,lastOrderDate
Priya Sharma,priya.sharma@xp-import.demo,9876543210,Mumbai,18500,2025-12-15
Rahul Verma,rahul.verma@xp-import.demo,9845123456,Delhi,7200,2025-09-10
Anita Desai,anita.desai@xp-import.demo,9712345678,Bangalore,31000,2026-01-20
Suresh Kumar,suresh.kumar@xp-import.demo,9654321098,Chennai,4500,2025-07-05
Meera Nair,meera.nair@xp-import.demo,9567890123,Pune,22800,2026-02-28
Vikram Singh,vikram.singh@xp-import.demo,9432109876,Hyderabad,9600,2025-10-18
Kavitha Reddy,kavitha.reddy@xp-import.demo,9321098765,Kolkata,15000,2026-03-10
Deepak Joshi,deepak.joshi@xp-import.demo,9210987654,Ahmedabad,3200,2025-05-22
Sunita Patel,sunita.patel@xp-import.demo,9109876543,Jaipur,27500,2026-01-08
Arjun Mehta,arjun.mehta@xp-import.demo,9098765432,Lucknow,11200,2025-11-30
Nandita Rao,nandita.rao@xp-import.demo,9988776655,Surat,42000,2026-04-05
Kiran Pillai,kiran.pillai@xp-import.demo,9876012345,Kochi,8900,2025-08-14
Siddharth Bhat,siddharth.bhat@xp-import.demo,9765123456,Indore,19700,2026-03-28
Pooja Malhotra,pooja.malhotra@xp-import.demo,9654098765,Nagpur,5100,2025-06-01
Ravi Kapoor,ravi.kapoor@xp-import.demo,9543210987,Bhopal,33600,2026-02-12
"""
    response = make_response(sample_data)
    response.headers["Content-Type"] = "text/csv"
    response.headers["Content-Disposition"] = "attachment; filename=xenopilot_sample_customers.csv"
    return response


@pages_bp.post("/htmx/retention/outreach")
def htmx_retention_outreach():
    from routes.ai_routes import get_gemini_client
    from google.genai import types

    customer_id = request.form.get("customer_id")
    if not customer_id:
        return '<p class="text-red-400 text-sm">Please select a customer first.</p>'
        
    c = Customer.query.get(customer_id)
    if not c:
        return '<p class="text-red-400 text-sm">Customer not found.</p>'
        
    prompt = f"""You are an AI Retention Agent for PulsePilot AI. Draft a highly personalized outreach message (email or WhatsApp) to win back this dormant customer.
    
    Customer Name: {c.name}
    City: {c.city}
    Last Order Date: {c.last_order_date}
    Total Spend: ₹{float(c.total_spend):,.2f}
    
    Rules:
    - Keep the outreach copy under 250 characters.
    - Be warm, personal, and reference their location or spend tier if relevant.
    - Offer a custom incentive (e.g. 20% off or free shipping).
    - Include a clear, compelling Call to Action (CTA).
    - Respond ONLY with the raw message text. Do not include markdown quotes or explanations.
    """
    
    try:
        client = get_gemini_client()
        if not client:
            return '<p class="text-red-400 text-sm">Gemini API Key is not set or invalid.</p>'
            
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=512),
        )
        message = (response.text or "").strip()
        
        html = f"""
        <div class="rounded-xl bg-purple-950/40 border border-purple-800/60 p-4 space-y-3">
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold text-purple-400 uppercase tracking-wider">Generated Outreach Copy</span>
            <button type="button" 
                    onclick="navigator.clipboard.writeText(document.getElementById('outreach-text').value); this.textContent = 'Copied!';" 
                    class="text-xs text-purple-300 hover:text-purple-200 bg-purple-900/40 border border-purple-800/50 px-2 py-1 rounded">
              Copy
            </button>
          </div>
          <textarea id="outreach-text" name="message" rows="4" 
                    class="w-full bg-[#111827] border border-slate-800 rounded-lg p-2.5 text-xs text-slate-200 outline-none focus:border-purple-500">{message}</textarea>
          <div class="flex justify-end">
            <button type="submit" 
                    class="rounded-lg bg-gradient-to-r from-brand-purple to-brand-red px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:opacity-90">
              Launch Campaign for {c.name}
            </button>
          </div>
        </div>
        """
        return html
    except Exception as e:
        logger.error(f"Retention agent outreach generation failed: {e}")
        return '<p class="text-red-400 text-sm">Failed to generate personalized outreach. Please check configuration.</p>'
