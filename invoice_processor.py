import json
from datetime import datetime
from flask import Blueprint, jsonify, render_template, request
from glue_up_api import GlueUpAPI

invoice_bp = Blueprint('invoice_bp', __name__)

@invoice_bp.route('/api/invoices/fetch', methods=['GET', 'POST'])
def fetch_invoices():
    try:
        api = GlueUpAPI()
        raw_invoices = api.get_all_invoices()
        
        # Default to the current month (from 1st of month to today) if not provided
        today = datetime.now()
        first_of_month = today.replace(day=1).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")
        
        from_date = request.args.get('from_date', first_of_month)
        to_date = request.args.get('to_date', today_str)
        
        processed_invoices = []
        for payload in raw_invoices:
            # 1. Filter for the target date's invoices
            # Date field might be createdDate, issueDate, date, etc.
            invoice_date = payload.get('createdDate') or payload.get('issueDate') or payload.get('date')
            
            invoice_date_str = None
            if invoice_date:
                try:
                    if isinstance(invoice_date, (int, float)):
                        # Assume Unix timestamp in ms (standard for Glue Up)
                        dt = datetime.fromtimestamp(invoice_date / 1000.0)
                        invoice_date_str = dt.strftime("%Y-%m-%d")
                    else:
                        # Assume string like "2026-09-08T..."
                        invoice_date_str = str(invoice_date)[:10]
                except Exception:
                    pass
                    
            # Skip if outside of the date range
            if not invoice_date_str:
                continue
                
            if from_date and invoice_date_str < from_date:
                continue
            if to_date and invoice_date_str > to_date:
                continue
                
            # We try to extract common fields
            invoice_id = payload.get('id') or payload.get('invoiceId') or payload.get('orderId') or payload.get('uuid') or 'Unknown'
            
            # The list endpoint doesn't return items, so we fetch the detailed invoice
            if invoice_id != 'Unknown':
                detailed_payload = api.get_invoice_details(invoice_id)
                if detailed_payload:
                    payload = detailed_payload
                    
            # Calculate Status
            is_voided = payload.get('voided', False)
            balance_due = float(payload.get('balanceDue') or 0)
            glueup_status = payload.get('status')
            
            due_date_ms = payload.get('dueDate')
            due_date_str = None
            if due_date_ms:
                try:
                    if isinstance(due_date_ms, (int, float)):
                        due_date_str = datetime.fromtimestamp(due_date_ms / 1000.0).strftime("%Y-%m-%d")
                    else:
                        due_date_str = str(due_date_ms)[:10]
                except Exception:
                    pass
            if not due_date_str:
                due_date_str = invoice_date_str
            
            now_ms = datetime.now().timestamp() * 1000
            
            if is_voided or glueup_status in ['Voided', 'Void', 'Canceled']:
                invoice_status = 'Void'
            elif glueup_status == 'Draft':
                invoice_status = 'Draft'
            elif balance_due <= 0:
                invoice_status = 'Paid'
            elif due_date_ms and float(due_date_ms) < now_ms:
                invoice_status = 'Overdue'
            else:
                invoice_status = 'Awaiting Payment'
                    
            line_items = payload.get('items') or payload.get('lineItems') or payload.get('line_items') or []
            
            # Extract Company and Contact info
            company_obj = payload.get('company', {})
            company_name = company_obj.get('name') if isinstance(company_obj, dict) else None
            
            contacts_list = payload.get('contacts', [])
            contact_name = None
            if contacts_list and len(contacts_list) > 0:
                first_contact = contacts_list[0]
                given_name = first_contact.get('givenName', '')
                family_name = first_contact.get('familyName', '')
                contact_name = f"{given_name} {family_name}".strip()
            
            # Formatted payload with a flat list of line items
            formatted_payload = {
                "invoice_id": invoice_id,
                "date": invoice_date_str,
                "due_date": due_date_str,
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": invoice_status,
                "company_name": company_name,
                "contact_name": contact_name,
                "items": []
            }
            
            for item in line_items:
                # In detailed payload, type is often explicit e.g. "MembershipApplication"
                item_type = item.get('type', 'Standard')
                description = item.get('description', item.get('name', ''))
                
                # If description is empty but we have a type, use type as description
                if not description:
                    if item_type == 'MembershipApplication':
                        description = 'Membership Application'
                    elif item_type == 'AdditionalMember':
                        description = 'Additional Member'
                    else:
                        description = item_type
                
                # Format the type for the badge
                badge_type = "Standard"
                desc_lower = description.lower()
                type_lower = item_type.lower()
                
                if "additional" in type_lower or "additional" in desc_lower or "extra" in type_lower or "extra" in desc_lower:
                    badge_type = "Additional Member"
                elif "application" in type_lower or "application" in desc_lower:
                    badge_type = "Membership Application"
                
                amount = item.get('faceValue') or item.get('amount') or item.get('total') or 0
                quantity = item.get('quantity') or item.get('count') or 1.0
                
                # Map to Xero accounts based on the line item type
                account_name = item.get('account') or item.get('accountCode') or item.get('accountingCode') or 'Uncategorized'
                
                if badge_type == "Membership Application":
                    account_name = "Membership Subscriptions - Ordinary - Nominated"
                elif badge_type == "Additional Member":
                    account_name = "Membership Subscriptions - Ordinary - Additional"
                
                formatted_payload["items"].append({
                    "description": description,
                    "amount": amount,
                    "quantity": float(quantity),
                    "type": badge_type,
                    "account": account_name
                })
                
            processed_invoices.append(formatted_payload)
            
        return jsonify({
            "status": "success", 
            "message": f"Fetched and processed {len(processed_invoices)} invoices for today",
            "data": processed_invoices
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@invoice_bp.route('/invoices', methods=['GET'])
def show_invoices():
    """Shows the invoices page UI"""
    return render_template("invoices.html")
