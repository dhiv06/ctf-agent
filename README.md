# CTF Agent

Autonomous beginner-CTF solver. A Gemini model (OpenAI-compatible API) runs a tool loop inside a Docker sandbox (`ctf-sandbox`) until it finds the flag.

## Quick start
Prereqs: Python 3.10+, Docker Desktop running, a Gemini API key.

```
python -m pip install -r agent/requirements.txt
```
Create `.env` in the project root (git-ignored):
```
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.8-flash   # optional, this is the default
# GROQ_API_KEY=...              # Groq (disabled)
```

### 1. Sandbox
Easiest: start it from the UI (step 2) with the **start sandbox** button. It builds the image if missing, then runs the container. Manually:
```
docker build -t ctf-sandbox .
docker run -d --name ctf-sandbox ctf-sandbox
docker start ctf-sandbox          # on later sessions
```
Rebuild the image after editing anything in `sandbox/` or the `Dockerfile`.

### 2. UI
```
python dropui.py
```
Open http://localhost:5000. Stdlib only, no Flask.
- Pick the **challenge type**: *general* (skills `general,git`; host/port shown) or *forensics* (skills `general,forensics`; host/port hidden, a dropped file is required).
- Fill in the description, optional flag/hints/max steps/model, drop challenge files, and run.
- Dropped files go to `challenge/` and are copied into the sandbox's `/workspace`.

### 3. CLI (without the UI)
```
python agent/agent.py --description "..." [--flag FLAG] [--file PATH] [--skills general,git] [--max-steps 20] [--model M] [--hint-file F] [--log run_log.json]
```
`--file` is repeatable and copies a host file into `/workspace` before the run.

## Repo layout
| Path | What it is |
|---|---|
| `agent/agent.py` | CLI entry point and the model/tool loop; builds the system prompt, logs to JSON |
| `agent/tools.py` | Tools the model can call (run commands in the sandbox, flag check, hints) |
| `agent/prompts/core.txt` | Base system prompt, always loaded |
| `agent/prompts/skills/` | Optional prompt modules: `general`, `git`, `forensics` (chosen via `--skills`) |
| `agent/requirements.txt` | Python dependencies |
| `dropui.py` | Web UI + local server; starts sandbox, uploads files, launches the agent |
| `Dockerfile` | Sandbox image: forensics/stego tools (binwalk, exiftool, steghide, tesseract, ...), git/ssh, netcat, Python libs |
| `sandbox/` | Helper scripts installed in the sandbox: `triage FILE`, `bits2bytes FILE`, `ocr IMAGE` |
| `challenge/` | Files dropped via the UI (created at runtime) |
| `workspace/` | Local fixtures/logs, e.g. `base64_warmup.txt` |
| `tests/` | Mock tests, forensics checks, and real-model sanity scripts |

## Tests
| Test | Command | Needs |
|---|---|---|
| Mock loop (scripted fake model) | `python tests/test_mock.py` | nothing |
| Skills, UI toggle, page JS, sandbox helpers | `python tests/test_forensics.py` | nothing |
| Sanity: base64 warmup | `powershell -File tests\run_sanity.ps1` | Docker sandbox + API key |
| Sanity: forensics | `powershell -File tests\run_sanity_forensics.ps1` | Docker sandbox + API key |

Mock tests pass with `PASS` lines and exit code 0. Sanity tests print `SOLVED` / `SANITY TEST PASSED` on success (typically 2-4 steps); on failure, inspect `workspace/sanity_log.json`.
