# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Function calling with Gemini — automatic and manual flows.

Two modes:
  AUTOMATIC: Pass plain Python callables in tools=[]. The SDK sends schemas,
  the model decides when to call them, the SDK executes the functions, and
  sends results back — the full loop happens inside one generate_content call.

  MANUAL (FunctionDeclaration): You declare the schema, control when the
  function is actually executed, and send the result back yourself. Use when
  execution must happen elsewhere (queue, human approval, external service).
"""

import os

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Tool implementations — stubbed; replace with real API calls in production.
# ---------------------------------------------------------------------------

def get_weather(city: str) -> dict:
    """Returns the current weather for a city."""
    # Docstring + type hints become the tool schema the model sees.
    fake = {"Pune": ("31C", "sunny"), "London": ("14C", "rainy")}
    temp, cond = fake.get(city, ("25C", "clear"))
    return {"city": city, "temperature": temp, "condition": cond}


def get_exchange_rate(from_currency: str, to_currency: str) -> dict:
    """Returns the exchange rate between two currency codes (e.g. USD, INR)."""
    fake_rates = {("USD", "INR"): 87.4, ("EUR", "INR"): 94.1}
    rate = fake_rates.get((from_currency.upper(), to_currency.upper()), 1.0)
    return {"from": from_currency, "to": to_currency, "rate": rate}


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def automatic_function_calling(client):
    """
    Automatic function calling: pass Python callables directly in tools=[].

    The SDK handles the full agentic loop:
      1. Extracts type hints + docstrings as schemas.
      2. Sends schemas to the model with the user prompt.
      3. Executes whichever functions the model requests.
      4. Returns results to the model for a final natural-language answer.

    One generate_content call — simplest path for synchronous, trusted tools.

    Returns:
        str: Final model response after tool execution.
    """
    response = client.models.generate_content(
        model=MODEL,
        contents="What's the weather in Pune, and how many INR is 1 USD?",
        config=types.GenerateContentConfig(
            tools=[get_weather, get_exchange_rate],
            max_output_tokens=512,
        ),
    )
    return response.text


def manual_function_calling(client):
    """
    Manual function calling via FunctionDeclaration: you execute, you return.

    Step A: Model returns a function_call part (not a text answer).
    Step B: You execute the function (or enqueue it, or ask a human).
    Step C: You send the result back as a function_response; model answers.

    Use this when:
    - Execution must happen outside the SDK (queue, approval workflow).
    - You need audit logs of which tool was called with what arguments.
    - The tool is asynchronous or lives in another service.

    Returns:
        dict with keys: fn_name, fn_args, fn_result, final_answer
    """
    weather_decl = types.FunctionDeclaration(
        name="get_weather",
        description="Returns the current weather for a city.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"city": types.Schema(type=types.Type.STRING)},
            required=["city"],
        ),
    )
    tool = types.Tool(function_declarations=[weather_decl])

    # Step A: model decides it needs the tool and returns a function_call part.
    contents = [types.Content(role="user", parts=[
        types.Part.from_text(text="What's the weather in London right now?")
    ])]
    resp1 = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(tools=[tool]),
    )
    fn_call = resp1.candidates[0].content.parts[0].function_call

    # Step B: we execute the function ourselves.
    result = get_weather(**fn_call.args)

    # Step C: send the result back as a function_response; model writes the answer.
    contents.append(resp1.candidates[0].content)  # keep model turn in history
    contents.append(types.Content(role="user", parts=[
        types.Part.from_function_response(name=fn_call.name, response=result)
    ]))
    resp2 = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(tools=[tool]),
    )

    return {
        "fn_name": fn_call.name,
        "fn_args": dict(fn_call.args),
        "fn_result": result,
        "final_answer": resp2.text,
    }


def run_function_calling():
    """Entry point for the function calling demo."""
    client = create_client()

    print("=== Automatic Function Calling ===")
    auto_result = automatic_function_calling(client)
    print(auto_result)

    print("\n=== Manual Function Calling Flow ===")
    manual = manual_function_calling(client)
    print(f"Model requested : {manual['fn_name']}({manual['fn_args']})")
    print(f"Tool returned   : {manual['fn_result']}")
    print(f"Final answer    : {manual['final_answer']}")
