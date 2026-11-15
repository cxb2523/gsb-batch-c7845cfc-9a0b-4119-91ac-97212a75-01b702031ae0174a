# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
PDF document understanding with Gemini.

No OCR pipeline, no parsing libraries — Gemini reads PDF text, tables, and
layout natively. Send the file as a Part.from_uri and ask any question.

Cost note: each PDF page costs a fixed number of input tokens. Page count
is the main cost driver for document workloads.

Use cases: contract review, report summarization, targeted field extraction.
"""

import os

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"

# Public sample PDF: Gemini 1.5 technical report (arXiv 2403.05530).
PDF_URI = "gs://cloud-samples-data/generative-ai/pdf/2403.05530.pdf"


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def build_pdf_part(pdf_uri=PDF_URI):
    """
    Constructs the PDF Part for inclusion in a multimodal content list.

    Returns a types.Part configured for application/pdf MIME type.
    The same Part object can be reused across multiple questions to avoid
    constructing it on every call (though the PDF tokens are re-sent each time
    unless you use context caching — see project 14).
    """
    return types.Part.from_uri(file_uri=pdf_uri, mime_type="application/pdf")


def summarize_document(client, pdf_part, max_output_tokens=512):
    """
    Asks Gemini to summarize the attached PDF in three bullet points.

    Args:
        client: genai.Client instance.
        pdf_part: types.Part for the PDF (from build_pdf_part).
        max_output_tokens: Cap on response length.

    Returns:
        str: Bullet-point summary.
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            pdf_part,
            "Summarize this document in 3 bullet points.",
        ],
        config=types.GenerateContentConfig(max_output_tokens=max_output_tokens),
    )
    return response.text


def extract_targeted_fields(client, pdf_part, max_output_tokens=512):
    """
    Extracts specific facts from the PDF without any pre-processing.

    Gemini can pull named fields, table values, and metadata directly —
    no regex, no parser, no intermediate representation needed.

    Args:
        client: genai.Client instance.
        pdf_part: types.Part for the PDF.
        max_output_tokens: Cap on response length.

    Returns:
        dict with keys: text, prompt_token_count
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            pdf_part,
            "What is the title of this paper and who are the first three "
            "listed contributors or authors? Answer in 2 lines.",
        ],
        config=types.GenerateContentConfig(max_output_tokens=max_output_tokens),
    )
    return {
        "text": response.text,
        "prompt_token_count": response.usage_metadata.prompt_token_count,
    }


def run_document_analysis():
    """Entry point for the PDF document analysis demo."""
    client = create_client()
    pdf_part = build_pdf_part()

    print("=== PDF Summarization ===")
    summary = summarize_document(client, pdf_part)
    print(summary)

    print("\n=== Targeted Extraction ===")
    extraction = extract_targeted_fields(client, pdf_part)
    print(extraction["text"])
    print(f"\nInput tokens for PDF + prompt: {extraction['prompt_token_count']}")
    print(
        "\nNote: page count drives input token cost for PDFs. "
        "Use context caching (project 14) when querying the same PDF repeatedly."
    )
