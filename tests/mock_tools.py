"""Docker-free stand-ins for tools.py. Same function signatures, fake filesystem."""
import base64

FAKE_FLAG = "academy{mock_f1ag_for_t3sts}"

FAKE_FS = {
    "notes.txt": "Nothing to see here. Try secret.b64\n",
    "secret.b64": base64.b64encode(FAKE_FLAG.encode()).decode() + "\n",
}


def run_command(command: str) -> str:
    """Tiny fake shell: supports ls, cat <file>, base64 -d, and `cat f | base64 -d`."""
    command = command.strip()
    if command in ("ls", "ls -la", "ls -l", "ls -a"):
        return "\n".join(sorted(FAKE_FS)) + "\n"
    if command.startswith("cat ") and "|" not in command:
        name = command[4:].strip()
        return FAKE_FS.get(name, f"cat: {name}: No such file or directory\n")
    if command.startswith("cat ") and "base64" in command:
        name = command[4:].split("|")[0].strip()
        if name not in FAKE_FS:
            return f"cat: {name}: No such file or directory\n"
        try:
            return base64.b64decode(FAKE_FS[name]).decode() + "\n"
        except Exception:
            return "base64: invalid input\n"
    return f"bash: {command.split()[0] if command else ''}: command not found (mock shell)\n"


def send_to_server(host: str, port: int, message: str = "", timeout: int = 10, messages=None) -> str:
    """Fake TCP exchange: canned banner plus an echo of the message. No Docker or network."""
    return (
        "--- Initial response ---\n"
        f"Welcome to the mock server at {host}:{port}\nEnter input:\n"
        "--- After sending input ---\n"
        f"You sent: {message}\n"
    )


def submit_flag(flag: str, correct_flag: str = FAKE_FLAG) -> dict:
    # Always checks against the hardcoded fake flag, ignoring whatever --flag was passed.
    ok = (flag or "").strip() == FAKE_FLAG
    return {"correct": ok, "message": "Correct! Challenge solved." if ok else "Incorrect flag."}


def request_hint(hint_file: str, wait: int = 0) -> str:
    return "No hint channel is available in this run. Keep trying on your own."
