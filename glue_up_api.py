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
            "projection": ["id", "title", "startDateTime", "endDateTime", "published"],
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
            "projection": ["id", "givenName", "familyName", "emailAddress"],
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
        only registered for exactly one event EVER, and that event was > 6 months ago.
        """
        events = self.get_all_events()
        if not events:
            return []
            
        attendee_data = {}
        for ev in events:
            event_id = ev.get('id')
            event_time = ev.get('startDateTime', 0)
            
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
                        "latest_event_time": 0
                    }
                
                attendee_data[email]["count"] += 1
                if event_time > attendee_data[email]["latest_event_time"]:
                    attendee_data[email]["latest_event_time"] = event_time
                    
        # Calculate the cutoff timestamp (6 months ago)
        six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
        cutoff_ms = int(six_months_ago.timestamp() * 1000)
        
        inactive_contacts = []
        for email, data in attendee_data.items():
            if data["count"] == 1 and data["latest_event_time"] < cutoff_ms:
                date_str = datetime.fromtimestamp(data["latest_event_time"] / 1000.0, tz=timezone.utc).strftime('%Y-%m-%d')
                inactive_contacts.append({
                    "email": email,
                    "name": data["name"],
                    "event_date": date_str
                })
                
        return inactive_contacts

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
