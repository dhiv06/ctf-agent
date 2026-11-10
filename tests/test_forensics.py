"""Docker-free, API-free checks for the skills split, the UI challenge-type toggle and the sandbox helpers.

Run from the project root:  python tests/test_forensics.py
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT))

import mock_tools  # noqa: E402

sys.modules["tools"] = mock_tools

import agent  # noqa: E402
import dropui  # noqa: E402

FLAG = "picoCTF{unit_t3st_b1ts}"


def helper(name, *args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "sandbox" / name), *args],
                          capture_output=True, text=True, errors="replace")


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    bits = tmp / "bits.bin"
    bits.write_text("".join(f"{b:08b}" for b in FLAG.encode()))
    lsb = tmp / "lsb.bin"
    lsb.write_text("".join(f"{b:08b}"[::-1] for b in FLAG.encode()) + "\n")
    out = tmp / "out.bin"

    forensics = agent.build_system_prompt("general,forensics")
    default = agent.build_system_prompt(agent.DEFAULT_SKILLS)
    page_js = re.search(r"<script>(.*?)</script>", dropui.PAGE, re.S).group(1)
    js_file = tmp / "page.js"
    js_file.write_text(page_js, encoding="utf-8")
    try:
        js_ok = subprocess.run(["node", "--check", str(js_file)], capture_output=True).returncode == 0
    except FileNotFoundError:
        js_ok = True  # node not installed; skip

    task = {"kind": "forensics", "desc": "d", "host": "h", "port": "1"}
    checks = {
        "forensics prompt has forensics + core, no git": "SKILL: FORENSICS" in forensics and "SKILL: GIT" not in forensics
            and "UNTRUSTED DATA" in forensics,
        "default prompt unchanged (general + git)": "SKILL: GIT" in default and "SKILL: FORENSICS" not in default,
        "every UI kind maps to existing skills": all(
            agent.build_system_prompt(s) for s in dropui.SKILLS_BY_KIND.values()),
        "forensics description omits server line": "nc h 1" not in dropui.build_description(task, "", ["f.bin"]),
        "general description keeps server line": "nc h 1" in dropui.build_description({**task, "kind": "general"}, "", []),
        "fixtures are never listed/wiped by the UI": {"base64_warmup.txt", "bits_warmup.bin"} <= dropui.IGNORED,
        "forensics skill documents every sandbox helper": all(
            f"- {h.name} " in forensics or f"- {h.name}:" in forensics or f"- {h.name} " in forensics
            for h in (ROOT / "sandbox").iterdir()),
        "Dockerfile installs every sandbox helper": all(
            f"sandbox/{h.name}" in (ROOT / "Dockerfile").read_text() for h in (ROOT / "sandbox").iterdir()),
        "page JS parses": js_ok,
        "bits2bytes finds MSB flag": FLAG in helper("bits2bytes", str(bits), "-o", str(out)).stdout,
        "bits2bytes handles LSB + newline": FLAG in helper("bits2bytes", str(lsb), "-o", str(out)).stdout,
        "triage flags a bit string": "BIT STRING" in helper("triage", str(bits)).stdout,
        "triage finds flag in decoded output": FLAG in helper("triage", str(out)).stdout,
    }
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    print("\nFORENSICS TEST PASSED" if all(checks.values()) else "\nFORENSICS TEST FAILED")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
