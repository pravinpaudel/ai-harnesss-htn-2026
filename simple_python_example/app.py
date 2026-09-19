import asyncio
from contextlib import AsyncExitStack
import json
import os
import sys
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openai import OpenAI
from pydantic import BaseModel
from rich.console import Console
import uvicorn

# System setup
app = FastAPI(title="MCP Web Chat")
openai_client = OpenAI()
console = Console()

# Default configuration
DEFAULT_MCP_URL = "http://3.143.20.160/mcp"


# Models for Request Payload
class ChatRequest(BaseModel):
  user_query: str
  mcp_url: Optional[str] = DEFAULT_MCP_URL
  history: Optional[List[Dict[str, str]]] = []


def mcp_tools_to_openai(mcp_tools):
  """Converts MCP tool declarations into OpenAI function tool call parameters."""
  openai_tools = []
  for tool in mcp_tools:
    openai_tools.append({
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": (
                tool.inputSchema
                if hasattr(tool, "inputSchema")
                else tool.input_schema
            ),
        },
    })
  return openai_tools


async def execute_mcp_query(
    user_query: str, history: List[Dict[str, str]], mcp_url: str
):
  """Connects to MCP session, runs tool-use loop, and yields formatted events."""
  async with AsyncExitStack() as stack:
    try:
      # Establish transport stream
      read_stream, write_stream = await stack.enter_async_context(
          streamable_http_client(mcp_url)
      )
      session = await stack.enter_async_context(
          ClientSession(read_stream, write_stream)
      )
      await session.initialize()

      # Load MCP tools
      tools_response = await session.list_tools()
      available_tools = tools_response.tools
      mcp_openai_tools = mcp_tools_to_openai(available_tools)

    except Exception as e:
      yield (
          f"data:"
          f" {json.dumps({'error': f'Failed to connect to MCP server at {mcp_url}: {str(e)}'})}\n\n"
      )
      return

    # Prepare chat messages combining past history and new user query
    messages = [{
        "role": "system",
        "content": (
            "You are a helpful assistant with access to MCP tools. Call tools"
            " when needed to fulfill requests."
        ),
    }]

    for msg in history:
      messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_query})

    while True:
      with console.status(
          "[bold cyan]Querying OpenAI...[/bold cyan]", spinner="dots"
      ):
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=mcp_openai_tools if mcp_openai_tools else None,
            tool_choice="auto" if mcp_openai_tools else None,
        )

      response_message = response.choices[0].message

      # Format message for OpenAI context payload
      msg_dict = {
          "role": "assistant",
          "content": response_message.content or "",
      }
      if response_message.tool_calls:
        msg_dict["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in response_message.tool_calls
        ]
      messages.append(msg_dict)

      # Check if model generated final message
      if not response_message.tool_calls:
        final_text = response_message.content or ""
        yield (
            f"data:"
            f" {json.dumps({'content': final_text, 'status': 'done'})}\n\n"
        )
        break

      # Process function calls
      for tool_call in response_message.tool_calls:
        tool_name = tool_call.function.name
        tool_args = json.loads(tool_call.function.arguments)

        yield (
            f"data:"
            f" {json.dumps({'status': 'executing_tool', 'tool': tool_name})}\n\n"
        )

        with console.status(
            f"[bold yellow]Executing {tool_name}...[/bold yellow]",
            spinner="dots",
        ):
          try:
            result = await session.call_tool(tool_name, arguments=tool_args)
            content_text = ""
            for content_block in result.content:
              if hasattr(content_block, "text"):
                content_text += content_block.text
              else:
                content_text += str(content_block)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": content_text,
            })

          except Exception as e:
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": f"Error executing tool: {e}",
            })


@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
  return StreamingResponse(
      execute_mcp_query(payload.user_query, payload.history, payload.mcp_url),
      media_type="text/event-stream",
  )


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
  try:
    with open("index.html", "r", encoding="utf-8") as f:
      return HTMLResponse(content=f.read())
  except FileNotFoundError:
    raise HTTPException(status_code=404, detail="index.html not found.")


if __name__ == "__main__":
  print("\n\033[1;32mStarting Web App Server\033[0m\n")
  uvicorn.run(app, host="0.0.0.0", port=8000)
