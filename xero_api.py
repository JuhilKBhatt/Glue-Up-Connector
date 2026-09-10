import os
from flask import Blueprint, redirect, request, session, url_for, jsonify
from xero_python.accounting import AccountingApi, Invoice, LineItem, Contact
from xero_python.api_client import ApiClient, serialize
from xero_python.api_client.configuration import Configuration
from xero_python.api_client.oauth2 import OAuth2Token
from xero_python.identity import IdentityApi

xero_bp = Blueprint('xero_bp', __name__)

# Xero Configuration
# Make sure to set these in your .env / Render environment variables
CLIENT_ID = os.environ.get("XERO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET")
# Example: http://localhost:5001/xero/callback for local dev
REDIRECT_URI = os.environ.get("XERO_REDIRECT_URI", "http://localhost:5001/xero/callback")

# Dummy token saver and getter functions required by xero-python's set_oauth2_token
def dummy_token_getter():
    return session.get('xero_token')

def dummy_token_saver(token):
    session['xero_token'] = token

api_client = ApiClient(
    Configuration(
        oauth2_token=OAuth2Token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
    ),
    pool_threads=1,
    oauth2_token_getter=dummy_token_getter,
    oauth2_token_saver=dummy_token_saver
)

@xero_bp.route("/xero/login")
def login():
    """Redirects the user to Xero to authorize the app."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return "XERO_CLIENT_ID and XERO_CLIENT_SECRET not set in environment.", 500
        
    # We request granular scopes replacing the deprecated accounting.transactions
    scope = "openid profile email offline_access accounting.invoices accounting.contacts"
    
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

    try:
        import requests
        import base64
        
        # Manually exchange the code to avoid xero-python OAuth lib headaches
        auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
        b64_auth = base64.b64encode(auth_string.encode()).decode()
        
        token_response = requests.post(
            "https://identity.xero.com/connect/token",
            headers={
                "Authorization": f"Basic {b64_auth}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI
            }
        )
        
        if not token_response.ok:
            return f"Error exchanging code: {token_response.text}", 400
            
        token = token_response.json()
        
        # Save token to session (in a real app, save to a DB if it's a background worker)
        session['xero_token'] = token
        
        # We also need the tenant ID to make API calls
        api_client.configuration.oauth2_token = OAuth2Token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        api_client.set_oauth2_token(token)
        
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
        api_client.configuration.oauth2_token = OAuth2Token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        api_client.set_oauth2_token(xero_token)
        
        accounting_api = AccountingApi(api_client)
        
        # Build the Xero LineItems
        line_items = []
        for item in invoice_data.get('items', []):
            item_type = item.get('type', 'Standard')
            
            # Xero Account Mapping
            if item_type == 'Membership Application':
                account_code = "2004"
            elif item_type == 'Additional Member':
                account_code = "2005"
            else:
                account_code = "200" # Default fallback (Sales)
                
            line_items.append(
                LineItem(
                    description=item.get('description', 'Membership Fee'),
                    unit_amount=float(item.get('amount', 0)),
                    quantity=1.0,
                    account_code=account_code
                )
            )
            
        # Convert date string to python datetime object for Xero serialization
        from datetime import datetime
        raw_date = invoice_data.get('date')
        try:
            # Attempt to parse YYYY-MM-DD
            parsed_date = datetime.strptime(raw_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            # Fallback to current date if parsing fails
            parsed_date = datetime.now()

        # Determine the best Contact Name to use in Xero
        company_name = invoice_data.get('company_name')
        person_name = invoice_data.get('contact_name')
        
        # Xero identifies contacts primarily by Name. Prefer company name if B2B.
        contact_display_name = company_name if company_name else (person_name if person_name else "AJBCC Member (Synced via Glue Up)")

        # Build the Xero Invoice
        xero_invoice = Invoice(
            type="ACCREC", 
            contact=Contact(name=contact_display_name), 
            line_items=line_items,
            date=parsed_date,
            due_date=parsed_date, # Default due date
            reference=f"GlueUp-{invoice_data.get('invoice_id')}",
            status="DRAFT" # Safe practice: create as DRAFT so you can review in Xero
        )
        
        # Push to Xero
        try:
            created_invoices = accounting_api.create_invoices(
                xero_tenant_id=xero_tenant_id, 
                invoices={"invoices": [xero_invoice]}
            )
        except Exception as api_err:
            # If the token expired (401), automatically refresh and retry
            if "401" in str(api_err) or "Unauthorized" in str(api_err):
                print("Xero token expired, attempting refresh...")
                api_client.refresh_oauth2_token()
                # Retry push
                created_invoices = accounting_api.create_invoices(
                    xero_tenant_id=xero_tenant_id, 
                    invoices={"invoices": [xero_invoice]}
                )
            else:
                raise api_err
        
        return jsonify({
            "status": "success",
            "message": "Invoice successfully pushed to Xero as DRAFT!"
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Xero API Error: {str(e)}"}), 500
