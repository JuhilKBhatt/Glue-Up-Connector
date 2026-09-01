import os
import requests
import time
import jwt
from datetime import datetime, timedelta, timezone

class GlueUpAPI:
    def __init__(self):
        # Updated base URL to v2
        self.base_url = os.environ.get("GLUE_UP_API_URL", "https://api.glueup.com/v2")
        self.public_key = os.environ.get("GLUE_UP_PUBLIC_KEY")
        self.private_key = os.environ.get("GLUE_UP_PRIVATE_KEY")

        if not self.public_key or not self.private_key:
            raise ValueError("GLUE_UP_PUBLIC_KEY or GLUE_UP_PRIVATE_KEY is not set in environment.")

    def get_headers(self, method="GET", endpoint="/user"):
        """
        Generates the digest authentication header 'a' for Glue Up v2 API.
        Format: a: v=1.0;k={YOUR_PUBLIC_KEY};ts={TIMESTAMP};d={DIGEST}
        """
        import time
        import hmac
        import hashlib
        
        ts = str(int(time.time() * 1000))
        version = "1.0"
        
        # Based on Glue Up Java reference code:
        # baseString = requestMethod + publicKey + version + timestamp
        base_string = f"{method}{self.public_key}{version}{ts}"
        
        # The digest is an HMAC-SHA256 hash using the privateKey as the secret
        signing_key = self.private_key.encode('utf-8')
        digest = hmac.new(
            signing_key, 
            base_string.encode('utf-8'), 
            hashlib.sha256
        ).hexdigest()
        
        return {
            "a": f"v={version};k={self.public_key};ts={ts};d={digest}",
            "Content-Type": "application/json"
        }

    def fetch_all_contacts(self):
        """Fetches all contacts from Glue Up v2 API."""
        url = f"{self.base_url}/user"
        response = requests.get(url, headers=self.get_headers("GET", "/user"))
        response.raise_for_status()
        return response.json().get('data', [])

    def fetch_contact_activities(self, contact_id):
        """Fetches activities for a specific contact."""
        endpoint = f"/user/{contact_id}/activities"
        url = f"{self.base_url}{endpoint}"
        response = requests.get(url, headers=self.get_headers("GET", endpoint))
        response.raise_for_status()
        return response.json().get('data', [])

    def get_inactive_contacts(self):
        """
        Gets contacts and flags those who haven't registered for an event
        in the last 6 months.
        """
        contacts = self.fetch_all_contacts()
        six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
        
        flagged_contacts = []

        for contact in contacts:
            contact_id = contact.get('id')
            if not contact_id:
                continue

            try:
                activities = self.fetch_contact_activities(contact_id)
            except Exception as e:
                print(f"Failed to fetch activities for {contact_id}: {e}")
                continue

            has_recent_event = False
            for activity in activities:
                activity_type = activity.get('type')
                activity_date_str = activity.get('date')
                
                if activity_type == 'event_registration' and activity_date_str:
                    try:
                        # Assuming ISO 8601 format date like 'YYYY-MM-DDTHH:MM:SSZ'
                        activity_date = datetime.fromisoformat(activity_date_str.replace('Z', '+00:00'))
                        if activity_date >= six_months_ago:
                            has_recent_event = True
                            break
                    except ValueError:
                        pass
            
            if not has_recent_event:
                contact['flagged_reason'] = "No event registration in the last 6 months"
                flagged_contacts.append(contact)

        return flagged_contacts

    def get_contact_crm_profile(self, user_id):
        """Fetches core profile details and custom CRM fields."""
        endpoint = f"/user/{user_id}"
        url = f"{self.base_url}{endpoint}"
        response = requests.get(url, headers=self.get_headers("GET", endpoint))
        if response.status_code == 200:
            return response.json()
        print(f"Error fetching CRM data: {response.status_code}")
        return None

    def get_contact_event_activities(self, user_id):
        """Fetches event registrations, tickets, and check-in statuses."""
        endpoint = "/event/registrations"
        url = f"{self.base_url}{endpoint}"
        params = {"user_id": user_id}  # Filters data specifically for this user
        response = requests.get(url, headers=self.get_headers("GET", endpoint), params=params)
        if response.status_code == 200:
            return response.json()
        print(f"Error fetching Event activities: {response.status_code}")
        return None

    def get_contact_financial_activities(self, user_id):
        """Fetches payment histories, invoices, and billing activities."""
        endpoint = "/finance/invoices"
        url = f"{self.base_url}{endpoint}"
        params = {"user_id": user_id}
        response = requests.get(url, headers=self.get_headers("GET", endpoint), params=params)
        if response.status_code == 200:
            return response.json()
        print(f"Error fetching Finance activities: {response.status_code}")
        return None

    def compile_user_timeline(self, user_id):
        print(f"--- Compiling Activity Timeline for User: {user_id} ---")
        
        # Fetch data across the different collections
        profile = self.get_contact_crm_profile(user_id)
        events = self.get_contact_event_activities(user_id)
        finances = self.get_contact_financial_activities(user_id)
        
        # Process or merge timelines here
        if profile:
            data = profile.get('data', {})
            name = data.get('name') or data.get('displayName') or 'Unknown'
            org = data.get('organization') or 'Unknown Organization'
            print(f"\n[CRM Profile Summary]: {name} from {org}")
            
        if events:
            records = events.get('data', [])
            print(f"\n[Event Activities Found]: {len(records)} records.")
            for ev in records:
                print(f" - {ev.get('name', 'Event')} ({ev.get('date', 'Unknown date')})")
            
        if finances:
            records = finances.get('data', [])
            print(f"\n[Financial Activities Found]: {len(records)} records.")
            for fin in records:
                print(f" - Invoice {fin.get('id', 'N/A')}: {fin.get('status', 'Unknown status')}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    import sys
    # For testing directly from command line
    user_id = sys.argv[1] if len(sys.argv) > 1 else "12345"
    api = GlueUpAPI()
    api.compile_user_timeline(user_id)
