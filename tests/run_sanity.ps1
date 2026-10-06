# End-to-end sanity check: REAL tools.py + REAL Docker container + REAL model.
# Run from the project root:  powershell -File tests\run_sanity.ps1
$ErrorActionPreference = "Stop"
$expected = "academy{b64_w4rmup_d3c0d3d}"

docker cp challenge/base64_warmup.txt ctf-sandbox:/workspace/base64_warmup.txt
python agent/agent.py `
  --description "A file named base64_warmup.txt is in /workspace in the sandbox. Find the flag hidden in it." `
  --flag $expected --max-steps 8 --log workspace/sanity_log.json
if ($LASTEXITCODE -eq 0) { Write-Host "SANITY TEST PASSED" } else { Write-Host "SANITY TEST FAILED (see workspace/sanity_log.json)"; exit 1 }
