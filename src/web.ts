#!/usr/bin/env node
import dotenv from "dotenv";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { webTools, WebSearchSchema, WebFetchSchema, webSearch, webFetch, WebBudgetError, budgetSettlement } from "./tools/web.js";
import type { WebBudget } from "./tools/web.js";
import { version } from "../package.json";

process.umask(0o077);

// This entrypoint only loads the dedicated web credential file, never media or infrastructure clients.
dotenv.config({ path: process.env.HOMELAB_WEB_ENV_FILE ?? "/etc/homelab-mcp/web.env" });
const server = new Server({ name: "homelab-web-mcp", version }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: webTools }));

/** The tool payloads embed their settlement; mirror it for clients that read structured results. */
function embeddedBudget(result: string): WebBudget | undefined {
  try {
    const budget = (JSON.parse(result) as { budget?: WebBudget }).budget;
    return budget && typeof budget === "object" ? budget : undefined;
  } catch {
    return undefined;
  }
}

server.setRequestHandler(CallToolRequestSchema, async request => {
  try {
    const args = request.params.arguments ?? {};
    let result: string;
    switch (request.params.name) {
      case "web_search": result = await webSearch(WebSearchSchema.parse(args)); break;
      case "web_fetch": result = await webFetch(WebFetchSchema.parse(args)); break;
      default: throw new Error("Unknown public-web tool.");
    }
    const budget = embeddedBudget(result);
    return { content: [{ type: "text", text: result }], ...(budget ? { structuredContent: { budget } } : {}) };
  } catch (error) {
    const key = process.env.DEEPSEEK_API_KEY;
    const redact = (value: string) => (key ? value.split(key).join("[redacted]") : value);
    // Every tool-level failure carries its own budget settlement; a schema rejection
    // is decided before the tool runs and therefore never touches the budget. The
    // settlement is appended after truncating the detail so it is never cut off.
    if (error instanceof WebBudgetError) {
      const text = `${redact(error.detail).slice(0, 800)} ${budgetSettlement(error.budget)}`.slice(0, 1000);
      return { content: [{ type: "text", text }], structuredContent: { budget: error.budget }, isError: true };
    }
    const message = error instanceof z.ZodError
      ? "Invalid public-web arguments. This request does not count toward the shared daily public-web budget."
      : error instanceof Error ? error.message : "Public-web request failed.";
    return { content: [{ type: "text", text: redact(message).slice(0, 1000) }], isError: true };
  }
});
server.connect(new StdioServerTransport()).catch(() => {
  process.stderr.write("Public-web MCP transport failed.\n");
  process.exit(1);
});
