from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, nullable=False, unique=True)
    password_hash = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    email = db.Column(db.String, nullable=False, unique=True)
    phone = db.Column(db.String, nullable=False)
    city = db.Column(db.String, nullable=False)
    total_spend = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    last_order_date = db.Column(db.Date)
    tag = db.Column(db.String, nullable=False, default="New")
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    orders = db.relationship("Order", backref="customer", lazy="dynamic")


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    order_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)


class Campaign(db.Model):
    __tablename__ = "campaigns"

    id = db.Column(db.Integer, primary_key=True)
    goal = db.Column(db.String, nullable=False)
    segment_name = db.Column(db.String, nullable=False)
    channel = db.Column(db.String, nullable=False)
    message = db.Column(db.String, nullable=False)
    audience_tag = db.Column(db.String, nullable=False, default="All")
    offer = db.Column(db.String)
    status = db.Column(db.String, nullable=False, default="active")
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    communications = db.relationship("Communication", backref="campaign", lazy="dynamic")
    attributions = db.relationship("Attribution", backref="campaign", lazy="dynamic")


class Communication(db.Model):
    __tablename__ = "communications"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    # Lifecycle: queued → sent → delivered → opened → clicked → failed / permanently_failed
    status = db.Column(db.String, nullable=False, default="queued")
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    receipts = db.relationship("Receipt", backref="communication", lazy="dynamic")


class Receipt(db.Model):
    __tablename__ = "receipts"

    id = db.Column(db.Integer, primary_key=True)
    communication_id = db.Column(
        db.Integer, db.ForeignKey("communications.id"), nullable=False
    )
    event_type = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)


class Attribution(db.Model):
    """Tracks campaign-driven conversions — a customer who clicked and then placed an order."""
    __tablename__ = "attributions"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    communication_id = db.Column(db.Integer, db.ForeignKey("communications.id"), nullable=False)
    attributed_revenue = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
