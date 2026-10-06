"""Test the agent loop with NO Docker and NO API calls.

A scripted fake model plays the LLM: it lists files, reads one, decodes it, submits.
Passing means the loop, tool dispatch, flag check and JSON logging all work.

Run from the project root:  python tests/test_mock.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "agent"))

import mock_tools  # noqa: E402

sys.modules["tools"] = mock_tools  # agent.py's `from tools import ...` now gets the mocks

import agent  # noqa: E402

SCRIPT = [
    ("I'll list the files first.", "run_command", {"command": "ls"}),
    ("secret.b64 looks interesting; read it.", "run_command", {"command": "cat secret.b64"}),
    ("Looks base64; decode it.", "run_command", {"command": "cat secret.b64 | base64 -d"}),
    ("That is flag-shaped; submit it.", "submit_flag", {"flag": mock_tools.FAKE_FLAG}),
]


class FakeClient:
    def __init__(self, *a, **k):
        self.calls = 0
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        text, name, args = SCRIPT[self.calls]
        self.calls += 1
        tc = NS(id=f"call_{self.calls}", function=NS(name=name, arguments=json.dumps(args)))
        return NS(choices=[NS(message=NS(content=text, tool_calls=[tc]))])


def main() -> int:
    os.environ.setdefault("GROQ_API_KEY", "dummy")
    agent.OpenAI = FakeClient

    log = Path(tempfile.mkdtemp()) / "mock_log.json"
    sys.argv = ["agent.py", "--description", "mock", "--flag", mock_tools.FAKE_FLAG,
                "--max-steps", "6", "--log", str(log)]
    exit_code = agent.main()

    data = json.loads(log.read_text(encoding="utf-8"))
    checks = {
        "agent exited 0": exit_code == 0,
        "log says solved": data["solved"] is True,
        "4 steps logged": len(data["steps"]) == 4,
        "reasoning logged": all(s["reasoning"] for s in data["steps"]),
        "decoded flag in step 3 result": mock_tools.FAKE_FLAG in data["steps"][2]["result"],
    }
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    print("\nMOCK TEST PASSED" if all(checks.values()) else "\nMOCK TEST FAILED")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
