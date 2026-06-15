import logging
from datetime import date, timedelta
from flask import Blueprint, jsonify
from sqlalchemy import func
from models import db, Customer, Campaign, Communication, Order

logger = logging.getLogger(__name__)
dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/dashboard/stats")
@dashboard_bp.get("/dashboard/summary")
def stats():
    total_customers = Customer.query.count()

    total_revenue = db.session.query(func.coalesce(func.sum(Order.amount), 0)).scalar()

    active_campaigns = Campaign.query.filter_by(status="active").count()

    comm_sent = Communication.query.filter(
        Communication.status.in_(["sent", "delivered", "opened", "clicked", "failed"])
    ).count()
    comm_clicked = Communication.query.filter_by(status="clicked").count()
    success_rate = round((comm_clicked / comm_sent) * 100) if comm_sent > 0 else 0

    return jsonify({
        "totalCustomers": total_customers,
        "totalRevenue": float(total_revenue),
        "activeCampaigns": active_campaigns,
        "campaignSuccessRate": success_rate,
    })


@dashboard_bp.get("/dashboard/opportunities")
def opportunities():
    today = date.today()
    dormant_cutoff = today - timedelta(days=90)
    at_risk_cutoff = today - timedelta(days=60)

    dormant_count = Customer.query.filter(
        (Customer.last_order_date == None) | (Customer.last_order_date < dormant_cutoff)
    ).count()

    at_risk_count = Customer.query.filter(
        Customer.last_order_date >= dormant_cutoff,
        Customer.last_order_date < at_risk_cutoff,
    ).count()

    avg_spend_result = db.session.query(func.coalesce(func.avg(Customer.total_spend), 0)).scalar()
    avg_spend = float(avg_spend_result)

    total_customers = Customer.query.count()
    top_20_pct = max(1, round(total_customers * 0.2))
    high_value_threshold_row = (
        db.session.query(Customer.total_spend)
        .order_by(Customer.total_spend.desc())
        .offset(top_20_pct - 1)
        .limit(1)
        .scalar()
    )
    high_value_count = 0
    high_value_threshold = 0.0
    if high_value_threshold_row is not None:
        high_value_threshold = float(high_value_threshold_row)
        high_value_count = Customer.query.filter(
            Customer.total_spend >= high_value_threshold
        ).count()

    opps = []

    if dormant_count > 0:
        potential = round(dormant_count * avg_spend * 0.3)
        opps.append({
            "id": "opp-dormant",
            "title": "Win-Back Opportunity",
            "description": f"{dormant_count} customers are dormant with no recent purchases.",
            "action": "Run a Win-Back Campaign",
            "metric": f"Potential recoverable revenue: ₹{potential:,}",
            "severity": "critical",
            "customerCount": dormant_count,
            "audienceTag": "Dormant",
        })

    if at_risk_count > 0:
        opps.append({
            "id": "opp-at-risk",
            "title": "Retain At-Risk Customers",
            "description": f"{at_risk_count} customers show signs of churn — purchase frequency dropping.",
            "action": "Send a Loyalty Reward Campaign",
            "metric": f"{at_risk_count} customers at risk",
            "severity": "warning",
            "customerCount": at_risk_count,
            "audienceTag": "AtRisk",
        })

    if high_value_count > 0:
        opps.append({
            "id": "opp-high-value",
            "title": "Upsell High-Value Customers",
            "description": f"{high_value_count} customers are in the top 20% by lifetime spend (≥₹{high_value_threshold:,.0f}).",
            "action": "Launch an Exclusive Offer Campaign",
            "metric": f"{high_value_count} high-value customers ready to engage",
            "severity": "info",
            "customerCount": high_value_count,
            "audienceTag": "Loyal",
        })

    return jsonify(opps)
