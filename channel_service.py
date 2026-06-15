"""
Channel Service — simulates multi-channel message delivery.

Lifecycle per communication:
  queued → sent → delivered → opened → clicked
                            ↘ (no open)
                 → failed → retry → delivered ...
                                  → permanently_failed

Rates:
  Delivery:  90% success, 10% fail on first attempt
  Retry:     70% success on second attempt; else → permanently_failed
  Open:      60–75% of delivered
  Click:     10–30% of opened
  Conversion: 25% of clicked (stored as Attribution)

Delays:
  delivery:  2–5 s
  retry:     1–2 s
  open:      3–10 s
  click:     5–15 s
"""
import logging
import random
import threading
import time
from datetime import date

logger = logging.getLogger(__name__)

STATUS_ORDER = ["queued", "sent", "delivered", "opened", "clicked", "failed", "permanently_failed"]

DELIVERY_SUCCESS_RATE = 0.90
RETRY_SUCCESS_RATE = 0.70
OPEN_RATE_MIN = 0.60
OPEN_RATE_MAX = 0.75
CLICK_RATE_MIN = 0.10
CLICK_RATE_MAX = 0.30
CONVERSION_RATE = 0.25

DELIVERY_DELAY = (2.0, 5.0)
RETRY_DELAY = (1.0, 2.0)
OPEN_DELAY = (3.0, 10.0)
CLICK_DELAY = (5.0, 15.0)


def higher_status(current: str, incoming: str) -> str:
    if incoming == "permanently_failed":
        return "permanently_failed"
    if incoming == "failed" and current not in ("permanently_failed",):
        return "failed"
    try:
        ci = STATUS_ORDER.index(current)
        ii = STATUS_ORDER.index(incoming)
        return incoming if ii > ci else current
    except ValueError:
        return current


def record_event(app, comm_id: int, event_type: str):
    from models import db, Communication, Receipt

    try:
        with app.app_context():
            receipt = Receipt(communication_id=comm_id, event_type=event_type)
            db.session.add(receipt)

            comm = Communication.query.get(comm_id)
            if comm:
                new_status = higher_status(comm.status, event_type)
                if new_status != comm.status:
                    comm.status = new_status

            db.session.commit()
    except Exception as e:
        logger.error(f"Failed to record channel event commId={comm_id} event={event_type}: {e}")


def simulate_attribution(app, comm_id: int, campaign_id: int, customer_id: int):
    """25% of clicked communications simulate a follow-up purchase (attribution)."""
    from models import db, Attribution, Order
    import random as _r

    try:
        with app.app_context():
            existing = Attribution.query.filter_by(
                campaign_id=campaign_id, customer_id=customer_id
            ).first()
            if existing:
                return

            order_amount = round(_r.uniform(500, 5000), 2)
            attr = Attribution(
                campaign_id=campaign_id,
                customer_id=customer_id,
                communication_id=comm_id,
                attributed_revenue=order_amount,
            )
            db.session.add(attr)

            new_order = Order(
                customer_id=customer_id,
                amount=order_amount,
                order_date=date.today(),
            )
            db.session.add(new_order)
            db.session.commit()
            logger.info(f"Attribution recorded: campaign={campaign_id} customer={customer_id} revenue=₹{order_amount}")
    except Exception as e:
        logger.error(f"Attribution simulation failed comm={comm_id}: {e}")


def simulate_single(app, comm: dict):
    comm_id = comm["id"]
    campaign_id = comm.get("campaign_id")
    customer_id = comm.get("customer_id")

    # ── Sent ──────────────────────────────────────────────────────────────────
    time.sleep(0.3 + random.random() * 0.5)
    record_event(app, comm_id, "sent")

    # ── First delivery attempt (90% success) ──────────────────────────────────
    time.sleep(random.uniform(*DELIVERY_DELAY))
    first_attempt_ok = random.random() < DELIVERY_SUCCESS_RATE

    if not first_attempt_ok:
        record_event(app, comm_id, "failed")

        # ── Retry once ────────────────────────────────────────────────────────
        time.sleep(random.uniform(*RETRY_DELAY))
        retry_ok = random.random() < RETRY_SUCCESS_RATE

        if not retry_ok:
            record_event(app, comm_id, "permanently_failed")
            return
        # Retry succeeded → fall through to delivered

    record_event(app, comm_id, "delivered")

    # ── Open (60–75% of delivered) ────────────────────────────────────────────
    open_rate = random.uniform(OPEN_RATE_MIN, OPEN_RATE_MAX)
    time.sleep(random.uniform(*OPEN_DELAY))
    if random.random() > open_rate:
        return
    record_event(app, comm_id, "opened")

    # ── Click (10–30% of opened) ──────────────────────────────────────────────
    click_rate = random.uniform(CLICK_RATE_MIN, CLICK_RATE_MAX)
    time.sleep(random.uniform(*CLICK_DELAY))
    if random.random() > click_rate:
        return
    record_event(app, comm_id, "clicked")

    # ── Conversion attribution (25% of clicked) ───────────────────────────────
    if campaign_id and customer_id and random.random() < CONVERSION_RATE:
        simulate_attribution(app, comm_id, campaign_id, customer_id)


def simulate_campaign(app, comms: list):
    for comm in comms:
        t = threading.Thread(target=simulate_single, args=(app, comm), daemon=True)
        t.start()
