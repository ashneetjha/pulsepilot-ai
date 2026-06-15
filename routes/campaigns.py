import logging
from flask import Blueprint, request, jsonify
from sqlalchemy import func
from models import db, Campaign, Communication, Customer, Attribution

logger = logging.getLogger(__name__)
campaigns_bp = Blueprint("campaigns", __name__)

VALID_CHANNELS = {"WhatsApp", "Email", "SMS", "Push"}
VALID_TAGS = {"Loyal", "AtRisk", "Dormant", "New", "All"}
STATUS_ORDER = ["queued", "sent", "delivered", "opened", "clicked", "failed", "permanently_failed"]


def get_campaign_stats(campaign_id: int) -> dict:
    rows = (
        db.session.query(Communication.status, func.count(Communication.id).label("cnt"))
        .filter(Communication.campaign_id == campaign_id)
        .group_by(Communication.status)
        .all()
    )
    m = {r.status: r.cnt for r in rows}

    total_audience = db.session.query(func.count(Communication.id)).filter(
        Communication.campaign_id == campaign_id
    ).scalar() or 0

    sent = sum(m.get(s, 0) for s in ["sent", "delivered", "opened", "clicked", "failed", "permanently_failed"])
    delivered = m.get("delivered", 0) + m.get("opened", 0) + m.get("clicked", 0)
    opened = m.get("opened", 0) + m.get("clicked", 0)
    clicked = m.get("clicked", 0)
    failed = m.get("failed", 0)
    permanently_failed = m.get("permanently_failed", 0)

    attr_row = db.session.query(
        func.count(Attribution.id).label("conversions"),
        func.coalesce(func.sum(Attribution.attributed_revenue), 0).label("revenue"),
    ).filter(Attribution.campaign_id == campaign_id).one()

    conversions = int(attr_row.conversions or 0)
    attributed_revenue = float(attr_row.revenue or 0)
    conversion_rate = round(conversions / total_audience * 100, 1) if total_audience > 0 else 0.0

    return {
        "sent": sent,
        "delivered": delivered,
        "opened": opened,
        "clicked": clicked,
        "failed": failed,
        "permanentlyFailed": permanently_failed,
        "conversions": conversions,
        "conversionRate": conversion_rate,
        "attributedRevenue": attributed_revenue,
        "audienceSize": total_audience,
    }


def campaign_to_dict(c: Campaign) -> dict:
    stats = get_campaign_stats(c.id)
    return {
        "id": c.id,
        "goal": c.goal,
        "segmentName": c.segment_name,
        "channel": c.channel,
        "message": c.message,
        "audienceTag": c.audience_tag,
        "offer": c.offer,
        "status": c.status,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
        **stats,
    }


@campaigns_bp.get("/campaigns")
def list_campaigns():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    return jsonify([campaign_to_dict(c) for c in campaigns])


@campaigns_bp.post("/campaigns")
def create_campaign():
    from channel_service import simulate_campaign
    import flask

    data = request.get_json(silent=True) or {}
    goal = (data.get("goal") or "").strip()
    segment_name = (data.get("segmentName") or "").strip()
    channel = data.get("channel", "Email")
    message = (data.get("message") or "").strip()
    audience_tag = data.get("audienceTag", "All")
    offer = data.get("offer")

    if not goal or not message:
        return jsonify({"error": "goal and message are required"}), 400
    if channel not in VALID_CHANNELS:
        return jsonify({"error": f"channel must be one of {sorted(VALID_CHANNELS)}"}), 400
    if audience_tag not in VALID_TAGS:
        audience_tag = "All"
    if not segment_name:
        segment_name = audience_tag

    campaign = Campaign(
        goal=goal, segment_name=segment_name, channel=channel,
        message=message, audience_tag=audience_tag, offer=offer, status="active",
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

    app = flask.current_app._get_current_object()
    comm_jobs = [
        {"id": c.id, "message": message, "channel": channel,
         "campaign_id": campaign.id, "customer_id": c.customer_id}
        for c in comms
    ]
    simulate_campaign(app, comm_jobs)

    return jsonify(campaign_to_dict(campaign)), 201


@campaigns_bp.get("/campaigns/<int:campaign_id>")
def get_campaign(campaign_id: int):
    c = Campaign.query.get_or_404(campaign_id)
    return jsonify(campaign_to_dict(c))
