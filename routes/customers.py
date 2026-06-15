import csv
import io
import logging
from datetime import date, datetime
from flask import Blueprint, request, jsonify
from sqlalchemy import func, or_
from models import db, Customer, Order

logger = logging.getLogger(__name__)
customers_bp = Blueprint("customers", __name__)

VALID_TAGS = {"Loyal", "AtRisk", "Dormant", "New"}
VALID_SORT = {"totalSpend": Customer.total_spend, "lastOrderDate": Customer.last_order_date, "name": Customer.name}


def customer_to_dict(c: Customer, order_count: int = 0) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "phone": c.phone,
        "city": c.city,
        "totalSpend": float(c.total_spend or 0),
        "lastOrderDate": c.last_order_date.isoformat() if c.last_order_date else None,
        "tag": c.tag,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
        "orderCount": order_count,
    }


def classify_tag(total_spend: float, last_order_days_ago: int | None, order_count: int) -> str:
    if order_count <= 1:
        return "New"
    if last_order_days_ago is None or last_order_days_ago > 90:
        return "Dormant"
    if last_order_days_ago > 45 or (order_count < 3 and total_spend < 2000):
        return "AtRisk"
    return "Loyal"


@customers_bp.get("/customers")
def list_customers():
    search = request.args.get("search", "").strip()
    tag = request.args.get("tag", "").strip()
    sort_by = request.args.get("sortBy", "totalSpend")
    sort_order = request.args.get("sortOrder", "desc")
    try:
        page = int(request.args.get("page", 1))
        page_size = min(int(request.args.get("pageSize", 50)), 200)
    except (ValueError, TypeError):
        page, page_size = 1, 50

    order_count_sub = (
        db.session.query(Order.customer_id, func.count(Order.id).label("cnt"))
        .group_by(Order.customer_id)
        .subquery()
    )

    query = db.session.query(Customer, func.coalesce(order_count_sub.c.cnt, 0).label("order_count")).outerjoin(
        order_count_sub, Customer.id == order_count_sub.c.customer_id
    )

    if search:
        query = query.filter(
            or_(
                Customer.name.ilike(f"%{search}%"),
                Customer.email.ilike(f"%{search}%"),
                Customer.city.ilike(f"%{search}%"),
            )
        )
    if tag and tag in VALID_TAGS:
        query = query.filter(Customer.tag == tag)

    sort_col = VALID_SORT.get(sort_by, Customer.total_spend)
    if sort_order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    total = query.count()
    results = query.offset((page - 1) * page_size).limit(page_size).all()

    return jsonify({
        "customers": [customer_to_dict(c, cnt) for c, cnt in results],
        "total": total,
        "page": page,
        "pageSize": page_size,
    })


@customers_bp.post("/customers")
def create_customer():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    if not name or not email:
        return jsonify({"error": "Name and email are required"}), 400

    if Customer.query.filter_by(email=email).first():
        return jsonify({"error": "Email already exists"}), 409

    tag = data.get("tag", "New")
    if tag not in VALID_TAGS:
        tag = "New"

    c = Customer(
        name=name,
        email=email,
        phone=data.get("phone", ""),
        city=data.get("city", ""),
        total_spend=float(data.get("totalSpend", 0)),
        last_order_date=data.get("lastOrderDate") or None,
        tag=tag,
    )
    db.session.add(c)
    db.session.commit()
    return jsonify(customer_to_dict(c, 0)), 201


def _days_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        from datetime import date as date_cls
        d = date_cls.fromisoformat(str(date_str))
        return (date_cls.today() - d).days
    except Exception:
        return None


def _parse_rows_from_request() -> list[dict]:
    """Accept both JSON {rows:[...]} and raw CSV (multipart file or text/csv body)."""
    rows = []

    content_type = request.content_type or ""

    if "application/json" in content_type:
        data = request.get_json(silent=True) or {}
        rows = data.get("rows", [])
    elif "multipart/form-data" in content_type or "text/csv" in content_type:
        if "file" in request.files:
            raw = request.files["file"].read().decode("utf-8", errors="replace")
        else:
            raw = request.get_data(as_text=True)
        reader = csv.DictReader(io.StringIO(raw))
        for r in reader:
            r = {k.strip(): v.strip() for k, v in r.items() if k}
            rows.append({
                "name": r.get("name") or r.get("Name", ""),
                "email": r.get("email") or r.get("Email", ""),
                "phone": r.get("phone") or r.get("Phone", ""),
                "city": r.get("city") or r.get("City", ""),
                "totalSpend": r.get("totalSpend") or r.get("total_spend") or r.get("Total Spend", 0),
                "lastOrderDate": r.get("lastOrderDate") or r.get("last_order_date") or r.get("Last Order Date"),
                "tag": r.get("tag") or r.get("Tag", ""),
            })

    return rows


@customers_bp.post("/customers/import")
def import_customers():
    rows = _parse_rows_from_request()

    imported = 0
    skipped = 0
    errors = []

    for row in rows:
        try:
            email = (str(row.get("email") or "")).strip().lower()
            name = (str(row.get("name") or "")).strip()
            if not email or not name:
                skipped += 1
                continue
            if Customer.query.filter_by(email=email).first():
                skipped += 1
                continue

            total_spend = float(row.get("totalSpend") or 0)
            last_order_date_raw = row.get("lastOrderDate") or None
            days_ago = _days_since(last_order_date_raw)

            order_count_hint = int(row.get("orderCount") or (1 if total_spend > 0 else 0))
            provided_tag = (row.get("tag") or "").strip()
            tag = provided_tag if provided_tag in VALID_TAGS else classify_tag(total_spend, days_ago, order_count_hint)

            c = Customer(
                name=name,
                email=email,
                phone=str(row.get("phone") or ""),
                city=str(row.get("city") or ""),
                total_spend=total_spend,
                last_order_date=last_order_date_raw or None,
                tag=tag,
            )
            db.session.add(c)
            imported += 1
        except Exception as e:
            skipped += 1
            errors.append(f"Row {row.get('email', '?')}: {e}")

    db.session.commit()
    return jsonify({"imported": imported, "skipped": skipped, "errors": errors})


@customers_bp.get("/customers/<int:customer_id>")
def get_customer(customer_id: int):
    c = Customer.query.get_or_404(customer_id)
    order_count = Order.query.filter_by(customer_id=customer_id).count()
    return jsonify(customer_to_dict(c, order_count))


@customers_bp.put("/customers/<int:customer_id>")
def update_customer(customer_id: int):
    c = Customer.query.get_or_404(customer_id)
    data = request.get_json(silent=True) or {}

    if "name" in data:
        c.name = data["name"].strip()
    if "phone" in data:
        c.phone = data["phone"]
    if "city" in data:
        c.city = data["city"]
    if "totalSpend" in data:
        c.total_spend = float(data["totalSpend"])
    if "lastOrderDate" in data:
        c.last_order_date = data["lastOrderDate"] or None
    if "tag" in data and data["tag"] in VALID_TAGS:
        c.tag = data["tag"]

    db.session.commit()
    order_count = Order.query.filter_by(customer_id=customer_id).count()
    return jsonify(customer_to_dict(c, order_count))


@customers_bp.delete("/customers/<int:customer_id>")
def delete_customer(customer_id: int):
    from models import Communication, Receipt, Order as OrderModel
    c = Customer.query.get_or_404(customer_id)

    comm_ids = [comm.id for comm in Communication.query.filter_by(customer_id=customer_id).all()]
    if comm_ids:
        Receipt.query.filter(Receipt.communication_id.in_(comm_ids)).delete(synchronize_session=False)
        Communication.query.filter_by(customer_id=customer_id).delete(synchronize_session=False)

    OrderModel.query.filter_by(customer_id=customer_id).delete(synchronize_session=False)

    db.session.delete(c)
    db.session.commit()
    return jsonify({"success": True, "message": "Customer deleted"})
