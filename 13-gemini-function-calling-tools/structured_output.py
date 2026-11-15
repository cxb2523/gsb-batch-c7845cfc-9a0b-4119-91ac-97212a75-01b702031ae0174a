# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Structured JSON output with Pydantic schema enforcement.

Forces Gemini to return JSON that validates against a Pydantic model via
response_schema. The SDK auto-converts the Pydantic model to an OpenAPI schema
and the model is CONSTRAINED to emit JSON that matches it.

Use this whenever the output feeds code, a database write, or an API call —
get a validated Python object directly via response.parsed, no json.loads needed.
"""

import os

from pydantic import BaseModel
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Schema definition — the contract between the prompt and the output.
# ---------------------------------------------------------------------------

class SupportTicket(BaseModel):
    customer_name: str
    product: str
    severity: str          # e.g. "low" | "medium" | "high" | "critical"
    issue_summary: str
    follow_up_needed: bool


# Deliberately messy, unstructured input — the realistic extraction scenario.
MESSY_EMAIL = """
fwd: hey team, got a call from Priya Sharma earlier, she's pretty upset --
their BigQuery scheduled queries have been failing since last night and
month-end reporting is blocked. sounds urgent tbh. she asked someone to
call her back tomorrow morning. thx -r
"""


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def extract_structured(client, text=MESSY_EMAIL, schema=SupportTicket):
    """
    Extracts structured fields from unstructured text using schema-constrained JSON.

    Two required config fields for schema enforcement:
      response_mime_type="application/json"  — forces JSON output mode
      response_schema=<PydanticModel>         — constrains to this exact shape

    Args:
        client: genai.Client instance.
        text: Raw unstructured text to extract from.
        schema: Pydantic BaseModel class defining the output contract.

    Returns:
        dict with keys: raw_json (str), parsed (Pydantic model instance)
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=f"Extract a support ticket from this email:\n{text}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=512,
        ),
    )
    return {
        "raw_json": response.text,
        "parsed": response.parsed,
    }


def run_structured_output():
    """Entry point for the structured output demo."""
    client = create_client()

    print("=== Input (messy email) ===")
    print(MESSY_EMAIL)

    result = extract_structured(client)

    print("=== Raw JSON (response.text) ===")
    print(result["raw_json"])

    ticket: SupportTicket = result["parsed"]
    print("\n=== Parsed Pydantic Object (response.parsed) ===")
    print(f"Customer        : {ticket.customer_name}")
    print(f"Product         : {ticket.product}")
    print(f"Severity        : {ticket.severity}")
    print(f"Summary         : {ticket.issue_summary}")
    print(f"Follow-up needed: {ticket.follow_up_needed}")
    print(
        "\nNote: response.parsed is a validated Pydantic instance — no json.loads, "
        "no manual validation. Safe to write directly to a database or API."
    )
