"""
=====================================================================
TOURIST PLACES OPENSEARCH SEEDING SCRIPT (INDIA.GOV.IN MCP POC)
=====================================================================
WHAT: tourist.json file se real tourist places ka data read karta hai, 
      HTML tags clean karta hai, aur OpenSearch ke 'touristplace' index mein ingest karta hai.
WHY: Yeh script ensure karti hai ki chatbot ke paas tourist spots se judi 
     sahi aur clean information available ho.

NOTE: OpenSearch native security plugin ENABLED hai (DISABLE_SECURITY_PLUGIN=false).
Sirf EK admin credential use ho raha hai -- role-based access control (citizen vs
admin) OpenSearch level par nahi, balki official_groq_client.py ke application
gatekeeper mein hota hai.
"""
import os
import re
import sys
import json
from opensearchpy import OpenSearch, helpers
from dotenv import load_dotenv

# .env file se configurations load karte hain
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
# Kyunki OpenSearch password-protected hai, naya index create karne aur 
# data seed karne ke liye Master Admin ke credentials use kiye jate hain.
client = OpenSearch(
    hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
    http_auth=(OPENSEARCH_ADMIN_USER, OPENSEARCH_ADMIN_PASS),
    use_ssl=os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true",
    verify_certs=False,
)

# Fail-fast check: Agar OpenSearch down hai ya connection fail hota hai toh script ruk jayegi
if not client.ping():
    sys.exit(
        f"ERROR: Cannot reach OpenSearch at {OPENSEARCH_HOST}:{OPENSEARCH_PORT} or Authentication Failed. "
        f"Is `docker compose up -d` running? Check OPENSEARCH_ADMIN_PASS in your .env file."
    )

INDEX_NAME = "touristplace"

# =====================================================================
# 📐 EXPLICIT INDEX MAPPING (SCHEMA DEFINITION)
# =====================================================================
# Mapping define karti hai ki kaunsa field full-text search ke liye hai (text) 
# aur kaunsa field exact filtering/aggregation ke liye hai (keyword).
MAPPING = {
    "properties": {
        "title": {"type": "text"},
        "description": {"type": "text"},
        "url": {"type": "keyword"},
        "cityName": {"type": "keyword"},
        "districtName": {"type": "keyword"},
        "stateName": {"type": "keyword"},
        "statecode": {"type": "keyword"},
        "placeCategoryName": {"type": "keyword"},
        "how_to_reach": {
            "properties": {
                "by_air": {"type": "text"},
                "by_rail": {"type": "text"},
                "by_road": {"type": "text"},
            }
        },
        "location": {"type": "geo_point"},
        "totalViews": {"type": "integer"},
        "totalLikes": {"type": "integer"},
    }
}


def clean_html(raw_html: str, max_len: int = 600) -> str:
    """
    HTML tags aur unwanted entities ko description se remove karta hai,
    aur text ko concise limit (max_len) tak truncate karta hai taaki LLM context window optimize rahe.
    """
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)     # HTML tags remove karna
    text = re.sub(r"&nbsp;|&#\d+;", " ", text)    # HTML special entities remove karna
    text = re.sub(r"\s+", " ", text).strip()      # Extra spaces clean karna
    return text[:max_len]


def seed():
    """Main function jo JSON file read karke tourist data ko OpenSearch mein bulk ingest karta hai."""
    # tourist.json file ko read-mode mein open karna
    with open("tourist.json", "r", encoding="utf-8") as f:
        docs = json.load(f)

    # Agar purana index mojood hai toh use delete karke fresh index banana
    if client.indices.exists(index=INDEX_NAME):
        client.indices.delete(index=INDEX_NAME)
    client.indices.create(index=INDEX_NAME, body={"mappings": MAPPING})

    actions = []
    for d in docs:
        lat = d.get("latitude", 0) or 0
        lon = d.get("longitude", 0) or 0
        
        # Document source structure prepare karna
        src = {
            "title": d.get("title", ""),
            "description": clean_html(d.get("description", "")),
            "url": d.get("url", ""),
            "cityName": d.get("cityName", ""),
            "districtName": d.get("districtName", ""),
            "stateName": d.get("stateName", ""),
            "statecode": d.get("statecode", ""),
            "placeCategoryName": d.get("placeCategoryName", []),
            "how_to_reach": d.get("how_to_reach", {}),
            "location": {"lat": lat, "lon": lon},
            "totalViews": d.get("totalViews", 0),
            "totalLikes": d.get("totalLikes", 0),
        }
        actions.append({
            "_index": INDEX_NAME,
            "_id": d.get("_id") or d.get("id"),
            "_source": src,
        })

    # Bulk API ka use karke fast ingestion perform karna
    success, errors = helpers.bulk(client, actions, raise_on_error=False)
    if errors:
        print(f"⚠️  {success} succeeded, {len(errors)} FAILED")
        for e in errors[:3]:
            print(f"    {e}")
    else:
        print(f"✅ Seeded {success} docs into '{INDEX_NAME}'")

    # Index refresh karna taaki naya data turant search query ke liye available ho jaye
    client.indices.refresh(index=INDEX_NAME)


if __name__ == "__main__":
    seed()
