# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Gen AI Evaluation — Gemini-as-Judge Rubric Scoring.

Evaluates model responses on a small in-memory dataset using the
gemini-as-judge pattern: pointwise rubric scoring (groundedness, fluency)
plus a pairwise A/B comparison. Use to regression-test prompt or model
changes before shipping. Token usage is minimal.
"""

import json
import os

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
JUDGE_MODEL = "gemini-2.5-flash"

# Small in-memory eval dataset: prompt, context, and two candidate responses.
DATASET = [
    {
        "prompt": "What does Cloud Run scale to when idle?",
        "context": "Cloud Run scales container instances to zero when there is no traffic.",
        "response_a": "Cloud Run scales to zero instances when idle, so you pay nothing for compute.",
        "response_b": "Cloud Run keeps a minimum of three instances running at all times.",
    },
    {
        "prompt": "How is BigQuery on-demand pricing charged?",
        "context": "BigQuery on-demand pricing bills per TiB of data scanned by each query.",
        "response_a": "You are billed for the bytes each query scans, priced per TiB.",
        "response_b": "BigQuery bills per row returned by the query.",
    },
]

POINTWISE_RUBRIC = (
    "You are an evaluation judge. Score the RESPONSE against the CONTEXT.\n"
    "Metrics (1-5 integers):\n"
    "- groundedness: factually supported by the context, no invention\n"
    "- fluency: clear, well-formed language\n"
    'Return ONLY JSON: {"groundedness": n, "fluency": n, "reason": "<10 words"}'
)

PAIRWISE_RUBRIC = (
    "You are an evaluation judge. Given CONTEXT, decide which response is more\n"
    'grounded and helpful. Return ONLY JSON: {"winner": "A"|"B", "reason": "<10 words"}'
)


def judge(client, system, user_text):
    """Call Gemini as an evaluation judge and parse JSON response."""
    response = client.models.generate_content(
        model=JUDGE_MODEL,
        contents=user_text,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    return json.loads(response.text)


def run_pointwise_evaluation(client):
    """Run pointwise rubric scoring (groundedness + fluency) for all candidates."""
    print(f"{'#':<3} {'CAND':<5} {'GROUND':<7} {'FLUENCY':<8} REASON")
    print("=" * 78)

    totals = {"A": [0, 0], "B": [0, 0]}
    for i, row in enumerate(DATASET, start=1):
        for cand in ("A", "B"):
            resp = row[f"response_{cand.lower()}"]
            scores = judge(client, POINTWISE_RUBRIC, (
                f"CONTEXT: {row['context']}\nPROMPT: {row['prompt']}\nRESPONSE: {resp}"
            ))
            totals[cand][0] += scores["groundedness"]
            totals[cand][1] += scores["fluency"]
            print(f"{i:<3} {cand:<5} {scores['groundedness']:<7} "
                  f"{scores['fluency']:<8} {scores.get('reason', '')[:40]}")

    print("=" * 78)
    n = len(DATASET)
    for cand in ("A", "B"):
        print(f"Candidate {cand} averages -> groundedness: {totals[cand][0] / n:.1f}, "
              f"fluency: {totals[cand][1] / n:.1f}")

    return totals


def run_pairwise_evaluation(client):
    """Run pairwise comparison between response A and B on the first dataset row."""
    row = DATASET[0]
    verdict = judge(client, PAIRWISE_RUBRIC, (
        f"CONTEXT: {row['context']}\nPROMPT: {row['prompt']}\n"
        f"RESPONSE A: {row['response_a']}\nRESPONSE B: {row['response_b']}"
    ))
    print()
    print(f"Pairwise (row 1): winner = {verdict['winner']} — {verdict['reason']}")
    return verdict


def run_evaluation():
    """Full evaluation pipeline: pointwise scoring + pairwise comparison."""
    print(f"[evaluation] Project: {PROJECT_ID} | Location: {LOCATION} | Judge: {JUDGE_MODEL}")
    print("-" * 78)

    client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

    totals = run_pointwise_evaluation(client)
    verdict = run_pairwise_evaluation(client)

    print()
    print("NOTE: For larger suites, see the managed Vertex Gen AI Evaluation")
    print("service (EvalTask) — same idea, with prebuilt metrics and BQ export.")

    return {"pointwise_totals": totals, "pairwise_verdict": verdict}
