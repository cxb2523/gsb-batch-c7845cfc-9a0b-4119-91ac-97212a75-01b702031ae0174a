# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Entry point: generates Cloud Run deployment artifacts for a FastMCP server,
demonstrates the local MCP client, and shows MCP Toolbox BigQuery setup.

Run steps:
  1. python main.py              → generates ./mcp_server_deploy/ + prints deploy commands
  2. Start local MCP server in another terminal, then:
     python mcp_client.py        → connects and calls tools
  3. python toolbox_client.py    → generates tools.yaml + shows Toolbox usage
"""

import asyncio
import os

from cloud_run_deployer import generate_deployment_artifacts
from toolbox_client import run_toolbox_demo


def main():
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
    region = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    print("=" * 70)
    print("MCP Server → Cloud Run  |  MCP Client  |  MCP Toolbox BigQuery")
    print("=" * 70)
    print(f"  Project : {project_id}")
    print(f"  Region  : {region}")

    # Step 1: generate Cloud Run deployment artifacts
    print("\n" + "=" * 70)
    print("STEP 1: Generate Cloud Run deployment artifacts")
    print("=" * 70)
    generate_deployment_artifacts(project_id=project_id, region=region)

    # Step 2: instructions for running the MCP client
    print("\n" + "=" * 70)
    print("STEP 2: Run the MCP client against a local server")
    print("=" * 70)
    print("In a separate terminal, start the local MCP server first:")
    print("  cd mcp_server_deploy && python server.py")
    print("\nThen in this terminal:")
    print("  python mcp_client.py")
    print("\nOr to run the client inline (requires local server at localhost:8080):")
    print("  MCP_URL=http://localhost:8080/mcp python mcp_client.py")

    # Step 3: MCP Toolbox BigQuery demo (generates tools.yaml, skips live call if not running)
    print("\n" + "=" * 70)
    print("STEP 3: MCP Toolbox BigQuery — tools.yaml + client demo")
    print("=" * 70)
    asyncio.run(run_toolbox_demo(project_id=project_id))

    print("\n" + "=" * 70)
    print("All steps complete.")
    print("  Deploy artifacts  : ./mcp_server_deploy/")
    print("  Toolbox config    : ./tools.yaml")
    print("  Deploy script     : ./deploy.sh")
    print("=" * 70)


if __name__ == "__main__":
    main()
