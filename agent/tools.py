"""Tools exposed to the CTF agent."""
import hmac
import re
import subprocess

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


# Runs inside the container. argv: host port message timeout. Reads until the server
# goes quiet (or closes), so prompts that never send a newline still come back.
_TCP_SCRIPT = (
    "import socket,sys,time\n"
    "h,p,m,t=sys.argv[1],int(sys.argv[2]),sys.argv[3],float(sys.argv[4])\n"
    "def rd(s):\n"
    " d=b'';e=time.time()+t\n"
    " while time.time()<e:\n"
    "  s.settimeout(min(2.0,max(0.1,e-time.time())))\n"
    "  try:c=s.recv(4096)\n"
    "  except socket.timeout:\n"
    "   if d:break\n"
    "   continue\n"
    "  if not c:break\n"
    "  d+=c\n"
    " return d.decode('utf-8','replace')\n"
    "s=socket.create_connection((h,p),timeout=t)\n"
    "a=rd(s)\n"
    "s.sendall(m.encode()+b'\\n')\n"
    "b=rd(s)\n"
    "print('--- Initial response ---\\n'+a+'\\n--- After sending input ---\\n'+b)\n"
)


def send_to_server(host: str, port: int, message: str, timeout: int = 10) -> str:
    """Connect to host:port from inside the sandbox, read the banner, send one line, read the reply."""
    try:
        port = int(port)
        timeout = max(1, int(timeout))
    except (TypeError, ValueError):
        return "[error: port and timeout must be integers]"
    try:
        proc = subprocess.run(
            ["docker", "exec", CONTAINER, "python3", "-c", _TCP_SCRIPT,
             str(host), str(port), str(message), str(timeout)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout * 2 + 10,
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
    if hmac.compare_digest(flag.encode(), correct_flag.strip().encode()):
        return {"correct": True, "message": "Correct! Challenge solved."}
    return {"correct": False, "message": "Incorrect flag. Keep investigating."}
