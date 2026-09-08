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
        
        # Get requested date from query params, default to today
        target_date = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
        
        processed_invoices = []
        for payload in raw_invoices:
            # 1. Filter for the target date's invoices
            # Date field might be createdDate, issueDate, date, etc.
            invoice_date = payload.get('createdDate') or payload.get('issueDate') or payload.get('date')
            
            is_target_date = False
            if invoice_date:
                try:
                    if isinstance(invoice_date, (int, float)):
                        # Assume Unix timestamp in ms (standard for Glue Up)
                        dt = datetime.fromtimestamp(invoice_date / 1000.0)
                        is_target_date = dt.strftime("%Y-%m-%d") == target_date
                    else:
                        # Assume string like "2026-09-08T..."
                        is_target_date = str(invoice_date).startswith(target_date)
                except Exception:
                    pass
                    
            # Skip if it's not from the target date (unless target_date is 'all')
            if target_date != 'all' and not is_target_date:
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
            due_date = payload.get('dueDate')
            
            # Use current time to check overdue
            now_ms = datetime.now().timestamp() * 1000
            
            if is_voided:
                invoice_status = 'Void'
            elif balance_due <= 0:
                invoice_status = 'Paid'
            elif due_date and float(due_date) < now_ms:
                invoice_status = 'Overdue'
            else:
                invoice_status = 'Unpaid'
                    
            line_items = payload.get('items') or payload.get('lineItems') or payload.get('line_items') or []
            
            # Formatted payload with a flat list of line items
            formatted_payload = {
                "invoice_id": invoice_id,
                "date": str(invoice_date),
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": invoice_status,
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
                
                if "additional" in type_lower or "additional" in desc_lower:
                    badge_type = "Additional Member"
                elif "application" in type_lower or "application" in desc_lower:
                    badge_type = "Membership Application"
                
                amount = item.get('faceValue') or item.get('amount') or item.get('total') or 0
                
                # Map to Xero accounts based on the line item type
                account_name = item.get('account') or item.get('accountCode') or item.get('accountingCode') or 'Uncategorized'
                
                if badge_type == "Membership Application":
                    account_name = "Membership Subscriptions - Ordinary - Nominated"
                elif badge_type == "Additional Member":
                    account_name = "Membership Subscriptions - Ordinary - Additional"
                
                formatted_payload["items"].append({
                    "description": description,
                    "amount": amount,
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
