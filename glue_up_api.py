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

    def _generate_token(self):
        """
        Generates an authentication token using the private key.
        This assumes a standard JWT (JSON Web Token) RS256 signature for API v2.
        """
        try:
            # Note: The exact payload claims ('iss', 'sub', 'aud') may need to 
            # match Glue Up API v2 specifications. 
            payload = {
                "iss": self.public_key,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600 # Token valid for 1 hour
            }
            # Handle potential escaped newlines from .env variables
            formatted_private_key = self.private_key.replace('\\n', '\n')
            
            # Ensure it has PEM formatting
            if "-----BEGIN" not in formatted_private_key:
                # Break the base64 string into 64-character chunks just in case
                import textwrap
                key_body = "\n".join(textwrap.wrap(formatted_private_key.replace(" ", ""), 64))
                formatted_private_key = f"-----BEGIN PRIVATE KEY-----\n{key_body}\n-----END PRIVATE KEY-----"

            token = jwt.encode(payload, formatted_private_key, algorithm="RS256")
            return token
        except Exception as e:
            print(f"Error generating token: {e}")
            raise ValueError(f"Failed to generate authentication token: {e}")

    def get_headers(self):
        token = self._generate_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def fetch_all_contacts(self):
        """Fetches all contacts from Glue Up v2 API."""
        url = f"{self.base_url}/user"
        response = requests.get(url, headers=self.get_headers())
        response.raise_for_status()
        return response.json().get('data', [])

    def fetch_contact_activities(self, contact_id):
        """Fetches activities for a specific contact."""
        url = f"{self.base_url}/user/{contact_id}/activities"
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
