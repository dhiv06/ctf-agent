# End-to-end forensics sanity check: REAL tools.py + REAL Docker container + REAL model.
# Run from the project root:  powershell -File tests\run_sanity_forensics.ps1
$ErrorActionPreference = "Stop"
$expected = "picoCTF{b1ts_t0_t3xt_t3st}"

python agent/agent.py `
  --description "This file doesn't look like much... just a bunch of 1s and 0s. Can you recover anything meaningful from it?" `
  --file challenge/bits_warmup.bin --skills general,forensics `
  --flag $expected --max-steps 10 --log workspace/sanity_forensics_log.json
if ($LASTEXITCODE -eq 0) { Write-Host "FORENSICS SANITY TEST PASSED" } else { Write-Host "FORENSICS SANITY TEST FAILED (see workspace/sanity_forensics_log.json)"; exit 1 }
