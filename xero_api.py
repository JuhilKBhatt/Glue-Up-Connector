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
                
            total_amount = float(item.get('amount', 0))
            quantity = float(item.get('quantity', 1.0))
            
            # Xero multiplies unit_amount by quantity. Xero supports up to 4 decimal places for unit amounts
            # to prevent rounding errors on the final line total.
            unit_amount = total_amount / quantity if quantity != 0 else total_amount

            line_items.append(
                LineItem(
                    description=item.get('description', 'Membership Fee'),
                    unit_amount=round(unit_amount, 4),
                    quantity=quantity,
                    account_code=account_code,
                    tax_type="OUTPUT"  # Must exactly match a Xero tax code (e.g., OUTPUT for standard sales tax)
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

        # In a strict blueprint, we query by Email if available. If not, use name.
        contact_email = invoice_data.get('contact_email')
        reference_str = f"GlueUp-{invoice_data.get('invoice_id')}"
        
        from xero_python.accounting import LineAmountTypes, Payment, Account
        
        # Map Glue Up Status to Xero Status
        glue_up_status = invoice_data.get('status', 'Unpaid')
        
        if glue_up_status in ['Paid', 'Unpaid', 'Overdue']:
            xero_status = "AUTHORISED"  # Xero requires invoices to be AUTHORISED before payments can be applied
        else:
            xero_status = "DRAFT"

        # Helper function to perform the sync with duplicate check and payments
        def attempt_sync():
            # 0. Check for existing Contact in Xero to prevent duplicates
            contact_obj = Contact(name=contact_display_name)
            try:
                # Blueprint: Query Xero's GET /Contacts endpoint using the customer's email address or Glue Up ID.
                if contact_email:
                    where_clause = f'EmailAddress=="{contact_email}"'
                else:
                    safe_name = contact_display_name.replace('"', '\\"')
                    where_clause = f'Name=="{safe_name}"'
                    
                existing_contacts = accounting_api.get_contacts(
                    xero_tenant_id=xero_tenant_id,
                    where=where_clause
                )
                if existing_contacts and existing_contacts.contacts:
                    # Blueprint: If they exist, retrieve their Xero ContactID.
                    contact_obj = Contact(contact_id=existing_contacts.contacts[0].contact_id)
                else:
                    # Blueprint: If they do not exist, send a POST /Contacts request to create them and store the new ContactID.
                    new_contact = accounting_api.create_contacts(
                        xero_tenant_id=xero_tenant_id,
                        contacts={"contacts": [Contact(name=contact_display_name, email_address=contact_email)]}
                    )
                    contact_obj = Contact(contact_id=new_contact.contacts[0].contact_id)
            except Exception as e:
                print(f"Warning: Failed to query/create contact {contact_display_name}: {str(e)}")
                # Fallback to just passing the name and letting Xero decide
                pass
            
            # 1. Blueprint: Check for Duplicates
            existing_invoices = accounting_api.get_invoices(
                xero_tenant_id=xero_tenant_id,
                where=f'Reference=="{reference_str}"'
            )
            if existing_invoices and existing_invoices.invoices:
                return False, "Invoice is already synced to Xero!"
                
            # 2. Blueprint: Map the Invoice Data
            xero_invoice = Invoice(
                type="ACCREC", 
                contact=contact_obj, 
                line_items=line_items,
                date=parsed_date,
                due_date=parsed_date,
                reference=reference_str,
                line_amount_types=LineAmountTypes.INCLUSIVE,
                status=xero_status
            )
                
            # 3. Push to Xero
            created_invoices = accounting_api.create_invoices(
                xero_tenant_id=xero_tenant_id, 
                invoices={"invoices": [xero_invoice]}
            )
            invoice_id = created_invoices.invoices[0].invoice_id
            
            # 4. Blueprint: Handling Payments (Sync the payment so the invoice doesn't sit as Awaiting Payment)
            if glue_up_status == 'Paid':
                try:
                    # Find a Bank/Clearing Account to apply the payment to
                    bank_accounts = accounting_api.get_accounts(
                        xero_tenant_id=xero_tenant_id,
                        where='Type=="BANK"'
                    )
                    if bank_accounts and bank_accounts.accounts:
                        clearing_account_id = bank_accounts.accounts[0].account_id
                        
                        payment = Payment(
                            invoice=Invoice(invoice_id=invoice_id),
                            account=Account(account_id=clearing_account_id),
                            amount=sum(float(item.get('amount', 0)) for item in invoice_data.get('items', [])),
                            date=parsed_date
                        )
                        accounting_api.create_payment(
                            xero_tenant_id=xero_tenant_id,
                            payment=payment
                        )
                        return True, "Invoice successfully pushed to Xero and marked as PAID!"
                except Exception as payment_err:
                    print(f"Payment sync failed: {payment_err}")
                    return True, "Invoice pushed to Xero, but Payment sync failed (missing clearing account)."
                    
            return True, "Invoice successfully pushed to Xero!"

        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                success, msg = attempt_sync()
                break
            except Exception as api_err:
                # Blueprint: Rate Limiting (Exponential Backoff)
                if "429" in str(api_err):
                    if attempt < max_retries - 1:
                        backoff = 2 ** attempt
                        print(f"Xero Rate Limit Hit (429). Retrying in {backoff} seconds...")
                        time.sleep(backoff)
                        continue
                    else:
                        # Blueprint: Dead Letter Queue (Alert team)
                        print("CRITICAL: Invoice failed to sync after retries. Pushing to Dead Letter Queue (DLQ)...")
                        raise api_err
                        
                # Blueprint: Authentication (Auto token refresh)
                elif "401" in str(api_err) or "Unauthorized" in str(api_err):
                    print("Xero token expired, attempting refresh...")
                    api_client.refresh_oauth2_token()
                    # Will retry on next loop iteration
                else:
                    raise api_err
        
        return jsonify({
            "status": "success" if success else "error",
            "message": msg
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Xero API Error: {str(e)}"}), 500
