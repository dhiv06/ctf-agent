"""Tools exposed to the CTF agent."""
import hmac
import json
import re
import subprocess
import time
from pathlib import Path

CONTAINER = "ctf-sandbox"
TIMEOUT_SECONDS = 20
MAX_OUTPUT_CHARS = 8000

FLAG_RE = re.compile(r"(academy|picoCTF)\{[^}]*\}")


def run_command(command: str) -> str:
    """Run a shell command inside the sandbox container; return stdout+stderr."""
    try:
        proc = subprocess.run(
            ["docker", "exec", CONTAINER, "bash", "-c", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        partial = (e.stdout or b"")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return f"[timeout after {TIMEOUT_SECONDS}s]\n{partial}"
    except FileNotFoundError:
        return "[error: docker executable not found on host]"

    output = (proc.stdout or "") + (proc.stderr or "")
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f"\n[output truncated at {MAX_OUTPUT_CHARS} chars]"
    if not output.strip():
        output = f"[no output, exit code {proc.returncode}]"
    elif proc.returncode != 0:
        output += f"\n[exit code {proc.returncode}]"
    return output


# Runs inside the container. argv: host port messages_json timeout. Reads until the server
# goes quiet (or closes), so prompts that never send a newline still come back. Sends each
# message on the SAME connection, reading the reply after each one.
_TCP_SCRIPT = r"""
import socket, sys, time, json
h, p, ms, t = sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3]), float(sys.argv[4])
def rd(s):
    d = b""
    e = time.time() + t
    while time.time() < e:
        s.settimeout(min(2.0, max(0.1, e - time.time())))
        try:
            c = s.recv(4096)
        except socket.timeout:
            if d:
                break
            continue
        if not c:
            break
        d += c
    return d.decode("utf-8", "replace")
s = socket.create_connection((h, p), timeout=t)
o = ["--- Initial response ---\n" + rd(s)]
for i, m in enumerate(ms):
    try:
        s.sendall(m.encode() + b"\n")
    except OSError as x:
        o.append("[connection closed before message %d: %s]" % (i + 1, x))
        break
    lab = "After sending input" if len(ms) == 1 else "After message %d (%s)" % (i + 1, repr(m[:40]))
    o.append("--- " + lab + " ---\n" + rd(s))
print("\n".join(o))
"""


def send_to_server(host: str, port: int, message: str = "", timeout: int = 10, messages=None) -> str:
    """Open ONE connection to host:port from inside the sandbox, read the banner, send each line in
    `messages` (or the single `message`) in order, and return the output after each."""
    try:
        port = int(port)
        timeout = max(1, int(timeout))
    except (TypeError, ValueError):
        return "[error: port and timeout must be integers]"
    lines = [str(m) for m in messages] if messages else [str(message)]
    try:
        proc = subprocess.run(
            ["docker", "exec", CONTAINER, "python3", "-c", _TCP_SCRIPT,
             str(host), str(port), json.dumps(lines), str(timeout)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout * (len(lines) + 1) + 10,
        )
    except subprocess.TimeoutExpired as e:
        partial = e.stdout or b""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return f"[timeout after {timeout}s]\n{partial}"
    except FileNotFoundError:
        return "[error: docker executable not found on host]"

    output = (proc.stdout or "") + (proc.stderr or "")
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f"\n[output truncated at {MAX_OUTPUT_CHARS} chars]"
    if not output.strip():
        output = f"[no output, exit code {proc.returncode}]"
    elif proc.returncode != 0:
        output += f"\n[exit code {proc.returncode}]"
    return output


def submit_flag(flag: str, correct_flag: str) -> dict:
    """Check a submitted flag against the known correct one."""
    flag = (flag or "").strip()
    if not FLAG_RE.fullmatch(flag):
        return {"correct": False, "message": "Not in a valid flag format: academy{...} or picoCTF{...}."}
    if not (correct_flag or "").strip():
        return {"correct": True, "message": "Flag format accepted (no known flag was provided to verify against)."}
    if hmac.compare_digest(flag.encode(), correct_flag.strip().encode()):
        return {"correct": True, "message": "Correct! Challenge solved."}
    return {"correct": False, "message": "Incorrect flag. Keep investigating."}


HINT_WAIT_SECONDS = 600
HINT_SEP = "\n---\n"  # must match dropui.py
_hints_given = 0


def request_hint(hint_file: str, wait: int = HINT_WAIT_SECONDS) -> str:
    """Return the next unused operator hint. If there is none, print a marker and wait for one."""
    global _hints_given
    if not hint_file:
        return "No hint channel is available in this run. Keep trying on your own."
    path = Path(hint_file)

    def read() -> list:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        return [h.strip() for h in text.split(HINT_SEP) if h.strip()]

    hints = read()
    _hints_given = min(_hints_given, len(hints))  # hints were cleared while the run was going
    if _hints_given >= len(hints):
        print("[HINT NEEDED] waiting for the operator to provide a hint...", flush=True)
        deadline = time.time() + wait
        while _hints_given >= len(hints) and time.time() < deadline:
            time.sleep(2)
            hints = read()
            _hints_given = min(_hints_given, len(hints))
    if _hints_given >= len(hints):
        return f"No new hint was provided within {wait}s. Keep trying on your own."
    _hints_given += 1
    return f"Operator hint {_hints_given}: {hints[_hints_given - 1]}"
