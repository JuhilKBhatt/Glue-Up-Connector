import os
from flask import Blueprint, redirect, request, session, url_for, jsonify
from xero_python.accounting import AccountingApi, Invoice, Invoices, LineItem, Contact, Address, Phone, Payment, Account, LineAmountTypes
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
REDIRECT_URI = os.environ.get("XERO_REDIRECT_URI")

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

def is_scope_error(exc):
    """Detects whether a Xero API exception was caused by missing or insufficient OAuth scopes."""
    if not exc:
        return False
    exc_str = str(exc).lower()
    if "insufficient_scope" in exc_str or "authorizationunsuccessful" in exc_str:
        return True
    if hasattr(exc, 'status') and exc.status == 401:
        headers_str = str(getattr(exc, 'headers', '')).lower()
        body_str = str(getattr(exc, 'body', '')).lower()
        if "insufficient_scope" in headers_str or "insufficient_scope" in body_str:
            return True
        if "authorizationunsuccessful" in body_str:
            return True
    return False

class ScopeUpgradeRequiredException(Exception):
    """Raised when Xero API operation requires user to re-authorize with upgraded scopes."""
    pass

@xero_bp.route("/xero/login")
def login():
    """Redirects the user to Xero to authorize the app with granular permissions."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return "XERO_CLIENT_ID and XERO_CLIENT_SECRET not set in environment.", 500
        
    # Request granular scopes required for Invoices, Contacts, Bank Accounts, and Payments
    scope = "openid profile email offline_access accounting.invoices accounting.contacts accounting.settings accounting.payments accounting.banktransactions"
    
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

def verify_xero_connection():
    """
    Actively checks whether there is an authentic, valid, working Xero OAuth connection.
    Tests token validity against Xero's Identity API and refreshes expired tokens.
    Returns: dict(connected=bool, tenant_id=str, tenant_name=str, error=str)
    """
    xero_token = session.get('xero_token')
    xero_tenant_id = session.get('xero_tenant_id')
    
    if not xero_token or not xero_tenant_id:
        return {
            "connected": False,
            "tenant_id": None,
            "tenant_name": None,
            "error": "Not authenticated with Xero. Please click 'Connect to Xero' to authenticate."
        }
        
    try:
        api_client.configuration.oauth2_token = OAuth2Token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        api_client.set_oauth2_token(xero_token)
        
        identity_api = IdentityApi(api_client)
        connections = identity_api.get_connections()
        
        if not connections:
            session.pop('xero_token', None)
            session.pop('xero_tenant_id', None)
            session.pop('xero_tenant_name', None)
            return {
                "connected": False,
                "tenant_id": None,
                "tenant_name": None,
                "error": "No connected Xero organization found. Authorization was cancelled or revoked."
            }
            
        matching_tenant = next((c for c in connections if c.tenant_id == xero_tenant_id), connections[0])
        session['xero_tenant_id'] = matching_tenant.tenant_id
        tenant_name = getattr(matching_tenant, 'tenant_name', None) or "Connected Organization"
        session['xero_tenant_name'] = tenant_name

        # Check if the active token has payment-enabled scopes
        token_scope = xero_token.get('scope', '')
        if isinstance(token_scope, str):
            token_scopes = token_scope.split()
        elif isinstance(token_scope, (list, tuple)):
            token_scopes = list(token_scope)
        else:
            token_scopes = []
        if token_scopes and not any(s in token_scopes for s in ['accounting.payments', 'accounting.transactions']):
            return {
                "connected": False,
                "tenant_id": matching_tenant.tenant_id,
                "tenant_name": tenant_name,
                "error": "Your Xero connection permissions need to be updated to support Payments and Bank Accounts. Please click 'Log In & Authorize Xero' to authorize the updated scopes."
            }
        
        return {
            "connected": True,
            "tenant_id": matching_tenant.tenant_id,
            "tenant_name": tenant_name,
            "error": None
        }
    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "unauthorized" in err_str.lower():
            try:
                refreshed = api_client.refresh_oauth2_token()
                session['xero_token'] = refreshed
                identity_api = IdentityApi(api_client)
                connections = identity_api.get_connections()
                if connections:
                    session['xero_tenant_id'] = connections[0].tenant_id
                    tenant_name = getattr(connections[0], 'tenant_name', 'Connected Organization')
                    session['xero_tenant_name'] = tenant_name
                    return {
                        "connected": True,
                        "tenant_id": connections[0].tenant_id,
                        "tenant_name": tenant_name,
                        "error": None
                    }
            except Exception as refresh_err:
                session.pop('xero_token', None)
                session.pop('xero_tenant_id', None)
                session.pop('xero_tenant_name', None)
                return {
                    "connected": False,
                    "tenant_id": None,
                    "tenant_name": None,
                    "error": f"Xero token expired and refresh failed: {str(refresh_err)}"
                }
        
        session.pop('xero_token', None)
        session.pop('xero_tenant_id', None)
        session.pop('xero_tenant_name', None)
        return {
            "connected": False,
            "tenant_id": None,
            "tenant_name": None,
            "error": f"Xero connection verification failed: {err_str}"
        }

@xero_bp.route("/xero/callback")
def oauth_callback():
    """Handles the callback from Xero after user authorization or cancellation."""
    error = request.args.get("error")
    error_desc = request.args.get("error_description", "")
    
    # If the user cancelled or denied access on the Xero authorization screen
    if error:
        session.pop('xero_token', None)
        session.pop('xero_tenant_id', None)
        session.pop('xero_tenant_name', None)
        return redirect(f"/invoices?xero_error={error}&xero_msg={error_desc or 'Login was cancelled by user'}")

    code = request.args.get("code")
    if not code:
        return redirect("/invoices?xero_error=no_code&xero_msg=No+authorization+code+received+from+Xero")

    try:
        import requests
        import base64
        
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
            return redirect(f"/invoices?xero_error=exchange_failed&xero_msg={token_response.text}")
            
        token = token_response.json()
        session['xero_token'] = token
        
        api_client.configuration.oauth2_token = OAuth2Token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        api_client.set_oauth2_token(token)
        
        identity_api = IdentityApi(api_client)
        connections = identity_api.get_connections()
        
        if connections:
            session['xero_tenant_id'] = connections[0].tenant_id
            session['xero_tenant_name'] = getattr(connections[0], 'tenant_name', 'Connected Organization')
            
        return redirect("/invoices?xero_connected=true")
    except Exception as e:
        return redirect(f"/invoices?xero_error=exception&xero_msg={str(e)}")

@xero_bp.route("/api/xero/connection-status", methods=["GET"])
def connection_status():
    """Returns the live connection status with Xero and organization name."""
    verification = verify_xero_connection()
    return jsonify({
        "status": "success",
        "connected": verification["connected"],
        "tenant_id": verification["tenant_id"],
        "tenant_name": verification["tenant_name"],
        "error": verification["error"],
        "client_id_configured": bool(CLIENT_ID and CLIENT_SECRET)
    })

@xero_bp.route("/xero/disconnect", methods=["GET", "POST"])
def disconnect_xero():
    """Disconnects the current Xero session."""
    session.pop('xero_token', None)
    session.pop('xero_tenant_id', None)
    session.pop('xero_tenant_name', None)
    return redirect("/invoices?xero_disconnected=true")

@xero_bp.route("/api/xero/sync", methods=["POST"])
def sync_invoice():
    """
    Endpoint triggered by the frontend to push a single invoice to Xero.
    Actively checks and validates the Xero connection at EVERY sync request,
    validates line item account codes, inspects Xero validation errors,
    and returns direct links to the synced invoice in Xero.
    """
    payload = request.json or {}
    invoice_data = payload.get('invoice')
    simulate = payload.get('simulate', False)
    
    if not invoice_data:
        return jsonify({"status": "error", "message": "No invoice data provided"}), 400

    inv_id = invoice_data.get('invoice_id', 'Unknown')
    invoice_number = invoice_data.get('invoice_number') or f"INV-{inv_id}"
    company_name = invoice_data.get('company_name')
    person_name = invoice_data.get('contact_name')
    first_name = invoice_data.get('contact_first_name')
    last_name = invoice_data.get('contact_last_name')
    if not first_name and person_name:
        parts = person_name.strip().split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

    contact_display_name = company_name if company_name else (person_name if person_name else "AJBCC Member")
    contact_email = invoice_data.get('contact_email')
    contact_phone = invoice_data.get('contact_phone')
    billing_address = invoice_data.get('billing_address') or {}
    glue_up_status = invoice_data.get('status', 'Awaiting Payment')
    reference_str = f"GlueUp-{inv_id}"

    print(f"\n[XERO SYNC] >>> Initiating sync for Invoice #{inv_id} ({invoice_number}) | Contact: {contact_display_name} ({person_name or ''}) | Email: {contact_email} | Phone: {contact_phone} | Status: {glue_up_status} | Simulate: {simulate}", flush=True)

    # 1. Handle Dry-Run Simulation (if user explicitly opted into simulation via UI toggle)
    if simulate:
        simulated_xero_status = "PAID" if glue_up_status == 'Paid' else ("VOIDED" if glue_up_status == 'Void' else ("DRAFT" if glue_up_status == 'Draft' else "AUTHORISED"))
        print(f"[XERO SYNC] Dry-run simulated push successful for #{invoice_number} (Status: {simulated_xero_status})", flush=True)
        return jsonify({
            "status": "success",
            "message": f"[DRY RUN] Invoice #{invoice_number} simulated push to Xero! (Contact: {contact_display_name}, Status: {simulated_xero_status})",
            "simulated": True,
            "xero_id": "simulated-" + str(inv_id),
            "xero_number": invoice_number,
            "xero_status": simulated_xero_status,
            "xero_url": None
        }), 200

    # 2. Active Verification of Xero Connection at every sync request
    status_check = verify_xero_connection()

    if not status_check["connected"]:
        # Real sync attempted, but connection check failed!
        print(f"[XERO SYNC] Blocked: Connection verification failed: {status_check['error']}", flush=True)
        return jsonify({
            "status": "error", 
            "message": f"Xero verification failed: {status_check['error']} The invoice was NOT synced to Xero.", 
            "auth_required": True,
            "login_url": url_for('xero_bp.login')
        }), 401

    xero_tenant_id = status_check["tenant_id"]
    xero_token = session.get('xero_token')

    try:
        # Rehydrate the API client with the verified token
        api_client.configuration.oauth2_token = OAuth2Token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        api_client.set_oauth2_token(xero_token)
        accounting_api = AccountingApi(api_client)

        from datetime import datetime
        from xero_python.accounting import (
            Invoices, Invoice, LineItem, Contact, Contacts, Address, Phone,
            LineAmountTypes, Payment, Account, Accounts
        )

        # Date parsing
        raw_date = invoice_data.get('date')
        raw_due_date = invoice_data.get('due_date')
        try:
            parsed_date = datetime.strptime(raw_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            parsed_date = datetime.now()
            
        try:
            if raw_due_date:
                parsed_due_date = datetime.strptime(raw_due_date, "%Y-%m-%d")
            else:
                parsed_due_date = parsed_date
        except (ValueError, TypeError):
            parsed_due_date = parsed_date

        # Determine target Xero status
        if glue_up_status in ['Paid', 'Awaiting Payment', 'Overdue', 'Void']:
            xero_status = "AUTHORISED"
        else:
            xero_status = "DRAFT"

        # Discover available accounts in the organization if permitted
        available_account_codes = set()
        bank_account_ids = []
        try:
            accounts_resp = accounting_api.get_accounts(xero_tenant_id=xero_tenant_id)
            if accounts_resp and accounts_resp.accounts:
                for acc in accounts_resp.accounts:
                    if acc.code:
                        available_account_codes.add(str(acc.code).strip())
                    if acc.type == "BANK" and acc.account_id:
                        bank_account_ids.append(acc.account_id)
                print(f"[XERO SYNC] Discovered {len(available_account_codes)} accounts in Xero org (Found Bank: {len(bank_account_ids) > 0})", flush=True)
        except Exception as acc_err:
            if is_scope_error(acc_err):
                raise ScopeUpgradeRequiredException(f"Accounts query requires scope upgrade: {acc_err}")
            print(f"[XERO SYNC] Note: Could not query Chart of Accounts: {acc_err}", flush=True)

        def build_line_items(force_account_code=None):
            """Builds LineItem objects, dynamically mapping accounts and omitting hardcoded taxes."""
            items = []
            for item in invoice_data.get('items', []):
                item_type = item.get('type', 'Standard')
                
                if force_account_code:
                    account_code = force_account_code
                elif available_account_codes:
                    if item_type == 'Membership Application' and '2004' in available_account_codes:
                        account_code = "2004"
                    elif item_type == 'Additional Member' and '2005' in available_account_codes:
                        account_code = "2005"
                    elif "200" in available_account_codes:
                        account_code = "200"
                    else:
                        account_code = "200"
                else:
                    if item_type == 'Membership Application':
                        account_code = "2004"
                    elif item_type == 'Additional Member':
                        account_code = "2005"
                    else:
                        account_code = "200"
                    
                total_amount = float(item.get('amount', 0))
                quantity = float(item.get('quantity', 1.0))
                unit_amount = total_amount / quantity if quantity != 0 else total_amount

                # Do NOT hardcode tax_type="OUTPUT" so Xero automatically defaults to the account's configured tax rate
                items.append(
                    LineItem(
                        description=item.get('description', 'AJBCC Membership Fee'),
                        unit_amount=round(unit_amount, 4),
                        quantity=quantity,
                        account_code=account_code
                    )
                )
            return items

        def get_clearing_account_id():
            if bank_account_ids:
                return bank_account_ids[0]
            try:
                # 1. Active bank accounts
                banks = accounting_api.get_accounts(xero_tenant_id=xero_tenant_id, where='Type=="BANK" AND Status=="ACTIVE"')
                if banks and banks.accounts:
                    return banks.accounts[0].account_id

                # 2. Accounts with EnablePaymentsToAccount == true
                pay_accs = accounting_api.get_accounts(xero_tenant_id=xero_tenant_id, where='EnablePaymentsToAccount==true AND Status=="ACTIVE"')
                if pay_accs and pay_accs.accounts:
                    return pay_accs.accounts[0].account_id

                # 3. Check for any active Current Asset / Liability / Clearing account and enable payments on it
                all_accs = accounting_api.get_accounts(xero_tenant_id=xero_tenant_id, where='Status=="ACTIVE"')
                if all_accs and all_accs.accounts:
                    for acc in all_accs.accounts:
                        if acc.type in ["CURRENT", "CURRLIAB", "EXPENSE", "REVENUE", "OTHERINCOME"]:
                            try:
                                acc_update = Account(enable_payments_to_account=True)
                                accounting_api.update_account(
                                    xero_tenant_id=xero_tenant_id,
                                    account_id=acc.account_id,
                                    accounts=Accounts(accounts=[acc_update])
                                )
                                print(f"[XERO SYNC] Enabled payments on account '{acc.name}' ({acc.code})", flush=True)
                                return acc.account_id
                            except Exception as e:
                                if is_scope_error(e):
                                    raise ScopeUpgradeRequiredException(f"Enable payments requires scope upgrade: {e}")
                                print(f"[XERO SYNC] Note: Could not enable payments on {acc.code}: {e}", flush=True)
            except Exception as e:
                if is_scope_error(e):
                    raise ScopeUpgradeRequiredException(f"Clearing account lookup requires scope upgrade: {e}")
                print(f"[XERO SYNC] Note: Error locating clearing account: {e}", flush=True)
            return None

        # Core sync logic
        def attempt_sync():
            target_inv_number = invoice_number

            # 1. Contact & Billing Address Construction
            addresses_list = []
            has_addr_info = bool(
                billing_address.get('address_line1') or 
                billing_address.get('city') or 
                billing_address.get('postal_code')
            )
            attention_name = person_name or f"{first_name or ''} {last_name or ''}".strip() or None

            if has_addr_info:
                # POBOX: Required by Xero for the Billing / Postal address printed on the invoice under "To"
                addresses_list.append(
                    Address(
                        address_type="POBOX",
                        attention_to=attention_name,
                        address_line1=billing_address.get('address_line1') or None,
                        address_line2=billing_address.get('address_line2') or None,
                        city=billing_address.get('city') or None,
                        region=billing_address.get('region') or None,
                        postal_code=billing_address.get('postal_code') or None,
                        country=billing_address.get('country') or 'Australia'
                    )
                )
                # STREET: Physical delivery address
                addresses_list.append(
                    Address(
                        address_type="STREET",
                        attention_to=attention_name,
                        address_line1=billing_address.get('address_line1') or None,
                        address_line2=billing_address.get('address_line2') or None,
                        city=billing_address.get('city') or None,
                        region=billing_address.get('region') or None,
                        postal_code=billing_address.get('postal_code') or None,
                        country=billing_address.get('country') or 'Australia'
                    )
                )

            phones_list = []
            if contact_phone:
                phones_list.append(
                    Phone(
                        phone_type="DEFAULT",
                        phone_number=str(contact_phone).strip()
                    )
                )

            contact_obj = Contact(
                name=contact_display_name,
                first_name=first_name or None,
                last_name=last_name or None,
                email_address=contact_email or None,
                addresses=addresses_list if addresses_list else None,
                phones=phones_list if phones_list else None
            )

            try:
                safe_name = contact_display_name.replace('"', '\\"')
                where_clause = f'Name=="{safe_name}"'
                    
                print(f"[XERO SYNC] Looking up contact by Name: {where_clause}...", flush=True)
                existing_contacts = accounting_api.get_contacts(
                    xero_tenant_id=xero_tenant_id,
                    where=where_clause
                )
                if existing_contacts and existing_contacts.contacts:
                    c_id = existing_contacts.contacts[0].contact_id
                    print(f"[XERO SYNC] Found existing contact: {contact_display_name} (ID: {c_id}). Updating contact with billing details...", flush=True)
                    # Update contact in Xero to ensure address, phone, and names are saved
                    try:
                        update_payload = Contact(
                            contact_id=c_id,
                            name=contact_display_name,
                            first_name=first_name or None,
                            last_name=last_name or None,
                            email_address=contact_email or None,
                            addresses=addresses_list if addresses_list else None,
                            phones=phones_list if phones_list else None
                        )
                        accounting_api.update_contact(
                            xero_tenant_id=xero_tenant_id,
                            contact_id=c_id,
                            contacts=Contacts(contacts=[update_payload])
                        )
                        print(f"[XERO SYNC] Successfully updated existing contact {c_id} with billing details in Xero.", flush=True)
                    except Exception as upd_err:
                        print(f"[XERO SYNC] Note: Could not update existing contact: {upd_err}", flush=True)

                    contact_obj = Contact(
                        contact_id=c_id,
                        name=contact_display_name,
                        first_name=first_name or None,
                        last_name=last_name or None,
                        email_address=contact_email or None,
                        addresses=addresses_list if addresses_list else None,
                        phones=phones_list if phones_list else None
                    )
                else:
                    print(f"[XERO SYNC] Contact '{contact_display_name}' not found by Name. Creating new contact...", flush=True)
                    try:
                        new_contact_payload = Contact(
                            name=contact_display_name,
                            first_name=first_name or None,
                            last_name=last_name or None,
                            email_address=contact_email or None,
                            addresses=addresses_list if addresses_list else None,
                            phones=phones_list if phones_list else None
                        )
                        new_contact = accounting_api.create_contacts(
                            xero_tenant_id=xero_tenant_id,
                            contacts=Contacts(contacts=[new_contact_payload])
                        )
                        if new_contact and new_contact.contacts and new_contact.contacts[0].contact_id:
                            c_id = new_contact.contacts[0].contact_id
                            contact_obj = Contact(
                                contact_id=c_id,
                                name=contact_display_name,
                                first_name=first_name or None,
                                last_name=last_name or None,
                                email_address=contact_email or None,
                                addresses=addresses_list if addresses_list else None,
                                phones=phones_list if phones_list else None
                            )
                            print(f"[XERO SYNC] Created contact with ID: {c_id}", flush=True)
                        else:
                            contact_obj = Contact(
                                name=contact_display_name,
                                first_name=first_name or None,
                                last_name=last_name or None,
                                email_address=contact_email or None,
                                addresses=addresses_list if addresses_list else None,
                                phones=phones_list if phones_list else None
                            )
                    except Exception as create_err:
                        print(f"[XERO SYNC] Note: Pre-creating contact failed ({create_err}). Letting Xero resolve by name.", flush=True)
                        contact_obj = Contact(
                            name=contact_display_name,
                            first_name=first_name or None,
                            last_name=last_name or None,
                            email_address=contact_email or None,
                            addresses=addresses_list if addresses_list else None,
                            phones=phones_list if phones_list else None
                        )
            except Exception as c_err:
                print(f"[XERO SYNC] Warning during contact query/create: {c_err}", flush=True)
                contact_obj = Contact(
                    name=contact_display_name,
                    first_name=first_name or None,
                    last_name=last_name or None,
                    email_address=contact_email or None,
                    addresses=addresses_list if addresses_list else None,
                    phones=phones_list if phones_list else None
                )

            # 2. Check for Duplicates / Existing Invoices
            dup_where = f'Reference=="{reference_str}" OR InvoiceNumber=="{target_inv_number}"'
            print(f"[XERO SYNC] Checking for existing invoice ({dup_where})...", flush=True)
            existing_invoices = accounting_api.get_invoices(
                xero_tenant_id=xero_tenant_id,
                where=dup_where
            )
            
            # Separate active invoices from inactive (DELETED or VOIDED)
            # In Xero, DELETED and VOIDED invoices are immutable and can never be modified.
            active_invoices = []
            inactive_invoices = []
            if existing_invoices and existing_invoices.invoices:
                for inv in existing_invoices.invoices:
                    if inv.status in ['DELETED', 'VOIDED']:
                        inactive_invoices.append(inv)
                    else:
                        active_invoices.append(inv)

            # If an ACTIVE invoice already exists in Xero, update it or report it
            if active_invoices:
                existing_inv = active_invoices[0]
                ex_id = existing_inv.invoice_id
                ex_num = existing_inv.invoice_number or target_inv_number
                ex_status = existing_inv.status
                xero_url = f"https://go.xero.com/AccountsReceivable/View.aspx?InvoiceID={ex_id}"
                print(f"[XERO SYNC] Found ACTIVE existing invoice #{ex_num} in Xero (ID: {ex_id}, Status: {ex_status})", flush=True)

                if glue_up_status == 'Void' and ex_status != 'VOIDED':
                    if ex_status == 'PAID':
                        return False, f"Invoice #{ex_num} is already PAID in Xero and cannot be voided.", ex_id, ex_num, ex_status, xero_url
                    try:
                        accounting_api.update_invoice(
                            xero_tenant_id=xero_tenant_id,
                            invoice_id=ex_id,
                            invoices=Invoices(invoices=[Invoice(status="VOIDED")])
                        )
                    except Exception as v_err:
                        if is_scope_error(v_err):
                            raise ScopeUpgradeRequiredException(str(v_err))
                        print(f"[XERO SYNC] Note updating to voided via update_invoice: {v_err}", flush=True)
                        accounting_api.create_invoices(
                            xero_tenant_id=xero_tenant_id,
                            invoices=Invoices(invoices=[Invoice(invoice_id=ex_id, status="VOIDED")])
                        )
                    return True, f"Invoice #{ex_num} marked as VOIDED in Xero!", ex_id, ex_num, "VOIDED", xero_url

                elif ex_status == 'PAID':
                    if glue_up_status == 'Paid':
                        return True, f"Invoice #{ex_num} is already PAID and up to date in Xero.", ex_id, ex_num, "PAID", xero_url
                    else:
                        return False, f"Invoice #{ex_num} is already recorded as PAID in Xero (line items cannot be altered on paid invoices).", ex_id, ex_num, "PAID", xero_url

                # Active invoice is DRAFT or AUTHORISED:
                # Update Contact, Line Items, Due Date, Reference, and Status to synchronize latest changes from Glue Up!
                updated_line_items = build_line_items()
                target_status = "AUTHORISED" if glue_up_status in ['Paid', 'Awaiting Payment', 'Overdue'] else ("DRAFT" if glue_up_status == 'Draft' else ex_status)

                update_invoice_obj = Invoice(
                    invoice_id=ex_id,
                    contact=contact_obj,
                    line_items=updated_line_items,
                    due_date=parsed_due_date,
                    date=parsed_date,
                    reference=reference_str,
                    line_amount_types=LineAmountTypes.INCLUSIVE,
                    status=target_status
                )

                print(f"[XERO SYNC] Updating existing invoice #{ex_num} in Xero with latest Glue Up items and details...", flush=True)
                try:
                    upd_resp = accounting_api.update_invoice(
                        xero_tenant_id=xero_tenant_id,
                        invoice_id=ex_id,
                        invoices=Invoices(invoices=[update_invoice_obj])
                    )
                    if upd_resp and upd_resp.invoices:
                        ex_status = upd_resp.invoices[0].status or target_status
                        print(f"[XERO SYNC] Successfully updated invoice #{ex_num} via update_invoice (Status: {ex_status})", flush=True)
                except Exception as up_err:
                    if is_scope_error(up_err):
                        raise ScopeUpgradeRequiredException(str(up_err))
                    print(f"[XERO SYNC] Note on update_invoice: {up_err}. Trying create_invoices POST...", flush=True)
                    try:
                        upd_resp = accounting_api.create_invoices(
                            xero_tenant_id=xero_tenant_id,
                            invoices=Invoices(invoices=[update_invoice_obj])
                        )
                        if upd_resp and upd_resp.invoices:
                            ex_status = upd_resp.invoices[0].status or target_status
                            print(f"[XERO SYNC] Successfully updated invoice #{ex_num} via create_invoices (Status: {ex_status})", flush=True)
                    except Exception as up2_err:
                        if is_scope_error(up2_err):
                            raise ScopeUpgradeRequiredException(str(up2_err))
                        print(f"[XERO SYNC] Warning: could not update invoice details: {up2_err}", flush=True)

                # If Glue Up status is 'Paid' and Xero invoice is not yet marked 'PAID':
                if glue_up_status == 'Paid' and ex_status != 'PAID':
                    if ex_status == 'DRAFT':
                        try:
                            accounting_api.update_invoice(
                                xero_tenant_id=xero_tenant_id,
                                invoice_id=ex_id,
                                invoices=Invoices(invoices=[Invoice(status="AUTHORISED")])
                            )
                            ex_status = "AUTHORISED"
                        except Exception as d_err:
                            if is_scope_error(d_err):
                                raise ScopeUpgradeRequiredException(str(d_err))
                            print(f"[XERO SYNC] Note authorizing draft: {d_err}", flush=True)

                    clearing_id = get_clearing_account_id()
                    pay_info = invoice_data.get('payment') or {}
                    pay_amount = float(pay_info.get('amount') or sum(float(item.get('amount', 0)) for item in invoice_data.get('items', [])))
                    
                    # Cap payment at amount due in Xero to prevent "Payment amount exceeds amount outstanding"
                    ex_due = getattr(existing_inv, 'amount_due', None)
                    if ex_due is not None:
                        ex_due_val = float(ex_due)
                        if ex_due_val <= 0.001:
                            print(f"[XERO SYNC] Invoice #{ex_num} is already fully paid in Xero (AmountDue: 0).", flush=True)
                            return True, f"Invoice #{ex_num} updated in Xero with latest Glue Up details and is fully PAID!", ex_id, ex_num, "PAID", xero_url
                        pay_amount = min(pay_amount, ex_due_val)

                    pay_date_str = pay_info.get('date') or invoice_data.get('payment_completion_date') or raw_date
                    try:
                        parsed_pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d")
                    except Exception:
                        parsed_pay_date = parsed_date
                    pay_ref = pay_info.get('reference') or f"GlueUp-{target_inv_number}"

                    if clearing_id:
                        try:
                            payment = Payment(
                                invoice=Invoice(invoice_id=ex_id),
                                account=Account(account_id=clearing_id),
                                amount=round(pay_amount, 2),
                                date=parsed_pay_date,
                                reference=f"GlueUp {pay_ref}".strip()
                            )
                            accounting_api.create_payment(xero_tenant_id=xero_tenant_id, payment=payment)
                            print(f"[XERO SYNC] Applied payment of ${pay_amount} to invoice #{ex_num}", flush=True)
                            return True, f"Invoice #{ex_num} updated in Xero with latest Glue Up details and marked as PAID!", ex_id, ex_num, "PAID", xero_url
                        except Exception as p_err:
                            if is_scope_error(p_err):
                                raise ScopeUpgradeRequiredException(str(p_err))
                            print(f"[XERO SYNC] Payment application error: {p_err}", flush=True)
                            return False, f"Invoice #{ex_num} details updated in Xero, but payment creation failed: {p_err}", ex_id, ex_num, ex_status, xero_url
                    else:
                        return False, f"Invoice #{ex_num} updated in Xero, but payment sync requires an active Bank Account or payment-enabled clearing account in Xero.", ex_id, ex_num, ex_status, xero_url

                return True, f"Invoice #{ex_num} updated in Xero with latest details from Glue Up! (Status: {ex_status})", ex_id, ex_num, ex_status, xero_url

            elif inactive_invoices:
                last_inactive = inactive_invoices[0]
                inact_status = last_inactive.status
                inact_num = last_inactive.invoice_number or target_inv_number
                inact_id = last_inactive.invoice_id
                inact_url = f"https://go.xero.com/AccountsReceivable/View.aspx?InvoiceID={inact_id}"
                print(f"[XERO SYNC] Matched previous record #{inact_num} which is {inact_status} in Xero.", flush=True)

                if glue_up_status == 'Void':
                    return True, f"Invoice #{inact_num} is already {inact_status} in Xero.", inact_id, inact_num, inact_status, inact_url

                # If Glue Up invoice is active (e.g. Awaiting Payment or Paid),
                # we recreate a fresh active invoice rather than touching the immutable voided/deleted one
                print(f"[XERO SYNC] Previous record was {inact_status}. Creating a fresh active invoice in Xero...", flush=True)
                if str(last_inactive.invoice_number).strip().lower() == str(target_inv_number).strip().lower():
                    # Xero does not allow reusing the exact same InvoiceNumber even if voided
                    target_inv_number = None  # let Xero auto-assign next number, keeping Reference intact

            # 3. Create New Invoice in Xero
            line_items = build_line_items()
            xero_invoice = Invoice(
                type="ACCREC",
                contact=contact_obj,
                line_items=line_items,
                invoice_number=target_inv_number,
                date=parsed_date,
                due_date=parsed_due_date,
                reference=reference_str,
                line_amount_types=LineAmountTypes.INCLUSIVE,
                status=xero_status
            )

            print(f"[XERO SYNC] Sending POST /Invoices to Xero for #{target_inv_number or 'Auto-assigned'}...", flush=True)
            created_invoices = accounting_api.create_invoices(
                xero_tenant_id=xero_tenant_id,
                invoices=Invoices(invoices=[xero_invoice])
            )

            xero_res = created_invoices.invoices[0] if created_invoices and created_invoices.invoices else None
            if not xero_res:
                return False, "Received empty response from Xero API.", None, None, None, None

            # Check for Xero business validation errors
            if getattr(xero_res, 'has_errors', False):
                raw_errors = getattr(xero_res, 'validation_errors', []) or []
                err_messages = [e.message for e in raw_errors if hasattr(e, 'message')]
                full_err = "; ".join(err_messages) if err_messages else "Xero business validation rejected the invoice."
                print(f"[XERO SYNC] Validation error from Xero: {full_err}", flush=True)

                needs_retry = False
                if "Account code" in full_err and "is not a valid code" in full_err:
                    print(f"[XERO SYNC] Retrying automatically with standard Sales account '200'...", flush=True)
                    xero_invoice.line_items = build_line_items(force_account_code="200")
                    needs_retry = True

                if "Invoice number must be unique" in full_err:
                    print(f"[XERO SYNC] Retrying without explicit invoice_number so Xero auto-assigns next number...", flush=True)
                    xero_invoice.invoice_number = None
                    needs_retry = True

                if needs_retry:
                    retry_resp = accounting_api.create_invoices(
                        xero_tenant_id=xero_tenant_id,
                        invoices=Invoices(invoices=[xero_invoice])
                    )
                    xero_res = retry_resp.invoices[0] if retry_resp and retry_resp.invoices else None
                    if xero_res and not getattr(xero_res, 'has_errors', False):
                        print(f"[XERO SYNC] Automatic retry SUCCEEDED! (Created: {xero_res.invoice_number})", flush=True)
                    else:
                        retry_errs = [e.message for e in getattr(xero_res, 'validation_errors', []) if hasattr(e, 'message')]
                        retry_err_str = "; ".join(retry_errs) if retry_errs else full_err
                        print(f"[XERO SYNC] Retry failed: {retry_err_str}", flush=True)
                        return False, f"Xero rejected invoice: {retry_err_str}", None, None, None, None
                else:
                    return False, f"Xero rejected invoice: {full_err}", None, None, None, None

            new_id = xero_res.invoice_id
            new_num = xero_res.invoice_number or target_inv_number
            final_status = xero_res.status or xero_status
            xero_url = f"https://go.xero.com/AccountsReceivable/View.aspx?InvoiceID={new_id}"
            print(f"[XERO SYNC] SUCCESS! Created Invoice #{new_num} in Xero (ID: {new_id}, Status: {final_status})", flush=True)


            # 4. Handle Payments for Paid Invoices
            if glue_up_status == 'Paid':
                clearing_id = get_clearing_account_id()
                pay_info = invoice_data.get('payment') or {}
                pay_amount = float(pay_info.get('amount') or sum(float(item.get('amount', 0)) for item in invoice_data.get('items', [])))
                
                # Cap payment at amount due or total of the newly created invoice
                res_due = getattr(xero_res, 'amount_due', None) or getattr(xero_res, 'total', None)
                if res_due is not None:
                    res_due_val = float(res_due)
                    if res_due_val > 0:
                        pay_amount = min(pay_amount, res_due_val)

                pay_date_str = pay_info.get('date') or invoice_data.get('payment_completion_date') or raw_date
                try:
                    parsed_pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d")
                except Exception:
                    parsed_pay_date = parsed_date
                pay_ref = pay_info.get('reference') or f"GlueUp-{target_inv_number}"

                if clearing_id:
                    try:
                        payment = Payment(
                            invoice=Invoice(invoice_id=new_id),
                            account=Account(account_id=clearing_id),
                            amount=round(pay_amount, 2),
                            date=parsed_pay_date,
                            reference=f"GlueUp {pay_ref}".strip()
                        )
                        accounting_api.create_payment(xero_tenant_id=xero_tenant_id, payment=payment)
                        final_status = "PAID"
                        print(f"[XERO SYNC] Payment applied to Invoice #{new_num}!", flush=True)
                        return True, f"Invoice #{new_num} successfully created in Xero and marked as PAID!", new_id, new_num, final_status, xero_url
                    except Exception as p_err:
                        if is_scope_error(p_err):
                            raise ScopeUpgradeRequiredException(str(p_err))
                        print(f"[XERO SYNC] Payment application note: {p_err}", flush=True)
                        return False, f"Invoice #{new_num} created in Xero, but payment creation failed: {p_err}", new_id, new_num, final_status, xero_url
                else:
                    return False, f"Invoice #{new_num} created in Xero, but payment sync requires an active Bank Account or payment-enabled clearing account in Xero.", new_id, new_num, final_status, xero_url

            # 5. Handle Void Invoices
            if glue_up_status == 'Void':
                try:
                    accounting_api.update_invoice(
                        xero_tenant_id=xero_tenant_id,
                        invoice_id=new_id,
                        invoices=Invoices(invoices=[Invoice(status="VOIDED")])
                    )
                    final_status = "VOIDED"
                    print(f"[XERO SYNC] Marked Invoice #{new_num} as VOIDED in Xero", flush=True)
                    return True, f"Invoice #{new_num} successfully created and marked as VOIDED in Xero!", new_id, new_num, final_status, xero_url
                except Exception as v_err:
                    if is_scope_error(v_err):
                        raise ScopeUpgradeRequiredException(str(v_err))
                    print(f"[XERO SYNC] Void update note: {v_err}", flush=True)
                    try:
                        accounting_api.create_invoices(
                            xero_tenant_id=xero_tenant_id,
                            invoices=Invoices(invoices=[Invoice(invoice_id=new_id, status="VOIDED")])
                        )
                        final_status = "VOIDED"
                        print(f"[XERO SYNC] Marked Invoice #{new_num} as VOIDED via create_invoices update in Xero", flush=True)
                        return True, f"Invoice #{new_num} successfully created and marked as VOIDED in Xero!", new_id, new_num, final_status, xero_url
                    except Exception as v2_err:
                        if is_scope_error(v2_err):
                            raise ScopeUpgradeRequiredException(str(v2_err))
                        print(f"[XERO SYNC] Void update fallback error: {v2_err}", flush=True)
                        return True, f"Invoice #{new_num} created in Xero (AUTHORISED), but could not be voided: {v_err}", new_id, new_num, final_status, xero_url

            return True, f"Invoice #{new_num} successfully created in Xero!", new_id, new_num, final_status, xero_url

        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                success, msg, xero_id, xero_num, final_status, xero_url = attempt_sync()
                break
            except ScopeUpgradeRequiredException as scope_err:
                print(f"[XERO SYNC] Scope upgrade required: {scope_err}", flush=True)
                return jsonify({
                    "status": "error",
                    "message": "Your Xero session requires updated permissions (scopes) to sync payments and access bank accounts. Please click 'Log In & Authorize Xero' below to reconnect with the updated permissions.",
                    "auth_required": True,
                    "login_url": url_for('xero_bp.login')
                }), 401
            except Exception as api_err:
                err_str = str(api_err)
                print(f"[XERO SYNC] Exception during sync attempt {attempt+1}: {err_str}", flush=True)
                if is_scope_error(api_err):
                    return jsonify({
                        "status": "error",
                        "message": "Your Xero session requires updated permissions (scopes) to sync payments and access bank accounts. Please click 'Log In & Authorize Xero' below to reconnect with the updated permissions.",
                        "auth_required": True,
                        "login_url": url_for('xero_bp.login')
                    }), 401
                if "429" in err_str:
                    if attempt < max_retries - 1:
                        backoff = 2 ** attempt
                        print(f"[XERO SYNC] Rate limited (429). Retrying in {backoff}s...", flush=True)
                        time.sleep(backoff)
                        continue
                    else:
                        return jsonify({"status": "error", "message": "Xero API rate limit reached. Please try again in a few moments."}), 429
                elif "401" in err_str or "Unauthorized" in err_str:
                    print("[XERO SYNC] Token expired, attempting refresh...", flush=True)
                    try:
                        api_client.refresh_oauth2_token()
                    except Exception as ref_err:
                        print(f"[XERO SYNC] Refresh failed: {ref_err}", flush=True)
                        return jsonify({
                            "status": "error",
                            "message": "Xero authentication expired. Please click 'Log In & Authorize Xero' to reconnect.",
                            "auth_required": True,
                            "login_url": url_for('xero_bp.login')
                        }), 401
                else:
                    return jsonify({"status": "error", "message": f"Xero API Error: {err_str}"}), 400

        status_code = 200 if success else 400
        return jsonify({
            "status": "success" if success else "error",
            "message": msg,
            "xero_id": xero_id,
            "xero_number": xero_num,
            "xero_status": final_status,
            "xero_url": xero_url
        }), status_code
        
    except ScopeUpgradeRequiredException as scope_err:
        print(f"[XERO SYNC] Scope upgrade required: {scope_err}", flush=True)
        return jsonify({
            "status": "error",
            "message": "Your Xero session requires updated permissions (scopes) to sync payments and access bank accounts. Please click 'Log In & Authorize Xero' below to reconnect with the updated permissions.",
            "auth_required": True,
            "login_url": url_for('xero_bp.login')
        }), 401
    except Exception as e:
        if is_scope_error(e):
            return jsonify({
                "status": "error",
                "message": "Your Xero session requires updated permissions (scopes) to sync payments and access bank accounts. Please click 'Log In & Authorize Xero' below to reconnect with the updated permissions.",
                "auth_required": True,
                "login_url": url_for('xero_bp.login')
            }), 401
        print(f"[XERO SYNC] Unhandled Exception: {str(e)}", flush=True)
        return jsonify({"status": "error", "message": f"Xero Error: {str(e)}"}), 500


@xero_bp.route("/api/xero/check_statuses", methods=["POST"])
def check_statuses():
    """Bulk check the Xero status for a list of invoice IDs."""
    payload = request.json
    invoice_ids = payload.get('invoice_ids', [])
    
    if not invoice_ids:
        return jsonify({"status": "success", "data": {}})
        
    xero_token = session.get('xero_token')
    xero_tenant_id = session.get('xero_tenant_id')
    
    # Dynamic status lookup from mock data when running in mock / disconnected mode
    mock_status_map = {
        # Legacy IDs fallback
        "12836418": "AUTHORISED",
        "12790728": "PAID",
        "12769018": "AUTHORISED",
        "12812473": "AUTHORISED",
        "12780507": "DRAFT",
        "12769138": "VOIDED"
    }
    try:
        from glue_up_api import GlueUpAPI
        glueup = GlueUpAPI()
        if glueup.has_mock_data():
            for m in glueup.get_mock_invoices():
                mid = str(m.get('id') or '')
                raw_st = str(m.get('status') or '')
                is_vd = bool(m.get('voided', False))
                b_due = float(m.get('balanceDue') if m.get('balanceDue') is not None else 0)
                if is_vd or raw_st.lower() in ['void', 'voided', 'cancelled']:
                    x_st = 'VOIDED'
                elif raw_st.lower() == 'draft':
                    x_st = 'DRAFT'
                elif b_due <= 0 or raw_st.lower() == 'paid':
                    x_st = 'PAID'
                else:
                    x_st = 'AUTHORISED'
                if mid:
                    mock_status_map[mid] = x_st
    except Exception as e:
        print(f"Error loading mock status map: {e}")

    if not xero_token or not xero_tenant_id:
        simulated = {str(i): mock_status_map[str(i)] for i in invoice_ids if str(i) in mock_status_map}
        return jsonify({"status": "success", "data": simulated, "connected": False})
        
    try:
        api_client.configuration.oauth2_token = OAuth2Token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        api_client.set_oauth2_token(xero_token)
        accounting_api = AccountingApi(api_client)
        
        # Build OR clause for References (batch in chunks of 50 to avoid URL length limits)
        results = {}
        
        # Helper to chunk list
        def chunker(seq, size):
            return (seq[pos:pos + size] for pos in range(0, len(seq), size))
            
        for chunk in chunker(invoice_ids, 50):
            where_clause = " OR ".join([f'Reference=="GlueUp-{inv_id}"' for inv_id in chunk])
            try:
                existing_invoices = accounting_api.get_invoices(
                    xero_tenant_id=xero_tenant_id,
                    where=where_clause
                )
                if existing_invoices and existing_invoices.invoices:
                    for inv in existing_invoices.invoices:
                        # Extract the GlueUp ID from Reference
                        ref = inv.reference
                        if ref and ref.startswith("GlueUp-"):
                            g_id = ref.replace("GlueUp-", "")
                            results[g_id] = inv.status
            except Exception as e:
                print(f"Error fetching chunk: {e}")
                pass
                
        return jsonify({"status": "success", "data": results})
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

