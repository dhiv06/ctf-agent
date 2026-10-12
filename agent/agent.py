"""Autonomous CTF agent using Groq (OpenAI-compatible) chat completions with tool calling."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI

from tools import request_hint, run_command, send_to_server, submit_flag

HERE = Path(__file__).parent
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"

# Loads GROQ_API_KEY (and optional GROQ_MODEL) from a .env in the project root or cwd.
load_dotenv(HERE.parent / ".env")
load_dotenv()

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command inside the ctf-sandbox container (20s timeout). Returns stdout+stderr.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "Shell command to run."}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_server",
            "description": (
                "Connect to a TCP server (used for netcat-style challenges), read its initial output, "
                "send one line of input, and read the response. Use this instead of run_command for "
                "challenges that give a `nc host port` connection string."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Hostname or IP of the server."},
                    "port": {"type": "integer", "description": "TCP port."},
                    "message": {"type": "string", "description": "One line of input to send (a newline is appended). Must be a literal string, not an expression."},
                    "messages": {"type": "array", "items": {"type": "string"}, "description": "Optional. Several lines to send IN ORDER on ONE connection (replaces `message`). Every call opens a brand-new connection that starts from the beginning, so for multi-step servers replay your earlier correct answers first, then add the next one."},
                    "repeat": {"type": "integer", "description": "Optional. Send `message` repeated this many times back-to-back (e.g. message \"a\", repeat 500). Use this instead of writing very long strings."},
                },
                "required": ["host", "port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_flag",
            "description": "Submit a candidate flag, e.g. academy{...} or picoCTF{...}.",
            "parameters": {
                "type": "object",
                "properties": {"flag": {"type": "string", "description": "The candidate flag."}},
                "required": ["flag"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_hint",
            "description": (
                "Ask the human operator for a hint. Use only when genuinely stuck: after several clearly "
                "different approaches have failed. If no hint is ready, the run pauses until one is provided."
            ),
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "What you tried and where you are stuck."}},
                "required": [],
            },
        },
    },
]


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


def save_log(path: str, meta: dict, steps: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**meta, "steps": steps}, f, indent=2, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous CTF solving agent (Groq).")
    parser.add_argument("--description", required=True, help="Challenge description given to the agent.")
    parser.add_argument("--flag", default="",
                        help="Known correct flag used to validate submissions (optional; if omitted, any valid-format flag ends the run).")
    parser.add_argument("--max-steps", type=int, default=20, help="Maximum model turns (default 20).")
    parser.add_argument("--model", default=os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
                        help=f"Groq model ID (default {DEFAULT_MODEL}, or $GROQ_MODEL).")
    parser.add_argument("--log", default="run_log.json", help="Path of the JSON log file.")
    parser.add_argument("--hint-file", default="",
                        help="File the operator writes a hint into. The agent reads it only when it calls request_hint.")
    args = parser.parse_args()

    client = OpenAI(base_url=GROQ_BASE_URL, api_key=require_env("GROQ_API_KEY"))
    deployment = args.model

    system_prompt = (HERE / "system_prompt.txt").read_text(encoding="utf-8")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Challenge description:\n{args.description}"},
    ]

    meta = {
        "started": datetime.now(timezone.utc).isoformat(),
        "description": args.description,
        "model": deployment,
        "max_steps": args.max_steps,
        "solved": False,
    }
    steps: list = []

    for step in range(1, args.max_steps + 1):
        try:
            response = client.chat.completions.create(
                model=deployment,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
            )
        except BadRequestError as e:
            # The model emitted a malformed tool call (e.g. an expression instead of a JSON string).
            print(f"\n=== Step {step} ===\n[model error] {e}")
            steps.append({"step": step, "reasoning": "", "tool": None, "arguments": None, "result": f"[model error: {e}]"})
            messages.append({
                "role": "user",
                "content": "Your last tool call was rejected as invalid JSON. Tool arguments must be literal JSON values: "
                           "no expressions like \"a\"*500. For long repeated input use send_to_server's `repeat` field.",
            })
            save_log(args.log, meta, steps)
            continue
        msg = response.choices[0].message
        reasoning = msg.content or ""
        tool_calls = msg.tool_calls or []

        print(f"\n=== Step {step} ===")
        if reasoning:
            print(reasoning)

        # Append the assistant turn (must include tool_calls so results can be matched).
        assistant_msg = {"role": "assistant", "content": reasoning or None}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
        messages.append(assistant_msg)

        if not tool_calls:
            steps.append({"step": step, "reasoning": reasoning, "tool": None, "arguments": None, "result": None})
            messages.append({
                "role": "user",
                "content": "Continue by calling a tool (run_command or submit_flag). Explain your reasoning first.",
            })
            save_log(args.log, meta, steps)
            continue

        for tc in tool_calls:
            name = tc.function.name
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
                result = "[error: tool arguments were not valid JSON]"
            else:
                result = None

            if result is None:
                if name == "run_command":
                    command = arguments.get("command", "")
                    print(f"[run_command] {command}")
                    result = run_command(command)
                elif name == "send_to_server":
                    host = arguments.get("host", "")
                    port = arguments.get("port", 0)
                    message = arguments.get("message", "")
                    try:
                        repeat = int(arguments.get("repeat") or 1)
                    except (TypeError, ValueError):
                        repeat = 1
                    message = message * max(1, min(repeat, 100000))
                    lines = arguments.get("messages")
                    if not isinstance(lines, list):
                        lines = None
                    shown = message if len(message) <= 200 else f"{message[:60]}...[{len(message)} chars]"
                    print(f"[send_to_server] {host}:{port} <- {lines if lines else shown!r}")
                    result = send_to_server(host, port, message, messages=lines)
                elif name == "submit_flag":
                    print(f"[submit_flag] {arguments.get('flag', '')}")
                    outcome = submit_flag(arguments.get("flag", ""), args.flag)
                    result = json.dumps(outcome)
                    if outcome["correct"]:
                        meta["solved"] = True
                elif name == "request_hint":
                    print(f"[request_hint] {arguments.get('reason', '')}")
                    result = request_hint(args.hint_file)
                else:
                    result = f"[error: unknown tool {name}]"

            print(f"[result]\n{result}")
            steps.append({
                "step": step,
                "reasoning": reasoning,
                "tool": name,
                "arguments": arguments,
                "result": result,
            })
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        save_log(args.log, meta, steps)
        if meta["solved"]:
            break

    meta["finished"] = datetime.now(timezone.utc).isoformat()
    meta["steps_used"] = len({s["step"] for s in steps})
    save_log(args.log, meta, steps)

    print("\nSOLVED" if meta["solved"] else f"\nNot solved within {args.max_steps} steps.")
    print(f"Log written to {args.log}")
    return 0 if meta["solved"] else 1


if __name__ == "__main__":
    sys.exit(main())
