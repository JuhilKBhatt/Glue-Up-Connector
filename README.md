# Glue Up & Xero API Connector

A robust Python/Flask integration suite designed to extend the **Glue Up Open API v2** and synchronize it with **Xero Accounting**. This connector handles complex Digest Authentication (HMAC-SHA256) for Glue Up, OAuth2 for Xero, and provides a beautiful web dashboard for automating CRM hygiene and financial syncing.

---

## 🌟 Core Features

### 1. Xero Invoice Synchronization (`/invoices`)
- **Automated Sync:** Fetches daily invoices from Glue Up and mirrors them into Xero as `AUTHORISED` (for paid/unpaid) or `DRAFT` invoices.
- **Intelligent Financial Mapping:** 
  - Translates Glue Up "Final Totals" back into "Unit Rates" for Xero.
  - Forces `Tax Inclusive` line items to prevent double-taxation.
  - Dynamically routes specific Glue Up ticket types (e.g., "Additional Member", "Membership Application") to distinct Xero General Ledger Chart of Account codes (`2004` and `2005`).
- **OAuth2 Auto-Refresh:** Maintains a persistent background connection to Xero, automatically refreshing expired API tokens mid-sync.

### 2. CRM Cleanup & Event Auditing (`/`)
- **Global Event Parsing:** Pings the Glue Up API to download the entire historical roster of events and attendees.
- **Dormant Contact Analysis:** Aggregates and counts the total number of events every contact has ever attended.
- **Excel Export with Activity Data:** Select inactive users and download an `.xlsx` file. The tool automatically unpacks nested JSON payloads from Glue Up to provide clean, readable columns for `Attendance Status`, `Ticket Type`, and `Registration Date`.

### 3. Smart Archiving Module (`/archiving`)
- **Automated Cross-Referencing:** Upload a raw CRM Excel export containing users flagged with `.archive`, `-archive`, or `.dormant` in their email addresses.
- **Memory-Mapping:** The backend strips the archive tags, hunts down the user's original profile in the Glue Up API, and extracts their lifetime event registration footprint.
- **Safe Archival:** Spits out a pristine `.xlsx` backup of your archived contacts, fully enriched with their historical event activity before you manually delete them from Glue Up.

---

## 📁 File Structure & Logic

- **`main.py`**: The core Flask web server. Mounts the API routes, renders the HTML templates, and enforces basic HTTP authentication.
- **`glue_up_api.py`**: The custom REST client for Glue Up v2. Handles the complex HMAC-SHA256 digest signature generation (`get_headers`), fetches event timelines (`get_all_events`), attendee lists, and calculates inactive contacts.
- **`xero_api.py`**: A dedicated Blueprint and API client for Xero. Manages OAuth2 token exchanges (with `Flask session` token savers), builds the complex `xero_python.accounting.models.Invoice` payloads, handles duplicate checking via the `Reference` field, and manages API rate-limit/token-refresh retries.
- **`invoice_processor.py`**: A Blueprint that sits between Glue Up and Xero. It parses the dense Glue Up finance payloads, extracts nested company names, maps custom ticket types to Xero Account codes, and formats the data for the frontend UI.
- **`templates/`**: Contains the Bootstrap 5 frontend UI.
  - `base.html`: The master layout and navigation sidebar.
  - `index.html`: The CRM Cleanup dashboard. Includes the JS logic for exporting the enriched `.xlsx` file using `SheetJS`.
  - `archiving.html`: The drag-and-drop Archiving module. Handles the local file parsing and merging of API event data.
  - `invoices.html`: The Xero Sync dashboard.
  - `dedupe.html`: A placeholder module for future contact deduplication workflows.

---

## ⚙️ Setup & Installation

### Prerequisites
- Python 3.9+
- Your Glue Up API Credentials (`GLUE_UP_PUBLIC_KEY` and `GLUE_UP_PRIVATE_KEY`)
- Your Xero API Credentials (`XERO_CLIENT_ID` and `XERO_CLIENT_SECRET`)

### 1. Environment Variables
Create a `.env` file in the root directory:
```env
# Glue Up Auth
GLUE_UP_API_URL=https://api-services.glueup.com/v2
GLUE_UP_PUBLIC_KEY=your_public_key_here
GLUE_UP_PRIVATE_KEY=your_private_key_here

# Xero Auth
XERO_CLIENT_ID=your_xero_client_id
XERO_CLIENT_SECRET=your_xero_client_secret
XERO_REDIRECT_URI=http://localhost:5001/xero/callback

# App Settings
FLASK_SECRET_KEY=super-secret-key-for-sessions
SITE_PASSWORD=optional-password-to-lock-the-ui
```

### 2. Local Development
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```
The application will be available at `http://localhost:5001`.

### 3. Docker (Optional)
```bash
docker compose up --build
```

---

## ⚠️ Important API Limitations

The Glue Up v2 API is highly restrictive regarding CRM write operations:
- **No Contact Deletion:** Endpoints like `DELETE /user/contacts/{id}` will return `404` or `405`.
- **No Contact Modification:** Updating core identity fields (like emails) via the API is blocked.
- **No Custom Field Updates:** Programmatically changing fields like `is_dormant` via API is unsupported.

**The Workaround:** This is why the `CRM Cleanup` and `Archiving` modules focus on **exporting enriched `.xlsx` files**. You must use these custom tools to safely backup event histories, and then use the native Glue Up web dashboard to execute bulk deletions or bulk imports to modify the database.
