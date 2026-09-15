import json
from datetime import datetime
from flask import Blueprint, jsonify, render_template, request
from glue_up_api import GlueUpAPI

invoice_bp = Blueprint('invoice_bp', __name__)

def format_glueup_invoice(payload):
    """
    Standardizes a Glue Up invoice dictionary into a clean, normalized payload
    ready for frontend display and Xero synchronization.
    Handles differences between list and detail responses and mock data.
    """
    # 1. Invoice ID and Number
    invoice_id = payload.get('id') or payload.get('invoiceId') or payload.get('orderId') or payload.get('uuid') or 'Unknown'
    invoice_number = payload.get('number') or f"INV-{invoice_id}"
    
    # 2. Date parsing (ms timestamp or ISO string)
    def parse_to_date_str(val):
        if not val:
            return None
        try:
            if isinstance(val, (int, float)):
                return datetime.fromtimestamp(val / 1000.0).strftime("%Y-%m-%d")
            return str(val)[:10]
        except Exception:
            return None

    raw_date = payload.get('issueDate') or payload.get('createdOn') or payload.get('createdDate') or payload.get('date')
    invoice_date_str = parse_to_date_str(raw_date) or datetime.now().strftime("%Y-%m-%d")
    
    raw_due_date = payload.get('dueDate')
    due_date_str = parse_to_date_str(raw_due_date) or invoice_date_str
    
    # 3. Status determination
    is_voided = payload.get('voided', False)
    balance_due = float(payload.get('balanceDue') if payload.get('balanceDue') is not None else 0)
    glueup_status = str(payload.get('status') or '')
    
    now_ms = datetime.now().timestamp() * 1000
    due_date_ms = None
    if isinstance(raw_due_date, (int, float)):
        due_date_ms = float(raw_due_date)
    
    if is_voided or glueup_status.lower() in ['voided', 'void', 'canceled', 'cancelled']:
        invoice_status = 'Void'
    elif glueup_status.lower() == 'draft':
        invoice_status = 'Draft'
    elif balance_due <= 0 or glueup_status.lower() == 'paid':
        invoice_status = 'Paid'
    elif due_date_ms and due_date_ms < now_ms:
        invoice_status = 'Overdue'
    else:
        invoice_status = 'Awaiting Payment'

    # 4. Purchaser, Company & Contact extraction
    comp_obj = payload.get('company') if isinstance(payload.get('company'), dict) else {}
    contacts_list = payload.get('contacts', [])
    first_contact = contacts_list[0] if (contacts_list and isinstance(contacts_list[0], dict)) else {}

    company_name = (
        payload.get('purchaserCompanyName') or 
        comp_obj.get('name') or 
        first_contact.get('companyName') or 
        None
    )

    purchaser_given = (
        payload.get('purchaserGivenName') or 
        first_contact.get('givenName', '') or 
        ''
    )
    purchaser_family = (
        payload.get('purchaserFamilyName') or 
        first_contact.get('familyName', '') or 
        ''
    )
    contact_name = f"{purchaser_given} {purchaser_family}".strip() or None

    email_field = (
        payload.get('purchaserEmail') or 
        first_contact.get('emailAddress') or 
        first_contact.get('email') or 
        comp_obj.get('email')
    )
    if isinstance(email_field, dict):
        contact_email = email_field.get('value')
    elif isinstance(email_field, str):
        contact_email = email_field.strip() or None
    else:
        contact_email = None

    contact_phone = (
        payload.get('purchaserPhone') or 
        payload.get('phone') or 
        first_contact.get('phone') or 
        first_contact.get('workPhone') or 
        first_contact.get('mobilePhone') or 
        comp_obj.get('phone') or 
        None
    )

    if not company_name:
        company_name = contact_name or "AJBCC Member Organization"

    # Billing Address extraction (handles mock data, top-level, company dict, and contacts array)
    raw_addr = payload.get('purchaserAddress') or payload.get('address') or ''
    addr_dict = raw_addr if isinstance(raw_addr, dict) else {}
    c_addr = first_contact.get('address') if isinstance(first_contact.get('address'), dict) else {}

    address_line1 = (
        (raw_addr if isinstance(raw_addr, str) and raw_addr.strip() else '') or
        addr_dict.get('line1') or addr_dict.get('street') or addr_dict.get('streetAddress') or
        comp_obj.get('billingStreetAddress') or comp_obj.get('streetAddress') or
        first_contact.get('billingStreetAddress') or first_contact.get('streetAddress') or
        c_addr.get('line1') or c_addr.get('street') or ''
    )
    address_line2 = (
        payload.get('purchaserAddressLine2') or
        addr_dict.get('line2') or
        comp_obj.get('streetAddress2') or
        first_contact.get('streetAddress2') or
        c_addr.get('line2') or ''
    )
    city = (
        payload.get('purchaserCity') or
        addr_dict.get('city') or
        comp_obj.get('billingCity') or comp_obj.get('cityNameText') or comp_obj.get('city') or
        first_contact.get('billingCity') or first_contact.get('cityNameText') or first_contact.get('city') or
        c_addr.get('city') or ''
    )
    region = (
        payload.get('purchaserState') or payload.get('state') or payload.get('region') or
        addr_dict.get('region') or addr_dict.get('state') or
        comp_obj.get('billingState') or comp_obj.get('state') or comp_obj.get('region') or
        first_contact.get('billingState') or first_contact.get('state') or first_contact.get('region') or
        c_addr.get('region') or c_addr.get('state') or ''
    )
    postal_code = (
        payload.get('purchaserPostalCode') or payload.get('postalCode') or payload.get('zip') or
        addr_dict.get('postalCode') or addr_dict.get('postal_code') or addr_dict.get('zip') or
        comp_obj.get('billingZipCode') or comp_obj.get('zipCode') or
        first_contact.get('billingZipCode') or first_contact.get('zipCode') or
        c_addr.get('postalCode') or c_addr.get('zip') or ''
    )
    raw_country = (
        payload.get('purchaserCountry') or payload.get('country') or
        addr_dict.get('country') or
        comp_obj.get('billingCountry') or comp_obj.get('country') or comp_obj.get('countryCode') or
        first_contact.get('billingCountry') or first_contact.get('country') or first_contact.get('countryCode') or
        c_addr.get('country') or 'Australia'
    )
    COUNTRY_MAP = {
        'AU': 'Australia', 'JP': 'Japan', 'US': 'United States',
        'NZ': 'New Zealand', 'GB': 'United Kingdom', 'UK': 'United Kingdom',
        'CN': 'China', 'SG': 'Singapore'
    }
    country = COUNTRY_MAP.get(str(raw_country).strip().upper(), str(raw_country).strip() or 'Australia')

    billing_address = {
        "address_line1": address_line1.strip(),
        "address_line2": address_line2.strip(),
        "city": city.strip(),
        "region": region.strip(),
        "postal_code": postal_code.strip(),
        "country": country.strip(),
        "attention_to": contact_name or company_name
    }

    # 5. Line items parsing & Chart of Accounts mapping
    line_items = payload.get('items') or payload.get('lineItems') or payload.get('line_items') or []
    items_list = []

    for item in line_items:
        raw_type = item.get('type', 'Standard')
        description = item.get('description') or item.get('name') or ''
        
        # Categorize badge type
        type_lower = str(raw_type).lower()
        desc_lower = str(description).lower()
        
        if "additional" in type_lower or "additional" in desc_lower or "extra" in type_lower:
            badge_type = "Additional Member"
            account_name = "Membership Subscriptions - Ordinary - Additional"
        elif "application" in type_lower or "renewal" in type_lower or "membership" in type_lower or "nominate" in desc_lower:
            badge_type = "Membership Application"
            account_name = "Membership Subscriptions - Ordinary - Nominated"
        elif "ticket" in type_lower or "event" in type_lower:
            badge_type = "Event Ticket"
            account_name = "Sales - Event Tickets"
        elif "custom" in type_lower or "sponsorship" in desc_lower:
            badge_type = "Custom / Sponsorship"
            account_name = "Sales - Sponsorship / Custom"
        else:
            badge_type = "Standard"
            account_name = "Sales"
            
        if not description:
            description = badge_type

        amount = float(item.get('faceValue') or item.get('amount') or item.get('total') or 0)
        quantity = float(item.get('quantity') or item.get('count') or 1.0)

        items_list.append({
            "description": description,
            "amount": amount,
            "quantity": quantity,
            "type": badge_type,
            "account": account_name
        })

    # If an invoice has no explicit items array (summary payload), synthesize from totals
    if not items_list:
        total_amt = float(payload.get('faceTotal') or payload.get('total') or balance_due or 0)
        title = payload.get('title') or payload.get('origin') or 'Membership Invoice'
        items_list.append({
            "description": title,
            "amount": total_amt,
            "quantity": 1.0,
            "type": "Standard",
            "account": "Sales"
        })

    # 6. Payment Information extraction (Glue Up API models)
    raw_completion_date = payload.get('paymentCompletionDate')
    payment_completion_date_str = parse_to_date_str(raw_completion_date)
    
    payment_obj = payload.get('payment')
    payment_method = None
    payment_amount = 0.0
    payment_id = None
    payment_date_str = payment_completion_date_str
    payment_settle_status = None
    seen_payment_ids = set()
    
    if isinstance(payment_obj, dict):
        payment_method = payment_obj.get('paymentMethod') or payment_obj.get('method')
        payment_amount = float(payment_obj.get('amount') or 0)
        payment_id = payment_obj.get('id') or payment_obj.get('reference')
        if payment_id:
            seen_payment_ids.add(str(payment_id))
        payment_settle_status = payment_obj.get('settleStatus') or payment_obj.get('status')
        if not payment_date_str:
            payment_date_str = parse_to_date_str(payment_obj.get('createdOn') or payment_obj.get('date'))

    # Check line item level payments if not already captured
    for item in line_items:
        it_pay = item.get('payment')
        if isinstance(it_pay, dict):
            p_id = it_pay.get('id') or it_pay.get('reference')
            p_id_str = str(p_id) if p_id else None
            
            # If this payment was already added from root or another line item sharing the same transaction, do not duplicate amount
            if p_id_str and p_id_str in seen_payment_ids:
                if not payment_method:
                    payment_method = it_pay.get('paymentMethod')
                continue
            if p_id_str:
                seen_payment_ids.add(p_id_str)

            if not payment_method:
                payment_method = it_pay.get('paymentMethod')
            payment_amount += float(it_pay.get('amount') or 0)
            if not payment_id:
                payment_id = p_id
            if not payment_settle_status:
                payment_settle_status = it_pay.get('settleStatus')
            if not payment_date_str:
                payment_date_str = parse_to_date_str(it_pay.get('createdOn'))

    total_face = float(payload.get('faceTotal') or payload.get('total') or sum(i.get('amount', 0) for i in items_list))
    if invoice_status == 'Paid':
        if payment_amount <= 0 or payment_amount > total_face:
            payment_amount = total_face
        if not payment_date_str:
            payment_date_str = invoice_date_str
        if not payment_method:
            payment_method = "Online Payment"
    else:
        if payment_amount > total_face:
            payment_amount = total_face

    payment_dict = None
    if invoice_status == 'Paid' or payment_amount > 0:
        payment_dict = {
            "id": payment_id or f"PAY-{invoice_id}",
            "amount": round(payment_amount, 2),
            "method": payment_method or "Online Payment",
            "reference": str(payment_id or f"GlueUp-{invoice_number}"),
            "date": payment_date_str or invoice_date_str,
            "settle_status": payment_settle_status or "Settle"
        }

    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "date": invoice_date_str,
        "due_date": due_date_str,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": invoice_status,
        "company_name": company_name,
        "contact_name": contact_name,
        "contact_first_name": purchaser_given,
        "contact_last_name": purchaser_family,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "billing_address": billing_address,
        "items": items_list,
        "payment": payment_dict,
        "payment_completion_date": payment_completion_date_str or (payment_date_str if invoice_status == 'Paid' else None)
    }

@invoice_bp.route('/api/invoices/mock', methods=['GET'])
def get_mock_invoices_route():
    """Returns mock invoices generated directly from Glue Up API models."""
    try:
        api = GlueUpAPI()
        if not api.has_mock_data():
            return jsonify({
                "status": "error",
                "message": "Mock data file (mock_invoices.json) not found. System is running in production mode."
            }), 404
        raw_mock = api.get_mock_invoices()
        processed = [format_glueup_invoice(inv) for inv in raw_mock]
        return jsonify({
            "status": "success",
            "message": f"Loaded {len(processed)} realistic mockup invoices modeled from Glue Up API",
            "source": "mock",
            "data": processed
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@invoice_bp.route('/api/invoices/fetch', methods=['GET', 'POST'])
def fetch_invoices():
    try:
        api = GlueUpAPI()
        has_mock = api.has_mock_data()
        
        # Source defaults to 'mock' if mock data exists, otherwise 'live' (production)
        default_source = 'mock' if has_mock else 'live'
        source = request.args.get('source', default_source)
        if source == 'mock' and not has_mock:
            source = 'live'
        
        today = datetime.now()
        first_of_month = today.replace(day=1).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")
        
        from_date = request.args.get('from_date')
        to_date = request.args.get('to_date')

        if source == 'mock':
            raw_mock = api.get_mock_invoices()
            processed_invoices = [format_glueup_invoice(inv) for inv in raw_mock]
            
            # Optional date filtering on mock data if user explicitly provided dates
            if from_date or to_date:
                filtered = []
                for inv in processed_invoices:
                    d = inv.get('date', '')
                    if from_date and d < from_date:
                        continue
                    if to_date and d > to_date:
                        continue
                    filtered.append(inv)
                processed_invoices = filtered

            return jsonify({
                "status": "success",
                "message": f"Loaded {len(processed_invoices)} mock invoices from Glue Up API model",
                "source": "mock",
                "data": processed_invoices
            })

        # Live Glue Up API branch
        raw_invoices = api.get_all_invoices()
        if not from_date:
            from_date = first_of_month
        if not to_date:
            to_date = today_str

        processed_invoices = []
        for payload in raw_invoices:
            # Check date range
            raw_date = payload.get('issueDate') or payload.get('createdOn') or payload.get('createdDate') or payload.get('date')
            if not raw_date:
                continue
                
            try:
                if isinstance(raw_date, (int, float)):
                    inv_date_str = datetime.fromtimestamp(raw_date / 1000.0).strftime("%Y-%m-%d")
                else:
                    inv_date_str = str(raw_date)[:10]
            except Exception:
                continue

            if from_date and inv_date_str < from_date:
                continue
            if to_date and inv_date_str > to_date:
                continue

            invoice_id = payload.get('id') or payload.get('invoiceId')
            # Fetch detailed invoice payload to extract nested line items if needed
            if invoice_id and not payload.get('items'):
                try:
                    detailed = api.get_invoice_details(invoice_id)
                    if detailed:
                        payload = detailed
                except Exception as e:
                    print(f"Warning: Failed to fetch details for invoice {invoice_id}: {e}")

            formatted = format_glueup_invoice(payload)
            processed_invoices.append(formatted)

        return jsonify({
            "status": "success", 
            "message": f"Fetched and processed {len(processed_invoices)} live invoices from Glue Up API",
            "source": "live",
            "data": processed_invoices
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@invoice_bp.route('/invoices', methods=['GET'])
def show_invoices():
    """Shows the invoices page UI"""
    api = GlueUpAPI()
    return render_template("invoices.html", has_mock_data=api.has_mock_data())

