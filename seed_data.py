"""
Seeds OpenSearch with dummy data for the india.gov.in MCP POC.
Run this once after `docker compose up -d` and before starting the MCP server.
"""
import os
import sys
from datetime import datetime, timedelta
from opensearchpy import OpenSearch, helpers
from dotenv import load_dotenv

load_dotenv()

OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9201"))

client = OpenSearch(
    hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
    use_ssl=os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true",
    verify_certs=False,
)

# --- FIX #3: FAIL-FAST VALIDATION ---
# Agar OpenSearch reachable hi nahi hai, turant clear error do, silently
# aage badh ke confusing error later mat do.
if not client.ping():
    sys.exit(
        f"ERROR: Cannot reach OpenSearch at {OPENSEARCH_HOST}:{OPENSEARCH_PORT}. "
        f"Is `docker compose up -d` running? Check your .env file."
    )

today = datetime.now()


def days_ago(n):
    return (today - timedelta(days=n)).strftime("%Y-%m-%d")


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

# --- FIX #1: EXPLICIT MAPPINGS ---
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
    for index_name, docs in INDEX_DATA.items():
        if client.indices.exists(index=index_name):
            client.indices.delete(index=index_name)

        client.indices.create(
            index=index_name,
            body={"mappings": INDEX_MAPPINGS[index_name]},
        )

        actions = [{"_index": index_name, "_source": doc} for doc in docs]

        # --- FIX #4: BULK ERROR HANDLING ---
        success, errors = helpers.bulk(client, actions, raise_on_error=False)

        if errors:
            print(f"⚠️  '{index_name}': {success} succeeded, {len(errors)} FAILED")
            for e in errors[:3]:
                print(f"    {e}")
        else:
            print(f"✅ Seeded {success} docs into '{index_name}'")

    client.indices.refresh(index=",".join(INDEX_DATA.keys()))
    print("\nDone. Indices created:", list(INDEX_DATA.keys()))


if __name__ == "__main__":
    seed()
