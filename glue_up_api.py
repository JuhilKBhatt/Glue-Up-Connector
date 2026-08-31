import os
import requests
from datetime import datetime, timedelta, timezone

class GlueUpAPI:
    def __init__(self):
        self.base_url = os.environ.get("GLUE_UP_API_URL")
        self.api_key = os.environ.get("GLUE_UP_API_KEY")
        self.api_secret = os.environ.get("GLUE_UP_API_SECRET")

        if not self.api_key or not self.api_secret:
            raise ValueError("GLUE_UP_API_KEY or GLUE_UP_API_SECRET is not set in environment.")

    def get_headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def fetch_all_contacts(self):
        """Fetches all contacts from Glue Up."""
        url = f"{self.base_url}/contacts"
        response = requests.get(url, headers=self.get_headers())
        response.raise_for_status()
        # The exact response structure depends on the API, usually it's under 'data' or 'items'
        return response.json().get('data', [])

    def fetch_contact_activities(self, contact_id):
        """Fetches activities for a specific contact."""
        # Note: adjust the endpoint according to actual Glue Up API docs
        url = f"{self.base_url}/contacts/{contact_id}/activities"
        response = requests.get(url, headers=self.get_headers())
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
                # Adjust 'type' and 'date' fields based on actual API payload structure
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
