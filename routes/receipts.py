import logging
from flask import Blueprint, request, jsonify
from models import db, Communication, Receipt

logger = logging.getLogger(__name__)
receipts_bp = Blueprint("receipts", __name__)

STATUS_ORDER = ["queued", "sent", "delivered", "opened", "clicked", "failed"]


def higher_status(current: str, incoming: str) -> str:
    if incoming == "failed":
        return "failed"
    try:
        current_idx = STATUS_ORDER.index(current)
        incoming_idx = STATUS_ORDER.index(incoming)
        return incoming if incoming_idx > current_idx else current
    except ValueError:
        return current


@receipts_bp.post("/receipts")
def create_receipt():
    data = request.get_json(silent=True) or {}
    comm_id = data.get("communicationId")
    event_type = data.get("eventType")

    if not comm_id or not event_type:
        return jsonify({"error": "communicationId and eventType are required"}), 400

    valid_events = {"sent", "delivered", "opened", "clicked", "failed"}
    if event_type not in valid_events:
        return jsonify({"error": f"eventType must be one of {sorted(valid_events)}"}), 400

    receipt = Receipt(communication_id=comm_id, event_type=event_type)
    db.session.add(receipt)

    comm = Communication.query.get(comm_id)
    if comm:
        new_status = higher_status(comm.status, event_type)
        if new_status != comm.status:
            comm.status = new_status

    db.session.commit()
    return jsonify({"success": True, "message": "Receipt recorded"})
