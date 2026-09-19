"""
=====================================================================
OPENSEARCH SEEDING SCRIPT (INDIA.GOV.IN MCP POC)
=====================================================================
WHAT: Dummy data (schemes, services, guidelines, error logs, applications) 
      ko OpenSearch indices mein populate karta hai.
WHY: Yeh script `docker compose up -d` ke baad aur MCP server start karne 
     se pehle ek hi baar run ki jaati hai.

NOTE: OpenSearch native security plugin ENABLED hai (DISABLE_SECURITY_PLUGIN=false).
Sirf EK admin credential use ho raha hai -- role-based access control (citizen vs
admin) OpenSearch level par nahi, balki official_groq_client.py ke application
gatekeeper mein hota hai.
"""
import os
import sys
from datetime import datetime, timedelta
from opensearchpy import OpenSearch, helpers
from dotenv import load_dotenv

# .env file se OpenSearch connection credentials aur host/port load karte hain
load_dotenv()

OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9201"))
OPENSEARCH_ADMIN_USER = os.getenv("OPENSEARCH_ADMIN_USER", "admin")
OPENSEARCH_ADMIN_PASS = os.getenv("OPENSEARCH_ADMIN_PASS")

# =====================================================================
# 🔐 LAYER 2 SECURITY: FAIL-FAST CREDENTIAL CHECK
# =====================================================================
# Agar .env mein admin password set hi nahi hai, toh yahin ruk jao -- 
# taaki koi silent/wrong-default password se connect na ho jaaye.
if not OPENSEARCH_ADMIN_PASS:
    sys.exit("ERROR: OPENSEARCH_ADMIN_PASS not set in .env file.")

# =====================================================================
# 🔐 LAYER 2 SECURITY: OPENSEARCH CLIENT INITIALIZATION
# =====================================================================
# Kyunki OpenSearch ab password-protected hai (native security enabled),
# hume connect karne ke liye Master Admin ke credentials pass karne honge.
client = OpenSearch(
    hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
    http_auth=(OPENSEARCH_ADMIN_USER, OPENSEARCH_ADMIN_PASS),
    use_ssl=os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true",
    verify_certs=False,
)

# --- FAIL-FAST VALIDATION ---
# Agar OpenSearch reachable nahi hai ya password galat hai, toh script 
# yahin ruk jayegi aur ek clear, actionable error message degi.
if not client.ping():
    sys.exit(
        f"ERROR: Cannot reach OpenSearch at {OPENSEARCH_HOST}:{OPENSEARCH_PORT} or Authentication Failed. "
        f"Is `docker compose up -d` running? Check OPENSEARCH_ADMIN_PASS in your .env file."
    )

today = datetime.now()

def days_ago(n):
    """Helper function jo current date se 'n' din pehle ki date format karke deti hai."""
    return (today - timedelta(days=n)).strftime("%Y-%m-%d")


# =====================================================================
# 📊 DUMMY DATA DEFINITION FOR E-GOVERNANCE PORTAL
# =====================================================================

schemes = [
    {"title": "PM-Kisan Samman Nidhi", "url_link": "https://pmkisan.gov.in", "description": "Income support scheme for farmer families providing Rs 6000 per year.", "eligibility": "Small and marginal farmer families", "release_date": days_ago(10)},
    {"title": "Ayushman Bharat - PMJAY", "url_link": "https://pmjay.gov.in", "description": "Health insurance scheme providing coverage up to Rs 5 lakh per family per year.", "eligibility": "Economically weaker section families", "release_date": days_ago(5)},
    {"title": "Pradhan Mantri Awas Yojana", "url_link": "https://pmaymis.gov.in", "description": "Housing scheme for urban and rural poor providing subsidy on home loans.", "eligibility": "Economically weaker and low income groups", "release_date": days_ago(45)},
    {"title": "Digital India Internship Scheme", "url_link": "https://digitalindia.gov.in", "description": "Internship program for students in emerging technologies.", "eligibility": "College students aged 18-25", "release_date": days_ago(3)},
    {"title": "Sukanya Samriddhi Yojana", "url_link": "https://nsiindia.gov.in", "description": "Small savings scheme for the girl child with high interest rate.", "eligibility": "Girl child below 10 years", "release_date": days_ago(60)},
]

services = [
    {"title": "Aadhaar Address Update", "url_link": "https://uidai.gov.in/update-address", "search_frequency": 9500},
    {"title": "PAN Card Application", "url_link": "https://incometax.gov.in/pan", "search_frequency": 8700},
    {"title": "Passport Renewal", "url_link": "https://passportindia.gov.in", "search_frequency": 6200},
    {"title": "Voter ID Registration", "url_link": "https://voters.eci.gov.in", "search_frequency": 5400},
    {"title": "Driving License Renewal", "url_link": "https://parivahan.gov.in", "search_frequency": 4100},
    {"title": "Income Certificate", "url_link": "https://edistrict.gov.in/income-certificate", "search_frequency": 3300},
]

public_guidelines = [
    {"title": "Aadhaar Update Guidelines", "url_link": "https://uidai.gov.in/guidelines", "description": "Documents required and process to update Aadhaar address, name, and mobile number."},
    {"title": "PAN-Aadhaar Linkage Circular", "url_link": "https://incometax.gov.in/pan-aadhaar-link", "description": "Official circular on mandatory linking of PAN with Aadhaar and penalty details."},
    {"title": "Income Certificate Requirements", "url_link": "https://edistrict.gov.in/income-certificate/docs", "description": "List of documents needed to apply for an income certificate: address proof, salary slip, ration card."},
]

error_logs = [
    {"timestamp": days_ago(0) + "T09:15:00", "service": "passport-fee-gateway", "error_type": "payment_failure", "status": "failed"},
    {"timestamp": days_ago(0) + "T10:42:00", "service": "income-cert-gateway", "error_type": "payment_failure", "status": "failed"},
    {"timestamp": days_ago(1) + "T14:05:00", "service": "pan-application", "error_type": "timeout", "status": "failed"},
    {"timestamp": days_ago(1) + "T16:30:00", "service": "passport-fee-gateway", "error_type": "payment_failure", "status": "failed"},
    {"timestamp": days_ago(2) + "T08:20:00", "service": "aadhaar-update", "error_type": "validation_error", "status": "failed"},
]

applications = [
    {"application_id": "APP-2026-001", "service": "Passport Renewal", "date": days_ago(0), "status": "under_review"},
    {"application_id": "APP-2026-002", "service": "Income Certificate", "date": days_ago(0), "status": "submitted"},
    {"application_id": "APP-2026-003", "service": "PAN Card", "date": days_ago(1), "status": "approved"},
    {"application_id": "APP-2026-004", "service": "Driving License", "date": days_ago(1), "status": "submitted"},
    {"application_id": "APP-2026-889", "service": "Aadhaar Update", "date": days_ago(2), "status": "approved"},
]

INDEX_DATA = {
    "schemes": schemes,
    "services": services,
    "public_guidelines": public_guidelines,
    "error_logs": error_logs,
    "applications": applications,
}

# =====================================================================
# 📐 EXPLICIT INDEX MAPPINGS (SCHEMA DEFINITIONS)
# =====================================================================
# Har index ke fields ke data types explicitly define kiye gaye hain 
# taaki OpenSearch unhe sahi tarike se search aur index kar sake.
INDEX_MAPPINGS = {
    "schemes": {
        "properties": {
            "title": {"type": "text"},
            "url_link": {"type": "keyword"},
            "description": {"type": "text"},
            "eligibility": {"type": "text"},
            "release_date": {"type": "date", "format": "yyyy-MM-dd"},
        }
    },
    "services": {
        "properties": {
            "title": {"type": "text"},
            "url_link": {"type": "keyword"},
            "search_frequency": {"type": "integer"},
        }
    },
    "public_guidelines": {
        "properties": {
            "title": {"type": "text"},
            "url_link": {"type": "keyword"},
            "description": {"type": "text"},
        }
    },
    "error_logs": {
        "properties": {
            "timestamp": {"type": "date", "format": "yyyy-MM-dd'T'HH:mm:ss"},
            "service": {"type": "keyword"},
            "error_type": {"type": "keyword"},
            "status": {"type": "keyword"},
        }
    },
    "applications": {
        "properties": {
            "application_id": {"type": "keyword"},
            "service": {"type": "keyword"},
            "date": {"type": "date", "format": "yyyy-MM-dd"},
            "status": {"type": "keyword"},
        }
    },
}


def seed():
    """Main function jo indices ko recreate karta hai aur data bulk mein ingest karta hai."""
    for index_name, docs in INDEX_DATA.items():
        # Agar index pehle se mojood hai, toh clean slate ke liye use delete kar dete hain
        if client.indices.exists(index=index_name):
            client.indices.delete(index=index_name)

        # Explicit mapping ke sath naya index create karna
        client.indices.create(
            index=index_name,
            body={"mappings": INDEX_MAPPINGS[index_name]},
        )

        # Bulk helpers ke liye actions list taiyar karna
        actions = [{"_index": index_name, "_source": doc} for doc in docs]

        # --- BULK ERROR HANDLING ---
        # helpers.bulk ka use karke fast ingestion karte hain bina crash hue errors capture karne ke liye
        success, errors = helpers.bulk(client, actions, raise_on_error=False)

        if errors:
            print(f"⚠️  '{index_name}': {success} succeeded, {len(errors)} FAILED")
            for e in errors[:3]:
                print(f"    {e}")
        else:
            print(f"✅ Seeded {success} docs into '{index_name}'")

    # Sabhi indices ko refresh karna taaki data turant search ke liye available ho jaye
    client.indices.refresh(index=",".join(INDEX_DATA.keys()))
    print("\nDone. Indices created:", list(INDEX_DATA.keys()))


if __name__ == "__main__":
    seed()
