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

    def get_recent_events(self, months=6):
        """Fetches all events and filters for the last N months in Python."""
        endpoint = "/event/list"
        url = f"{self.base_url}{endpoint}"
        
        six_months_ago = datetime.now(timezone.utc) - timedelta(days=30 * months)
        six_months_ago_ms = int(six_months_ago.timestamp() * 1000)
        
        payload = {
            "projection": ["id", "title", "startDateTime", "endDateTime", "published"],
            "limit": 500,  # Grab a large batch to ensure we cover 6 months
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
            all_events = response.json().get('value', [])
            
            # Post-filter in Python
            recent_events = []
            for ev in all_events:
                start_time = ev.get('startDateTime')
                if start_time and start_time >= six_months_ago_ms:
                    recent_events.append(ev)
                    
            return recent_events
        
        print(f"Error fetching recent events: {response.status_code} - {response.text}")
        return []

if __name__ == "__main__":
    load_dotenv()
    api = GlueUpAPI()
    
    print(f"--- Fetching Events from the Past 6 Months ---")
    events = api.get_recent_events(months=6)
    
    if events:
        print(f"\nFound {len(events)} events:")
        for ev in events:
            # Convert epoch time (ms) to readable format if available
            date_str = ev.get('startDateTime', 'Unknown')
            if isinstance(date_str, int):
                date_str = datetime.fromtimestamp(date_str / 1000.0, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
                
            print(f" - [{date_str}] {ev.get('title', 'Untitled')} (ID: {ev.get('id')})")
    else:
        print("No events found.")
