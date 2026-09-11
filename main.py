import os
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request, Response, session
from glue_up_api import GlueUpAPI
from invoice_processor import invoice_bp
from xero_api import xero_bp

# Load environment variables from .env file for local development
load_dotenv()

app = Flask(__name__)
# Secret key required for Flask sessions (used by Xero OAuth)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-default-key-for-dev")

app.register_blueprint(invoice_bp)
app.register_blueprint(xero_bp)

@app.before_request
def require_password():
    # Only protect if SITE_PASSWORD is set in the environment (e.g., on Render)
    expected_password = os.environ.get('SITE_PASSWORD')
    if expected_password:
        auth = request.authorization
        # Standard HTTP Basic Auth check
        if not auth or auth.password != expected_password:
            return Response(
                'Access Denied. Please enter the correct password to view this site.', 
                401,
                {'WWW-Authenticate': 'Basic realm="Login Required"'}
            )

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dedupe")
def dedupe():
    return render_template("dedupe.html")

@app.route("/archiving")
def archiving():
    return render_template("archiving.html")

@app.route("/events")
def events_module():
    return render_template("events.html")

@app.route("/api/events-list")
def api_get_all_events():
    try:
        api = GlueUpAPI()
        events = api.get_all_events()
        return jsonify({"status": "success", "data": events})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/event/<event_id>/attendees")
def api_get_event_attendees(event_id):
    try:
        api = GlueUpAPI()
        attendees = api.get_event_attendees(event_id)
        return jsonify({"status": "success", "data": attendees})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/inactive-contacts")
def api_get_inactive_contacts():
    try:
        api = GlueUpAPI()
        flagged_contacts = api.find_inactive_contacts()
        return jsonify({"status": "success", "data": flagged_contacts})
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/delete-contact/<contact_id>", methods=["POST"])
def api_delete_contact(contact_id):
    try:
        api = GlueUpAPI()
        success, message = api.delete_contact(contact_id)
        if success:
            return jsonify({"status": "success", "message": message})
        else:
            return jsonify({"status": "error", "message": message}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/webhook/glueup", methods=["POST"])
def glue_up_webhook():
    """
    Blueprint: Webhook Middleware Endpoint
    Configured in Glue Up to receive real-time POST payloads (e.g. Order Paid or Invoice Created).
    """
    payload = request.json
    # Blueprint: Always validate the webhook signature in your middleware 
    # to ensure the payload actually came from Glue Up.
    signature = request.headers.get("X-GlueUp-Signature")
    if not signature:
        return jsonify({"status": "error", "message": "Missing Signature"}), 401
        
    # Process the webhook async or push to a queue (e.g., Celery) to avoid timeout.
    # If the payload contains an invoice, we trigger xero_api.sync_invoice(id).
    
    # Example handling:
    event_type = payload.get("event")
    if event_type == "invoice.created" or event_type == "order.paid":
        # Queue for syncing...
        pass
        
    return jsonify({"status": "received"}), 200

if __name__ == "__main__":
    # host='0.0.0.0' is required for Docker port forwarding to work
    app.run(host="0.0.0.0", port=5001, debug=True)
