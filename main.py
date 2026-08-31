import os
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify

# Load environment variables from .env file for local development
load_dotenv()

GLUE_UP_API_URL = os.environ.get("GLUE_UP_API_URL", "https://api.glueup.com/v1")
GLUE_UP_API_KEY = os.environ.get("GLUE_UP_API_KEY")
GLUE_UP_API_SECRET = os.environ.get("GLUE_UP_API_SECRET")

app = Flask(__name__)

def get_headers():
    if not GLUE_UP_API_KEY or not GLUE_UP_API_SECRET:
        raise ValueError("GLUE_UP_API_KEY or GLUE_UP_API_SECRET is not set in .env file.")
    return {
        "Authorization": f"Bearer {GLUE_UP_API_KEY}",
        "Content-Type": "application/json"
    }

def fetch_events():
    """Example function to fetch events from Glue Up."""
    url = f"{GLUE_UP_API_URL}/events"
    try:
        # Note: If credentials are not set, get_headers will raise ValueError
        response = requests.get(url, headers=get_headers())
        response.raise_for_status()
        events = response.json()
        return events
    except Exception as e:
        print(f"Error fetching events: {e}")
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/events")
def api_get_events():
    try:
        events = fetch_events()
        if events is not None:
            return jsonify({"status": "success", "data": events})
        else:
            return jsonify({"status": "error", "message": "Failed to fetch events from API."}), 500
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400

if __name__ == "__main__":
    # host='0.0.0.0' is required for Docker port forwarding to work
    app.run(host="0.0.0.0", port=5000, debug=True)
