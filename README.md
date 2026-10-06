# CTF Agent

Autonomous beginner-CTF solver using Groq (OpenAI-compatible API) and a Docker sandbox (`ctf-sandbox`).

## Setup
```
python -m pip install -r agent/requirements.txt
```
Put `GROQ_API_KEY=...` in `.env` in the project root (git-ignored). Optional: `GROQ_MODEL=...`
(default `openai/gpt-oss-120b`; override with `--model`).

## Test 1: mock test (no Docker, no API calls, free)
```
python tests/test_mock.py
```
A scripted fake model drives the real loop against `tests/mock_tools.py`.
- **Success:** five `PASS` lines and `MOCK TEST PASSED`, exit code 0.
- **Failure:** any `FAIL` line or a traceback, exit code 1. The loop, tool dispatch, flag check or logging is broken.

## Test 2: sanity test (real Docker + real Groq calls)
Needs the `ctf-sandbox` container running and `GROQ_API_KEY` set.
```
powershell -File tests\run_sanity.ps1
```
Copies `challenge/base64_warmup.txt` into the sandbox and asks the agent to find the flag
(`academy{b64_w4rmup_d3c0d3d}`, base64-encoded in the file).
- **Success:** `SOLVED` then `SANITY TEST PASSED`, exit code 0. Typically 2-4 steps.
- **Failure:** `Not solved within 8 steps` / `SANITY TEST FAILED`. Inspect `workspace/sanity_log.json`
  to see whether the model's reasoning, a tool call or the Docker setup went wrong.
