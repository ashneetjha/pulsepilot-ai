import logging
import random
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

FIRST_NAMES = [
    "Arjun", "Priya", "Rahul", "Anjali", "Vikram", "Meera", "Suresh", "Kavya", "Rohit", "Deepa",
    "Karthik", "Pooja", "Aditya", "Sneha", "Sanjay", "Nisha", "Rajesh", "Divya", "Manoj", "Ananya",
    "Vivek", "Lakshmi", "Nikhil", "Sunita", "Ganesh", "Revathi", "Harish", "Padma", "Ramesh", "Geetha",
    "Naveen", "Shilpa", "Prasad", "Usha", "Vijay", "Swati", "Ashok", "Rekha", "Balaji", "Hema",
    "Dinesh", "Chitra", "Sunil", "Asha", "Mahesh", "Saranya", "Ajay", "Renu", "Rajiv", "Bharati",
]
LAST_NAMES = [
    "Kumar", "Sharma", "Patel", "Reddy", "Singh", "Nair", "Pillai", "Rao", "Gupta", "Iyer",
    "Krishnan", "Verma", "Shah", "Mehta", "Joshi", "Naidu", "Menon", "Desai", "Bhat", "Agarwal",
    "Malhotra", "Kapoor", "Bansal", "Saxena", "Tripathi", "Pandey", "Mishra", "Srivastava", "Tiwari", "Dubey",
]
CITIES = ["Chennai", "Bangalore", "Mumbai", "Delhi", "Hyderabad"]


def days_ago(n: int) -> date:
    return date.today() - timedelta(days=n)


def classify_tag(total_spend: float, last_order_days_ago, order_count: int) -> str:
    if order_count <= 1:
        return "New"
    if last_order_days_ago is None or last_order_days_ago > 90:
        return "Dormant"
    if last_order_days_ago > 45 or (order_count < 3 and total_spend < 2000):
        return "AtRisk"
    return "Loyal"


def _seed_customers(db, User, Customer, Order) -> bool:
    """Returns True if customers were freshly seeded (new DB), False if skipped."""
    existing_count = Customer.query.count()
    if existing_count >= 50:
        logger.info("Customers already seeded, skipping customer seed")
        return False

    logger.info("Seeding 100 demo customers...")

    if User.query.count() == 0:
        import bcrypt
        pw_hash = bcrypt.hashpw(b"demo1234", bcrypt.gensalt()).decode("utf-8")
        demo_user = User(email="demo@xenopilot.ai", password_hash=pw_hash)
        db.session.add(demo_user)
        db.session.flush()
        logger.info("Created demo user: demo@xenopilot.ai / demo1234")

    # Seed with a controlled distribution to ensure varied segment sizes
    # Target: ~28 Dormant, ~35 AtRisk, ~25 Loyal, ~12 New
    profiles = []

    # New customers (0–1 orders)
    for i in range(13):
        order_count = random.choice([0, 1])
        last_days = None if order_count == 0 else random.randint(5, 40)
        spend = random.randint(0, 1500) if order_count > 0 else 0.0
        profiles.append({"order_count": order_count, "last_days": last_days, "spend": spend})

    # Dormant customers (2+ orders, last order > 90 days)
    for i in range(28):
        order_count = random.randint(2, 7)
        last_days = random.randint(91, 200)
        spend = random.randint(1000, 8000)
        profiles.append({"order_count": order_count, "last_days": last_days, "spend": spend})

    # At-Risk customers (2+ orders, last order 46–90 days)
    for i in range(34):
        order_count = random.randint(2, 5)
        last_days = random.randint(46, 90)
        spend = random.randint(800, 6000)
        profiles.append({"order_count": order_count, "last_days": last_days, "spend": spend})

    # Loyal customers (2+ orders, last order < 45 days, good spend)
    for i in range(25):
        order_count = random.randint(3, 12)
        last_days = random.randint(2, 44)
        spend = random.randint(2000, 15000)
        profiles.append({"order_count": order_count, "last_days": last_days, "spend": spend})

    random.shuffle(profiles)

    for i, p in enumerate(profiles):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        city = random.choice(CITIES)
        order_count = p["order_count"]
        last_order_days = p["last_days"]
        total_spend = float(p["spend"])

        tag = classify_tag(total_spend, last_order_days, order_count)
        last_order_date = days_ago(last_order_days) if last_order_days is not None else None

        customer = Customer(
            name=f"{first} {last}",
            email=f"{first.lower()}.{last.lower()}{i}@{city.lower()}.in",
            phone=f"+91{random.randint(7000000000, 9999999999)}",
            city=city,
            total_spend=total_spend,
            last_order_date=last_order_date,
            tag=tag,
        )
        db.session.add(customer)
        db.session.flush()

        if order_count > 0 and last_order_days is not None and total_spend > 0:
            per_order = max(100, int(total_spend / order_count))
            for j in range(order_count):
                is_last = j == order_count - 1
                amt = max(100, random.randint(int(per_order * 0.6), int(per_order * 1.4)))
                day_offset = last_order_days if is_last else random.randint(last_order_days, last_order_days + random.randint(20, 80))
                db.session.add(Order(
                    customer_id=customer.id,
                    amount=amt,
                    order_date=days_ago(day_offset),
                ))

    db.session.commit()
    logger.info("Customer seed complete: 100 customers with orders")
    return True


def _seed_demo_campaigns(db, Campaign, Communication, Customer, Attribution, Order):
    """Seed realistic completed demo campaigns with varied audience sizes."""
    if Campaign.query.count() > 0:
        logger.info("Campaigns already exist, skipping campaign seed")
        return

    logger.info("Seeding demo campaigns...")
    now = datetime.utcnow()

    demo_campaigns = [
        {
            "goal": "Win Back Dormant Customers — Summer Sale",
            "segment_name": "Dormant Customers",
            "channel": "WhatsApp",
            "message": "Hey! We miss you. It's been a while since your last order. Come back with 20% off — this offer expires in 48 hours!",
            "audience_tag": "Dormant",
            "offer": "20% off next order",
            "days_ago": 8,
        },
        {
            "goal": "Retain At-Risk Customers — Loyalty Reward",
            "segment_name": "At-Risk Customers",
            "channel": "Email",
            "message": "We value your loyalty. As a thank-you, here's an exclusive 15% discount on your next order — valid for 7 days.",
            "audience_tag": "AtRisk",
            "offer": "15% loyalty discount",
            "days_ago": 5,
        },
        {
            "goal": "Premium Upsell for VIP Customers",
            "segment_name": "Loyal Customers",
            "channel": "Push",
            "message": "You're in our VIP club! Get early access to our new premium collection — exclusive pricing for 72 hours only.",
            "audience_tag": "Loyal",
            "offer": "VIP early access",
            "days_ago": 3,
        },
        {
            "goal": "New Customer First-Purchase Activation",
            "segment_name": "New Customers",
            "channel": "SMS",
            "message": "Welcome to XPilot Store! Complete your first order today and get ₹200 off. Use code WELCOME200 at checkout.",
            "audience_tag": "New",
            "offer": "₹200 first-purchase discount",
            "days_ago": 12,
        },
        {
            "goal": "Flash Sale — Re-engage Dormant Segment",
            "segment_name": "Dormant Customers",
            "channel": "Email",
            "message": "🔥 48-hour flash sale! Up to 30% off storewide. We saved your cart — shop now before stocks run out.",
            "audience_tag": "Dormant",
            "offer": "Up to 30% off storewide",
            "days_ago": 18,
        },
    ]

    for cfg in demo_campaigns:
        customers = (
            Customer.query.filter(Customer.tag == cfg["audience_tag"]).all()
            if cfg["audience_tag"] != "All"
            else Customer.query.all()
        )
        if not customers:
            continue

        created_at = now - timedelta(days=cfg["days_ago"])
        campaign = Campaign(
            goal=cfg["goal"],
            segment_name=cfg["segment_name"],
            channel=cfg["channel"],
            message=cfg["message"],
            audience_tag=cfg["audience_tag"],
            offer=cfg["offer"],
            status="completed",
        )
        campaign.created_at = created_at
        db.session.add(campaign)
        db.session.flush()

        for cust in customers:
            r = random.random()
            if r < 0.07:
                status = "permanently_failed"
            elif r < 0.15:
                status = "sent"
            elif r < 0.35:
                status = "delivered"
            elif r < 0.68:
                status = "opened"
            else:
                status = "clicked"

            comm = Communication(
                campaign_id=campaign.id,
                customer_id=cust.id,
                status=status,
            )
            comm.created_at = created_at
            comm.updated_at = created_at + timedelta(seconds=random.randint(3, 30))
            db.session.add(comm)
            db.session.flush()

            if status == "clicked" and random.random() < 0.25:
                revenue = float(random.randint(500, 5000))
                attribution = Attribution(
                    campaign_id=campaign.id,
                    customer_id=cust.id,
                    communication_id=comm.id,
                    attributed_revenue=revenue,
                )
                attribution.created_at = comm.updated_at
                db.session.add(attribution)

                order = Order(
                    customer_id=cust.id,
                    amount=revenue,
                    order_date=comm.updated_at.date(),
                )
                db.session.add(order)

        db.session.commit()
        logger.info(f"  Seeded campaign '{cfg['goal']}' — {len(customers)} audience")

    logger.info("Demo campaign seed complete")


def seed_database():
    from models import db, User, Customer, Order, Campaign, Communication, Attribution

    _seed_customers(db, User, Customer, Order)
    _seed_demo_campaigns(db, Campaign, Communication, Customer, Attribution, Order)
