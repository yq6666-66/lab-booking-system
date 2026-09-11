# Install test: simulate a full deployment into an empty directory --
# copy artifacts -> first init -> idempotent re-init -> start -> health ->
# CLI integrity -> stop -> cleanup.
# Usage: powershell -ExecutionPolicy Bypass -File tests/install_test.ps1
# NOTE: keep this file ASCII-only. PowerShell 5.1 reads BOM-less UTF-8 as
# ANSI; multibyte comments can swallow the newline and merge with the
# next statement, silently skipping commands.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
# Sandbox under repo results dir, not %TEMP%: real-time AV may remove
# freshly copied unsigned exes from %TEMP%. This test verifies a full
# deploy into an empty directory; every other test already runs the exe
# under tests/results.
$tmp = Join-Path $root ("tests/results/install-" + [guid]::NewGuid().ToString('N').Substring(0,8))
$exe = Join-Path $root 'build/lab-booking.exe'
if (-not (Test-Path $exe)) { throw 'run scripts/build.ps1 first to produce build/lab-booking.exe' }
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null

try {
  # 1. simulate the delivered package: exe + runtime dll + frontend assets
  Copy-Item $exe (Join-Path $tmp 'lab-booking.exe')
  Copy-Item (Join-Path $root 'build/libsodium-26.dll') (Join-Path $tmp 'libsodium-26.dll')
  Copy-Item (Join-Path $root 'web') (Join-Path $tmp 'web') -Recurse
  if (-not (Test-Path (Join-Path $tmp 'lab-booking.exe'))) { throw 'exe missing after package copy' }
  Write-Host '1/6 package copied'

  # 2. first init: seeding must succeed on an empty directory
  $env:LAB_SEED_PASSWORD = 'Install-Test-2026!'
  & (Join-Path $tmp 'lab-booking.exe') --db (Join-Path $tmp 'lab.db') --seed --init-only 2>$null
  if ($LASTEXITCODE -ne 0) { throw "first init failed: $LASTEXITCODE" }
  if (-not (Test-Path (Join-Path $tmp 'lab.db'))) { throw 'database not created' }
  Write-Host '2/6 first init ok'

  # 3. re-install idempotency: seeding the same db again must not fail
  & (Join-Path $tmp 'lab-booking.exe') --db (Join-Path $tmp 'lab.db') --seed --init-only 2>$null
  if ($LASTEXITCODE -ne 0) { throw "re-init not idempotent: $LASTEXITCODE" }
  Write-Host '3/6 re-init idempotent ok'

  # 4. start + health check (with version field)
  $proc = Start-Process -FilePath (Join-Path $tmp 'lab-booking.exe') `
      -ArgumentList @('--db',(Join-Path $tmp 'lab.db'),'--web',(Join-Path $tmp 'web'),'--port','18432') `
      -PassThru -WindowStyle Hidden -RedirectStandardError (Join-Path $tmp 'stderr.log')
  $ok = $false
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    try {
      $h = Invoke-RestMethod -Uri 'http://127.0.0.1:18432/api/health' -TimeoutSec 2
      if ($h.data.status -eq 'ok' -and $h.data.version) { $ok = $true; break }
    } catch {}
  }
  if (-not $ok) { throw 'health check never became ok' }
  Write-Host "4/6 health ok (version $($h.data.version))"

  # 5. static asset reachable + CLI integrity check after stop
  $probe = Invoke-WebRequest -Uri 'http://127.0.0.1:18432/' -UseBasicParsing -TimeoutSec 5
  if ($probe.StatusCode -ne 200) { throw 'index page not served' }
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  $proc.WaitForExit(5000) | Out-Null
  & (Join-Path $tmp 'lab-booking.exe') --db (Join-Path $tmp 'lab.db') --check 2>$null
  if ($LASTEXITCODE -ne 0) { throw "post-stop integrity check failed: $LASTEXITCODE" }
  Write-Host '5/6 integrity check ok after stop'

  # 6. cleanup and confirm no leftovers
  Remove-Item $tmp -Recurse -Force
  if (Test-Path $tmp) { throw 'cleanup failed' }
  Write-Host '6/6 cleanup ok'
  Write-Host '[PASS] install: fresh-deploy flow verified (init/re-init/health/check/cleanup)'
  exit 0
} catch {
  Write-Host "[FAIL] install: $($_.Exception.Message)"
  exit 1
} finally {
  Get-Process lab-booking -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$tmp*" } | Stop-Process -Force -ErrorAction SilentlyContinue
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}
