"""Autonomous CTF agent using Gemini (OpenAI-compatible) chat completions with tool calling."""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import (APIConnectionError, APIStatusError, APITimeoutError, BadRequestError,
                    InternalServerError, OpenAI, RateLimitError)

from tools import copy_file, request_hint, run_command, send_to_server, submit_flag

HERE = Path(__file__).parent
# --- Groq (disabled) ---
# GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# DEFAULT_MODEL = "openai/gpt-oss-120b"
# --- Gemini (OpenAI-compatible endpoint) ---
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.8-flash"

# Loads GEMINI_API_KEY (and optional GEMINI_MODEL) from a .env in the project root or cwd.
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


PROMPTS = HERE / "prompts"
DEFAULT_SKILLS = "general,git"


def build_system_prompt(skills: str) -> str:
    """core.txt plus the requested skills/<name>.txt modules, in the order given."""
    parts = [(PROMPTS / "core.txt").read_text(encoding="utf-8").rstrip()]
    available = sorted(p.stem for p in (PROMPTS / "skills").glob("*.txt"))
    for name in (s.strip() for s in skills.split(",")):
        if not name:
            continue
        if name not in available:
            sys.exit(f"Unknown skill '{name}'. Available: {', '.join(available)}")
        parts.append((PROMPTS / "skills" / f"{name}.txt").read_text(encoding="utf-8").rstrip())
    return "\n\n".join(parts) + "\n"


KEEP_FULL = 2      # newest tool results kept whole in the history sent to the model
OLD_RESULT_CHARS = 300


def compact_history(messages: list, keep_full: int = KEEP_FULL, old_chars: int = OLD_RESULT_CHARS) -> list:
    """Copy of messages with all but the newest tool results shortened. Gemini's free tier caps tokens/minute
    and requests/day, so old bulky output should not be resent every turn."""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    shrink = set(tool_idx[:-keep_full] if keep_full else tool_idx)
    out = []
    for i, m in enumerate(messages):
        c = m.get("content")
        if i in shrink and isinstance(c, str) and len(c) > old_chars:
            m = {**m, "content": c[:old_chars] + f"\n[...{len(c) - old_chars} chars of older output dropped; re-run if needed]"}
        out.append(m)
    return out


def create_with_retry(client, retries: int = 6, **kwargs):
    """chat.completions.create that waits out 429 rate limits (Gemini free tier has per-minute and per-day quotas)."""
    messages = kwargs.pop("messages")
    keep, chars = KEEP_FULL, OLD_RESULT_CHARS
    for attempt in range(retries + 1):
        try:
            return client.chat.completions.create(messages=compact_history(messages, keep, chars), **kwargs)
        except (APITimeoutError, APIConnectionError, InternalServerError) as e:
            # 500/503 ("high demand"), timeouts and dropped connections are transient: back off and retry.
            if attempt == retries:
                raise
            wait = min(10 * (attempt + 1), 60)
            print(f"[{type(e).__name__}: {str(e)[:120]} - retrying in {wait}s]", flush=True)
            time.sleep(wait)
        except RateLimitError as e:  # must precede APIStatusError (its parent class)
            if attempt == retries:
                raise
            # Gemini 429s carry e.g. "retryDelay": "34s" (or "Please retry in 34.5s") and quotaIds like "...PerDay...".
            m = re.search(r'retryDelay"?\s*[:=]\s*"?([\d.]+)s', str(e)) or re.search(r"retry in ([\d.]+)s", str(e))
            secs = float(m.group(1)) if m else 0
            if secs > 120 or "PerDay" in str(e) or "per day" in str(e).lower():
                sys.exit("Gemini daily quota reached. Wait for the reset, set GEMINI_MODEL / --model to another "
                         "model, or enable billing, then re-run.")
            wait = min(secs + 1 if secs else 15 * (attempt + 1), 90)
            print(f"[rate limited, waiting {wait:.0f}s]", flush=True)
            time.sleep(wait)
        except APIStatusError as e:
            if e.status_code != 413 or (keep == 0 and chars <= 100):
                raise
            keep, chars = 0, max(100, chars // 3)  # request alone exceeds the cap: shrink the history further
            print(f"[request too large, shrinking history to {chars} chars per old result]", flush=True)


STUCK_WINDOW = 3     # consecutive empty/failed results (or repeats) before the agent is nudged
NUDGE_COOLDOWN = 3   # minimum steps between nudges


def stuck_nudge(steps: list, step: int, last_nudge: int) -> str:
    """Generic wander guard: a note appended to the latest tool result when the agent is making no progress
    (the last few results were empty/failed, or it re-ran a command it already ran). Challenge-agnostic."""
    if step - last_nudge < NUDGE_COOLDOWN:
        return ""
    acts = [s for s in steps if s.get("tool") in ("run_command", "send_to_server")]
    recent = acts[-STUCK_WINDOW:]
    if len(recent) < STUCK_WINDOW:
        return ""

    def bad(r) -> bool:
        r = (r or "").strip()
        return r.startswith(("[no output", "[timeout", "[error")) or "[exit code" in r[-40:]

    failing = all(bad(s.get("result")) for s in recent)
    seen = [json.dumps(s.get("arguments"), sort_keys=True) for s in acts[-6:]]
    repeating = len(seen) != len(set(seen))
    if not (failing or repeating):
        return ""
    why = "your last commands returned nothing useful" if failing else "you re-ran a command you already ran"
    return (f"[harness note: {why}. Stop and change approach: re-read the challenge description and any hint, "
            "work through your skill's checklist for the next technique you have not tried, and prefer a helper "
            "command (triage, diskscan, ...) over more manual exploration. Never repeat a command. "
            "If you are still stuck, call request_hint.]")


def save_log(path: str, meta: dict, steps: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**meta, "steps": steps}, f, indent=2, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous CTF solving agent (Gemini).")
    parser.add_argument("--description", required=True, help="Challenge description given to the agent.")
    parser.add_argument("--flag", default="",
                        help="Known correct flag used to validate submissions (optional; if omitted, any valid-format flag ends the run).")
    parser.add_argument("--max-steps", type=int, default=20, help="Maximum model turns (default 20).")
    # parser.add_argument("--model", default=os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
    #                     help=f"Groq model ID (default {DEFAULT_MODEL}, or $GROQ_MODEL).")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
                        help=f"Gemini model ID (default {DEFAULT_MODEL}, or $GEMINI_MODEL).")
    parser.add_argument("--log", default="run_log.json", help="Path of the JSON log file.")
    parser.add_argument("--hint-file", default="",
                        help="File the operator writes a hint into. The agent reads it only when it calls request_hint.")
    parser.add_argument("--file", action="append", default=[], metavar="PATH",
                        help="Host file to copy into the sandbox's /workspace before the run (repeatable).")
    parser.add_argument("--skills", default=DEFAULT_SKILLS,
                        help=f"Comma-separated prompt skills from agent/prompts/skills (default {DEFAULT_SKILLS}).")
    args = parser.parse_args()

    # client = OpenAI(base_url=GROQ_BASE_URL, api_key=require_env("GROQ_API_KEY"))
    client = OpenAI(base_url=GEMINI_BASE_URL, api_key=require_env("GEMINI_API_KEY"),
                    timeout=90, max_retries=0)  # we do our own retries in create_with_retry
    deployment = args.model

    system_prompt = build_system_prompt(args.skills)
    user_text = f"Challenge description:\n{args.description}"
    if args.file:
        try:
            placed = [copy_file(p) for p in args.file]
        except RuntimeError as e:
            sys.exit(f"--file failed: {e}")
        user_text += "\n\nChallenge file(s) available in the sandbox: " + ", ".join(placed)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    meta = {
        "started": datetime.now(timezone.utc).isoformat(),
        "description": args.description,
        "model": deployment,
        "max_steps": args.max_steps,
        "solved": False,
    }
    steps: list = []
    last_nudge = -NUDGE_COOLDOWN

    for step in range(1, args.max_steps + 1):
        try:
            response = create_with_retry(
                client,
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
        choice = response.choices[0] if response.choices else None
        msg = choice.message if choice else None
        if msg is None:
            # Gemini sometimes returns an empty/blocked/malformed response with no message at all.
            reason = getattr(choice, "finish_reason", None) or "no choices"
            print(f"\n=== Step {step} ===\n[empty model response: finish_reason={reason}; asking it to continue]")
            steps.append({"step": step, "reasoning": "", "tool": None, "arguments": None,
                          "result": f"[empty model response: {reason}]"})
            messages.append({"role": "user", "content": "Your last response was empty. Continue by calling a tool "
                                                         "(run_command or submit_flag) with valid JSON arguments."})
            save_log(args.log, meta, steps)
            continue
        reasoning = msg.content or ""
        tool_calls = msg.tool_calls or []

        print(f"\n=== Step {step} ===")
        if reasoning:
            print(reasoning)

        # Append the assistant turn (must include tool_calls so results can be matched).
        assistant_msg = {"role": "assistant", "content": reasoning or None}
        if tool_calls:
            assistant_msg["tool_calls"] = []
            for tc in tool_calls:
                call = {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                # Gemini 3 returns a thought_signature in extra_content and rejects the next request (400)
                # unless it is echoed back unchanged on the same tool call.
                extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
                if extra:
                    call["extra_content"] = extra
                assistant_msg["tool_calls"].append(call)
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

        nudge = stuck_nudge(steps, step, last_nudge)
        if nudge and messages[-1].get("role") == "tool":
            messages[-1]["content"] += "\n\n" + nudge
            last_nudge = step
            print(f"\n{nudge}")

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
