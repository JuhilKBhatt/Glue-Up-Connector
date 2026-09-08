import os
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request, Response
from glue_up_api import GlueUpAPI
from invoice_processor import invoice_bp

# Load environment variables from .env file for local development
load_dotenv()

app = Flask(__name__)
app.register_blueprint(invoice_bp)

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

if __name__ == "__main__":
    # host='0.0.0.0' is required for Docker port forwarding to work
    app.run(host="0.0.0.0", port=5001, debug=True)
