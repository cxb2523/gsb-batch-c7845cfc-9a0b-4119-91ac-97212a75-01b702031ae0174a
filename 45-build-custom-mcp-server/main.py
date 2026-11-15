# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Custom MCP Server — Entry Point.

Starts the FastMCP server exposing bq_query and gcs_list tools over
Streamable HTTP. This file is the container entrypoint for Cloud Run.

Run locally:
  python main.py

For Cloud Run deployment, see deploy.sh.
"""

from server import start_server

if __name__ == "__main__":
    start_server()
