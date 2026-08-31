import os
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify
from glue_up_api import GlueUpAPI

# Load environment variables from .env file for local development
load_dotenv()

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/inactive-contacts")
def api_get_inactive_contacts():
    try:
        api = GlueUpAPI()
        flagged_contacts = api.get_inactive_contacts()
        return jsonify({"status": "success", "data": flagged_contacts})
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    # host='0.0.0.0' is required for Docker port forwarding to work
    app.run(host="0.0.0.0", port=5000, debug=True)
