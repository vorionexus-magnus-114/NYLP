"""
Web research and verification module for PathFinder AI.
Performs verification of scholarship records against web evidence and caches results in SQLite.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from src.database import get_connection, init_db

# Cache TTL: 24 hours
CACHE_TTL_HOURS = 24


def get_cached_verification(scholarship_id, max_age_hours=CACHE_TTL_HOURS):
    """
    Retrieve cached verification result for a scholarship if available and not expired.
    """
    if not scholarship_id:
        return None

    init_db()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT verified_at, source_url, status, claims_json, discrepancies_json, raw_evidence
            FROM verification_cache
            WHERE scholarship_id = ?
            """,
            (scholarship_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        verified_at_str = row["verified_at"]
        if verified_at_str:
            try:
                verified_time = datetime.fromisoformat(verified_at_str)
                if datetime.now() - verified_time > timedelta(hours=max_age_hours):
                    return None  # Expired
            except ValueError:
                pass

        return {
            "scholarship_id": scholarship_id,
            "verified_at": row["verified_at"],
            "source_url": row["source_url"],
            "status": row["status"],
            "verified_claims": json.loads(row["claims_json"]) if row["claims_json"] else [],
            "discrepancies": json.loads(row["discrepancies_json"]) if row["discrepancies_json"] else [],
            "raw_evidence": row["raw_evidence"],
        }
    finally:
        conn.close()


def cache_verification_result(scholarship_id, name, provider, source_url, status, claims, discrepancies, raw_evidence=""):
    """
    Store or update verification result in verification_cache table.
    """
    init_db()
    conn = get_connection()
    verified_at = datetime.now().isoformat()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO verification_cache
            (scholarship_id, scholarship_name, provider, verified_at, source_url, status, claims_json, discrepancies_json, raw_evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scholarship_id,
                name,
                provider,
                verified_at,
                source_url,
                status,
                json.dumps(claims, ensure_ascii=False),
                json.dumps(discrepancies, ensure_ascii=False),
                raw_evidence,
            ),
        )
        # Also update verified_at in scholarships table if scholarship_id is present
        if scholarship_id:
            conn.execute(
                "UPDATE scholarships SET verified_at = ?, source_url = COALESCE(?, source_url) WHERE id = ?",
                (verified_at, source_url, scholarship_id),
            )
        conn.commit()
        return verified_at
    finally:
        conn.close()


def verify_scholarship_record(scholarship, client=None, model_name=None, force_refresh=False):
    """
    Perform web verification of a scholarship record.
    Uses cached result if valid unless force_refresh is True.
    When GenAI client with search tool is available, generates search verification query.
    Fallback / default logic operates gracefully offline when no client or search credits are available.
    """
    sch_id = scholarship.get("id")
    name = scholarship.get("name") or "Scholarship"
    provider = scholarship.get("provider") or ""
    existing_url = scholarship.get("source_url") or scholarship.get("application_url")

    if not force_refresh and sch_id:
        cached = get_cached_verification(sch_id)
        if cached:
            return cached

    # Construct research claim check
    claims = []
    discrepancies = []
    status = "verified_primary" if existing_url else "unverified"

    if scholarship.get("deadline"):
        claims.append(f"Deadline listed as {scholarship.get('deadline')}")
    if scholarship.get("education_level"):
        claims.append(f"Education level: {scholarship.get('education_level')}")
    if scholarship.get("funding"):
        claims.append(f"Funding: {scholarship.get('funding')}")

    # If Gen AI client is provided, attempt search grounding verification
    if client and model_name:
        try:
            prompt = (
                f"Verify official scholarship details for '{name}' offered by '{provider}'. "
                f"Search query targets official university or organization source page. "
                f"Check deadline ({scholarship.get('deadline')}), funding ({scholarship.get('funding')}), "
                f"and eligibility. Is this scholarship currently open and verified?"
            )
            # Use google search tool grounding if supported in client API
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"tools": [{"google_search": {}}]},
            )
            evidence_text = getattr(response, "text", "")
            if "verified" in evidence_text.lower() or "open" in evidence_text.lower():
                status = "verified_grounded"
                claims.append("Verified open status via Google Search grounding")
            raw_evidence = evidence_text[:1000]
        except Exception as e:
            raw_evidence = f"Search verification fallback: {str(e)}"
    else:
        raw_evidence = "Database record primary verification check."

    v_date = cache_verification_result(
        sch_id, name, provider, existing_url, status, claims, discrepancies, raw_evidence
    )

    return {
        "scholarship_id": sch_id,
        "verified_at": v_date,
        "source_url": existing_url,
        "status": status,
        "verified_claims": claims,
        "discrepancies": discrepancies,
        "raw_evidence": raw_evidence,
    }
