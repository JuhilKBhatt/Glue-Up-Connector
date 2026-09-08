import os
from flask import Blueprint, redirect, request, session, url_for, jsonify
from xero_python.accounting import AccountingApi, Invoice, LineItem, Contact
from xero_python.api_client import ApiClient, serialize
from xero_python.api_client.configuration import Configuration
from xero_python.api_client.oauth2 import OAuth2Token
from xero_python.identity import IdentityApi
from xero_python.utils import get_value

xero_bp = Blueprint('xero_bp', __name__)

# Xero Configuration
# Make sure to set these in your .env / Render environment variables
CLIENT_ID = os.environ.get("XERO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET")
# Example: http://localhost:5001/xero/callback for local dev
REDIRECT_URI = os.environ.get("XERO_REDIRECT_URI", "http://localhost:5001/xero/callback")

xero_config = Configuration(
    debug=os.environ.get("DEBUG", "false").lower() == "true",
    oauth2_token=OAuth2Token.client_credentials(CLIENT_ID, CLIENT_SECRET),
)
api_client = ApiClient(
    Configuration(
        oauth2_token=None
    ),
    pool_threads=1,
)

@xero_bp.route("/xero/login")
def login():
    """Redirects the user to Xero to authorize the app."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return "XERO_CLIENT_ID and XERO_CLIENT_SECRET not set in environment.", 500
        
    # We request scopes for offline_access (to get a refresh token), and accounting.transactions
    scope = "offline_access accounting.transactions accounting.contacts"
    
    # Generate the authorization URL
    auth_url = (
        f"https://login.xero.com/identity/connect/authorize?"
        f"response_type=code&"
        f"client_id={CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"scope={scope}&"
        f"state=secret_state_123" # In production, generate a secure random string
    )
    return redirect(auth_url)

@xero_bp.route("/xero/callback")
def oauth_callback():
    """Handles the callback from Xero after user authorization."""
    code = request.args.get("code")
    if not code:
        return "Error: No code provided by Xero.", 400

    # Exchange the code for an access token
    try:
        # In the real xero-python SDK, we use the oauth2 mechanism to exchange the code
        # We manually configure the token exchange since the SDK wrapper is a bit complex
        token = api_client.get_oauth2_token(
            CLIENT_ID, 
            CLIENT_SECRET, 
            REDIRECT_URI, 
            code
        )
        
        # Save token to session (in a real app, save to a DB if it's a background worker)
        session['xero_token'] = token
        
        # We also need the tenant ID to make API calls
        api_client.configuration.oauth2_token = OAuth2Token(**token)
        identity_api = IdentityApi(api_client)
        connections = identity_api.get_connections()
        
        if connections:
            session['xero_tenant_id'] = connections[0].tenant_id
            
        return "Successfully connected to Xero! You can now close this tab and return to the dashboard."
    except Exception as e:
        return f"Failed to authenticate with Xero: {str(e)}", 500

@xero_bp.route("/api/xero/sync", methods=["POST"])
def sync_invoice():
    """Endpoint triggered by the frontend to push a single invoice to Xero."""
    payload = request.json
    invoice_data = payload.get('invoice')
    
    if not invoice_data:
        return jsonify({"status": "error", "message": "No invoice data provided"}), 400
        
    xero_token = session.get('xero_token')
    xero_tenant_id = session.get('xero_tenant_id')
    
    # If not authorized yet, tell the frontend
    if not xero_token or not xero_tenant_id:
        return jsonify({
            "status": "error", 
            "message": "Not connected to Xero", 
            "auth_required": True,
            "login_url": url_for('xero_bp.login')
        }), 401

    try:
        # Rehydrate the API client with the token from the session
        api_client.configuration.oauth2_token = OAuth2Token(**xero_token)
        accounting_api = AccountingApi(api_client)
        
        # Build the Xero LineItems
        line_items = []
        for item in invoice_data.get('items', []):
            # IMPORTANT: Here we use a generic AccountCode "200". 
            # You must map your specific accounts to Xero AccountCodes (integers like 200, 400).
            # e.g., if item['type'] == 'Membership Application': account_code = '410'
            account_code = "200" 
            
            line_items.append(
                LineItem(
                    description=item.get('description', 'Membership Fee'),
                    unit_amount=float(item.get('amount', 0)),
                    quantity=1.0,
                    account_code=account_code
                )
            )
            
        # Build the Xero Invoice
        # Using a dummy contact name since Glue Up payload doesn't easily expose the member's company name in the flattened items yet
        xero_invoice = Invoice(
            type="ACCREC", 
            contact=Contact(name="AJBCC Member (Synced via Glue Up)"), 
            line_items=line_items,
            date=invoice_data.get('date'),
            due_date=invoice_data.get('date'), # Default due date
            reference=f"GlueUp-{invoice_data.get('invoice_id')}",
            status="DRAFT" # Safe practice: create as DRAFT so you can review in Xero
        )
        
        # Push to Xero
        created_invoices = accounting_api.create_invoices(
            xero_tenant_id=xero_tenant_id, 
            invoices={"invoices": [xero_invoice]}
        )
        
        return jsonify({
            "status": "success",
            "message": "Invoice successfully pushed to Xero as DRAFT!"
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Xero API Error: {str(e)}"}), 500
