# Glue Up API Connector

A robust Python integration for the **Glue Up Open API v2**. This connector handles the complex Digest Authentication (HMAC-SHA256) required by Glue Up, allowing you to seamlessly interact with User, Event, and Finance collections.

## Features

- **Custom Digest Authentication:** Automatically handles the `a` header generation (`v=1.0;k=...;ts=...;d=...`) using your API Public and Private keys.
- **Cross-Collection Timelines:** Fetch a comprehensive activity timeline for any specific user, combining:
  - CRM Profile Data (`/user/{id}`)
  - Event Registrations (`/event/registrations`)
  - Financial/Invoice History (`/finance/invoices`)
- **Docker Ready:** Includes a `Dockerfile` and `docker-compose.yml` for quick containerized deployment and local development.
- **Flask API Wrapper:** Serve your Glue Up data through a lightweight local Flask web server.

## Prerequisites

- Python 3.9+
- Docker & Docker Compose (Optional, for containerized running)
- Your Glue Up API Credentials (`GLUE_UP_PUBLIC_KEY` and `GLUE_UP_PRIVATE_KEY`)

## Setup & Installation

1. **Clone the repository and navigate to the project directory:**
   ```bash
   cd Glue-Up-Connector
   ```

2. **Set up your environment variables:**
   Create or edit the `.env` file in the root directory and add your credentials:
   ```env
   GLUE_UP_API_URL=https://api-services.glueup.com/v2  # Use your organization's specific live server URL
   GLUE_UP_PUBLIC_KEY=your_public_key_here
   GLUE_UP_PRIVATE_KEY=your_private_key_here
   ```
   *Note: Ensure your Private Key is the exact secret string provided by Glue Up.*

3. **Install Dependencies (Local Development):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

## Usage

### 1. Running the API Connector Script directly
You can query a specific user's timeline directly from the command line by passing their User ID:

```bash
python glue_up_api.py 12345
```
This will compile and print a summary of their CRM profile, event registrations, and financial activities.

### 2. Running the Flask Web Server (Docker)
To spin up the Flask application using Docker Compose:

```bash
docker compose up --build
```
The application will be available at `http://localhost:5000`. Hot-reloading is enabled, so changes to your python files will instantly reflect in the container.

### 3. Running the Flask Web Server (Locally)
```bash
python main.py
```

## Important API Notes

- **Authentication:** Glue Up Open API v2 uses a strict HMAC-SHA256 Digest signature rather than standard JWT or Bearer tokens. The `GlueUpAPI` class handles this under the hood.
- **Endpoints:** Collections use singular nouns (e.g., `/user`, `/event`, `/finance`). 
- **Bulk Exports:** The API does not allow a simple `GET /user` request to dump all contacts (this will return a `405 Method Not Allowed`). To fetch a list of all users, you must use Glue Up's specific search POST endpoints or the UI export tool.
