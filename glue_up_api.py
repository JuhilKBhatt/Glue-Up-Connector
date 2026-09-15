import os
import requests
import time
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from dotenv import load_dotenv

class GlueUpAPI:
    def __init__(self):
        self.base_url = os.environ.get("GLUE_UP_API_URL", "https://api-services.glueup.com/v2")
        self.public_key = os.environ.get("GLUE_UP_PUBLIC_KEY")
        self.private_key = os.environ.get("GLUE_UP_PRIVATE_KEY")

        if not self.public_key or not self.private_key:
            raise ValueError("GLUE_UP_PUBLIC_KEY or GLUE_UP_PRIVATE_KEY is not set in environment.")

    def get_headers(self, method="GET", endpoint="/"):
        """Generates the digest authentication header 'a' for Glue Up v2 API."""
        ts = str(int(time.time() * 1000))
        version = "1.0"
        
        url = f"{self.base_url}{endpoint}"
        path = urlparse(url).path
        
        base_string = f"{method}{self.public_key}{version}{ts}"
        signing_key = self.private_key.encode('utf-8')
        digest = hmac.new(signing_key, base_string.encode('utf-8'), hashlib.sha256).hexdigest()
        
        return {
            "a": f"v={version};k={self.public_key};ts={ts};d={digest}",
            "Content-Type": "application/json"
        }

    def get_all_events(self):
        """Fetches all published events in the organization's history."""
        endpoint = "/event/list"
        url = f"{self.base_url}{endpoint}"
        
        payload = {
            "projection": ["id", "title", "startDateTime", "endDateTime", "published", "openToPublic"],
            "limit": 500,  # Their total history is ~115 events, so 500 covers everything
            "offset": 0,
            "filter": [
                {
                    "projection": "published",
                    "operator": "eq",
                    "values": [True]
                }
            ],
            "order": {"startDateTime": "desc"}
        }
        
        response = requests.post(url, headers=self.get_headers("POST", endpoint), json=payload)
        
        if response.status_code == 200:
            return response.json().get('value', [])
        
        print(f"Error fetching events: {response.status_code} - {response.text}")
        return []

    def get_event_attendees(self, event_id):
        """Fetches the list of attendees for a specific event."""
        endpoint = f"/event/{event_id}/attendeeList"
        url = f"{self.base_url}{endpoint}"
        
        payload = {
            "projection": [
                "id", "givenName", "familyName", "emailAddress", "contactId", 
                "company", "title", "jobTitle", "workPhone", "mobilePhone", 
                "status", "ticket", "answers", "customFields", "registration", "organization"
            ],
            "limit": 1000,
            "offset": 0
        }
        
        response = requests.post(url, headers=self.get_headers("POST", endpoint), json=payload)
        
        if response.status_code == 200:
            return response.json().get('value', [])
            
        print(f"Error fetching attendees for event {event_id}: {response.status_code} - {response.text}")
        return []

    def find_inactive_contacts(self):
        """
        Fetches all events and attendees, returning a list of contacts who have
        only registered for exactly one event EVER. Provides metadata for frontend filtering.
        """
        events = self.get_all_events()
        if not events:
            return []
            
        attendee_data = {}
        for ev in events:
            event_id = ev.get('id')
            event_time = ev.get('startDateTime', 0)
            event_title = ev.get('title', 'Unknown Event')
            is_public = ev.get('openToPublic', True)
            
            attendees = self.get_event_attendees(event_id)
            for att in attendees:
                email_obj = att.get("emailAddress", {})
                email = email_obj.get("value", "") if isinstance(email_obj, dict) else email_obj
                
                if not email:
                    continue
                    
                email = email.lower().strip()
                
                if email not in attendee_data:
                    attendee_data[email] = {
                        "name": f"{att.get('givenName', '')} {att.get('familyName', '')}".strip(),
                        "count": 0,
                        "latest_event_time": 0,
                        "contact_id": att.get("contactId", "N/A"),
                        "event_title": "",
                        "is_public": True,
                        "raw_data": att,
                        "all_events": []
                    }
                
                attendee_data[email]["count"] += 1
                
                # Keep a log of every event they attended
                date_str = datetime.fromtimestamp(event_time / 1000.0, tz=timezone.utc).strftime('%Y-%m-%d')
                attendee_data[email]["all_events"].append(f"{date_str}: {event_title}")
                
                if event_time >= attendee_data[email]["latest_event_time"]:
                    attendee_data[email]["latest_event_time"] = event_time
                    attendee_data[email]["event_title"] = event_title
                    attendee_data[email]["is_public"] = is_public
                    # Keep the raw data of their latest registration
                    attendee_data[email]["raw_data"] = att
                    
        all_contacts = []
        for email, data in attendee_data.items():
            date_str = datetime.fromtimestamp(data["latest_event_time"] / 1000.0, tz=timezone.utc).strftime('%Y-%m-%d')
            all_contacts.append({
                "email": email,
                "name": data["name"],
                "event_date": date_str,
                "event_time_ms": data["latest_event_time"],
                "event_title": data["event_title"],
                "is_public": data["is_public"],
                "contact_id": data["contact_id"],
                "total_events_attended": data["count"],
                "all_events": data["all_events"],
                "raw_data": data["raw_data"]
            })
            
        return all_contacts

    def get_all_invoices(self):
        """Fetches all invoices (or orders) in the organization's history."""
        # Try the most likely endpoints for invoices
        endpoints = ["/finance/invoice/list", "/invoice/list", "/finance/order/list", "/order/list"]
        
        payload = {
            "limit": 1000,
            "offset": 0,
            "order": {"createdDate": "desc"} # Assuming standard sorting
        }
        
        for endpoint in endpoints:
            url = f"{self.base_url}{endpoint}"
            # Some list endpoints in Glue Up might be POST with payload, or GET. The event list is POST.
            response = requests.post(url, headers=self.get_headers("POST", endpoint), json=payload)
            
            if response.status_code == 200:
                return response.json().get('value', [])
                
        print(f"Error fetching invoices. Last response: {response.status_code} - {response.text}")
        return []

    def get_invoice_details(self, invoice_id):
        """Fetches the details of a specific invoice to retrieve its line items."""
        endpoint = f"/finance/invoice/{invoice_id}"
        url = f"{self.base_url}{endpoint}"
        
        response = requests.get(url, headers=self.get_headers("GET", endpoint))
        
        if response.status_code == 200:
            return response.json().get('value', {})
            
        print(f"Error fetching invoice {invoice_id}: {response.status_code} - {response.text}")
        return {}

    def delete_contact(self, contact_id):
        """
        Executes a signed custom API script deletion against the User/Contact endpoint.
        """
        # The resource endpoint pattern used inside Glue Up's User Collection
        endpoint = f"/user/contacts/{contact_id}"
        full_url = f"{self.base_url}{endpoint}"
        
        # Generate the request signature specific to this DELETE event
        headers = self.get_headers("DELETE", endpoint)
        
        try:
            print(f"Sending signed deletion request for Contact ID: {contact_id}...")
            response = requests.delete(full_url, headers=headers)
            
            # Evaluate standard API lifecycle HTTP status codes
            if response.status_code in [200, 204]:
                print(f"Success: Contact {contact_id} successfully deleted via custom script.")
                return True, f"Contact {contact_id} successfully deleted"
            elif response.status_code == 401:
                print("Error 401: Unauthorised. Verify your Public/Private keys and digest generation.")
                return False, "Unauthorised API credentials"
            elif response.status_code == 404:
                print(f"Error 404: Contact ID {contact_id} does not exist in your CRM database.")
                return False, f"Contact {contact_id} not found or endpoint invalid"
            else:
                msg = f"Failed with Status Code {response.status_code}: {response.text}"
                print(msg)
                return False, msg
                
        except requests.exceptions.RequestException as error:
            msg = f"A connection failure occurred: {error}"
            print(msg)
            return False, msg

    def get_mock_invoices(self):
        """
        Generates realistic mockup invoices modeled directly from real Glue Up API payloads,
        AJBCC events, members, and ticket types. Useful for testing UI, filters, and Xero sync
        without waiting for slow live API calls or modifying production accounting.
        """
        now = datetime.now()
        
        # Calculate dynamic dates relative to today
        def offset_ms(days_delta):
            return int((now + timedelta(days=days_delta)).timestamp() * 1000)

        mock_data = [
            {
                "id": 12836418,
                "number": "MEM002041",
                "organizationId": 5424,
                "title": "AJBCC Tax Invoice",
                "currency": "AUD",
                "faceTotal": 11715.0,
                "balanceDue": 11715.0,
                "proforma": False,
                "voided": False,
                "createdOn": offset_ms(-2),
                "issueDate": offset_ms(-2),
                "dueDate": offset_ms(28),
                "purchaserGivenName": "Anne-Marie",
                "purchaserFamilyName": "Johnson",
                "purchaserEmail": "membership@ajbcc.com.au",
                "purchaserPhone": "+61 2 6230 5600",
                "purchaserCompanyName": "Australia Japan Business Co-operation Committee Limited",
                "purchaserAddress": "Level 3, 10-12 Brisbane Avenue",
                "purchaserCity": "Barton",
                "purchaserState": "ACT",
                "purchaserPostalCode": "2600",
                "purchaserCountry": "Australia",
                "origin": "MembershipApplication",
                "status": "Unpaid",
                "items": [
                    {
                        "id": 14444043,
                        "organizationId": 5424,
                        "invoiceId": 12836418,
                        "name": "Membership Application - Ordinary",
                        "status": "Valid",
                        "type": "MembershipApplication",
                        "description": "Membership Application (Nominated Representative)",
                        "paidStatus": "Unpaid",
                        "faceValue": 3630.0,
                        "quantity": 1.0,
                        "createdOn": offset_ms(-2)
                    },
                    {
                        "id": 14444044,
                        "organizationId": 5424,
                        "invoiceId": 12836418,
                        "name": "Additional Member Package",
                        "status": "Valid",
                        "type": "AdditionalMember",
                        "description": "Additional Member (3 Representatives)",
                        "paidStatus": "Unpaid",
                        "faceValue": 8085.0,
                        "quantity": 3.0,
                        "createdOn": offset_ms(-2)
                    }
                ],
                "contacts": [
                    {
                        "id": 1002196,
                        "givenName": "Anne-Marie",
                        "familyName": "Johnson",
                        "emailAddress": {"value": "membership@ajbcc.com.au"},
                        "phone": "+61 2 6230 5600",
                        "companyName": "Australia Japan Business Co-operation Committee Limited"
                    }
                ]
            },
            {
                "id": 12790728,
                "number": "MEM002038",
                "organizationId": 5424,
                "title": "AJBCC Tax Invoice",
                "currency": "AUD",
                "faceTotal": 11990.0,
                "balanceDue": 0.0,
                "proforma": False,
                "voided": False,
                "createdOn": offset_ms(-8),
                "issueDate": offset_ms(-8),
                "dueDate": offset_ms(22),
                "purchaserGivenName": "Richard",
                "purchaserFamilyName": "Andrews",
                "purchaserEmail": "richard.andrews@ajbcc.com.au",
                "purchaserPhone": "+61 2 9230 4000",
                "purchaserCompanyName": "Allens",
                "purchaserAddress": "Deutsche Bank Place, 126 Phillip Street",
                "purchaserCity": "Sydney",
                "purchaserState": "NSW",
                "purchaserPostalCode": "2000",
                "purchaserCountry": "Australia",
                "origin": "MembershipRenewal",
                "status": "Paid",
                "items": [
                    {
                        "id": 14138769,
                        "organizationId": 5424,
                        "invoiceId": 12790728,
                        "name": "Annual Corporate Membership Renewal",
                        "status": "Valid",
                        "type": "MembershipApplication",
                        "description": "Membership Application - Corporate Tier",
                        "paidStatus": "Paid",
                        "faceValue": 6710.0,
                        "quantity": 1.0,
                        "createdOn": offset_ms(-8)
                    },
                    {
                        "id": 14138770,
                        "organizationId": 5424,
                        "invoiceId": 12790728,
                        "name": "Additional Corporate Members",
                        "status": "Valid",
                        "type": "AdditionalMember",
                        "description": "Additional Member Subscriptions",
                        "paidStatus": "Paid",
                        "faceValue": 5280.0,
                        "quantity": 2.0,
                        "createdOn": offset_ms(-8)
                    }
                ],
                "contacts": [
                    {
                        "id": 1002197,
                        "givenName": "Richard",
                        "familyName": "Andrews",
                        "emailAddress": {"value": "richard.andrews@ajbcc.com.au"},
                        "phone": "+61 2 9230 4000",
                        "companyName": "Allens"
                    }
                ]
            },
            {
                "id": 12769018,
                "number": "EV000035",
                "organizationId": 5424,
                "title": "AJBCC Event Invoice",
                "currency": "AUD",
                "faceTotal": 880.0,
                "balanceDue": 880.0,
                "proforma": False,
                "voided": False,
                "createdOn": offset_ms(-35),
                "issueDate": offset_ms(-35),
                "dueDate": offset_ms(-5),  # Past due -> Overdue
                "purchaserGivenName": "Tim",
                "purchaserFamilyName": "Lester",
                "purchaserEmail": "tim.lester@jamesonboyce.com.au",
                "purchaserPhone": "+61 3 9614 7722",
                "purchaserCompanyName": "Jameson Boyce Partners",
                "purchaserAddress": "Level 15, 333 Collins Street",
                "purchaserCity": "Melbourne",
                "purchaserState": "VIC",
                "purchaserPostalCode": "3000",
                "purchaserCountry": "Australia",
                "origin": "EventTicket",
                "status": "Unpaid",
                "items": [
                    {
                        "id": 9165711,
                        "organizationId": 5424,
                        "invoiceId": 12769018,
                        "name": "The 63rd Annual Australia–Japan Joint Business Conference",
                        "status": "Valid",
                        "type": "EventTicket",
                        "description": "Delegate Ticket: 63rd Annual Joint Business Conference",
                        "paidStatus": "Unpaid",
                        "faceValue": 880.0,
                        "quantity": 1.0,
                        "createdOn": offset_ms(-35)
                    }
                ],
                "contacts": [
                    {
                        "id": 1002330,
                        "givenName": "Tim",
                        "familyName": "Lester",
                        "emailAddress": {"value": "tim.lester@jamesonboyce.com.au"},
                        "phone": "+61 3 9614 7722",
                        "companyName": "Jameson Boyce Partners"
                    }
                ]
            },
            {
                "id": 12812473,
                "number": "MEM002042",
                "organizationId": 5424,
                "title": "AJBCC Tax Invoice",
                "currency": "AUD",
                "faceTotal": 9020.0,
                "balanceDue": 9020.0,
                "proforma": False,
                "voided": False,
                "createdOn": offset_ms(-4),
                "issueDate": offset_ms(-4),
                "dueDate": offset_ms(26),
                "purchaserGivenName": "Heidi",
                "purchaserFamilyName": "Han",
                "purchaserEmail": "heidi.han@ebest.com.au",
                "purchaserPhone": "+61 2 8317 1234",
                "purchaserCompanyName": "eBest Pty Ltd",
                "purchaserAddress": "Suite 401, 55 Clarence Street",
                "purchaserCity": "Sydney",
                "purchaserState": "NSW",
                "purchaserPostalCode": "2000",
                "purchaserCountry": "Australia",
                "origin": "MembershipApplication",
                "status": "Unpaid",
                "items": [
                    {
                        "id": 14444050,
                        "organizationId": 5424,
                        "invoiceId": 12812473,
                        "name": "Membership Application - Ordinary",
                        "status": "Valid",
                        "type": "MembershipApplication",
                        "description": "Membership Application (Nominated Representative)",
                        "paidStatus": "Unpaid",
                        "faceValue": 3630.0,
                        "quantity": 1.0,
                        "createdOn": offset_ms(-4)
                    },
                    {
                        "id": 14444051,
                        "organizationId": 5424,
                        "invoiceId": 12812473,
                        "name": "Additional Member (2 Representatives)",
                        "status": "Valid",
                        "type": "AdditionalMember",
                        "description": "Additional Member",
                        "paidStatus": "Unpaid",
                        "faceValue": 5390.0,
                        "quantity": 2.0,
                        "createdOn": offset_ms(-4)
                    }
                ],
                "contacts": [
                    {
                        "id": 1002331,
                        "givenName": "Heidi",
                        "familyName": "Han",
                        "emailAddress": {"value": "heidi.han@ebest.com.au"},
                        "phone": "+61 2 8317 1234",
                        "companyName": "eBest Pty Ltd"
                    }
                ]
            },
            {
                "id": 12780507,
                "number": "CUST000109",
                "organizationId": 5424,
                "title": "AJBCC Custom Invoice",
                "currency": "AUD",
                "faceTotal": 2200.0,
                "balanceDue": 2200.0,
                "proforma": False,
                "voided": False,
                "createdOn": offset_ms(-1),
                "issueDate": offset_ms(-1),
                "dueDate": offset_ms(14),
                "purchaserGivenName": "Samantha",
                "purchaserFamilyName": "Taylor",
                "purchaserEmail": "samantha.taylor@vic.gov.au",
                "purchaserPhone": "+61 3 9651 9999",
                "purchaserCompanyName": "Victorian Government",
                "purchaserAddress": "121 Exhibition Street",
                "purchaserCity": "Melbourne",
                "purchaserState": "VIC",
                "purchaserPostalCode": "3000",
                "purchaserCountry": "Australia",
                "origin": "Custom",
                "status": "Draft",
                "items": [
                    {
                        "id": 10636032,
                        "organizationId": 5424,
                        "invoiceId": 12780507,
                        "name": "Chiba Sake Festival 2026 Sponsorship",
                        "status": "Valid",
                        "type": "Custom",
                        "description": "Major Sponsor Package - Chiba Sake Festival 2026",
                        "paidStatus": "Unpaid",
                        "faceValue": 2200.0,
                        "quantity": 1.0,
                        "createdOn": offset_ms(-1)
                    }
                ],
                "contacts": [
                    {
                        "id": 1000037,
                        "givenName": "Samantha",
                        "familyName": "Taylor",
                        "emailAddress": {"value": "samantha.taylor@vic.gov.au"},
                        "phone": "+61 3 9651 9999",
                        "companyName": "Victorian Government"
                    }
                ]
            },
            {
                "id": 12769138,
                "number": "EV000012",
                "organizationId": 5424,
                "title": "AJBCC Tax Invoice",
                "currency": "AUD",
                "faceTotal": 150.0,
                "balanceDue": 150.0,
                "proforma": False,
                "voided": True,
                "voidReason": "Duplicate ticket booking by member",
                "createdOn": offset_ms(-10),
                "issueDate": offset_ms(-10),
                "dueDate": offset_ms(20),
                "purchaserGivenName": "David",
                "purchaserFamilyName": "Clark",
                "purchaserEmail": "david.clark@gtlaw.com.au",
                "purchaserPhone": "+61 2 9263 4000",
                "purchaserCompanyName": "Gilbert + Tobin",
                "purchaserAddress": "Tower Two, International Towers, 200 Barangaroo Avenue",
                "purchaserCity": "Barangaroo",
                "purchaserState": "NSW",
                "purchaserPostalCode": "2000",
                "purchaserCountry": "Australia",
                "origin": "EventTicket",
                "status": "Voided",
                "items": [
                    {
                        "id": 9165722,
                        "organizationId": 5424,
                        "invoiceId": 12769138,
                        "name": "Future Leaders Forum 2026 Ticket",
                        "status": "Voided",
                        "type": "EventTicket",
                        "description": "Forum Ticket - Cancelled",
                        "paidStatus": "Unpaid",
                        "faceValue": 150.0,
                        "quantity": 1.0,
                        "createdOn": offset_ms(-10)
                    }
                ],
                "contacts": [
                    {
                        "id": 1002340,
                        "givenName": "David",
                        "familyName": "Clark",
                        "emailAddress": {"value": "david.clark@gtlaw.com.au"},
                        "phone": "+61 2 9263 4000",
                        "companyName": "Gilbert + Tobin"
                    }
                ]
            }
        ]
        return mock_data

if __name__ == "__main__":
    load_dotenv()
    api = GlueUpAPI()
    
    print("--- Fetching ALL Events in History ---")
    inactive_contacts = api.find_inactive_contacts()
    
    if inactive_contacts:
        print(f"Inactive Contacts (1 registration EVER, > 6 months ago): {len(inactive_contacts)}\n")
        print("List of Inactive Contacts (Safe to purge for quota):")
        for contact in inactive_contacts:
            print(f" - {contact['name']} ({contact['email']}) | Event Date: {contact['event_date']}")
    else:
        print("No inactive contacts found or no events returned.")
