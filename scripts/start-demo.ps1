# Start demo/test service: seeds data\demo.db on first run, listens on http://127.0.0.1:8080
param([string]$Password="Demo-Lab-2026",[int]$Port=8080,[switch]$Reset)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
 $db='data/demo.db';$pidfile='data\demo.pid'
 if($Reset -and (Test-Path $db)){Remove-Item "$db*" -Force;Write-Output 'Old demo database removed'}
 if(-not (Test-Path 'data')){New-Item -ItemType Directory -Path data -Force|Out-Null}
 if(Test-Path $pidfile){$old=Get-Content $pidfile -ErrorAction SilentlyContinue;if($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)){Stop-Process -Id $old -Force;Write-Output "Stopped old instance PID $old"};Remove-Item $pidfile -Force}
 if(-not (Test-Path $db)){
  $env:LAB_SEED_PASSWORD=$Password
  & '.\build\lab-booking.exe' --db $db --seed --init-only
  if($LASTEXITCODE -ne 0){throw 'Seed initialization failed'}
  Remove-Item Env:\LAB_SEED_PASSWORD
  Write-Output 'Demo database initialized (3 labs, 14 days of slots, admin + user01-20)'
 }
 $p=Start-Process -FilePath "$root\build\lab-booking.exe" -ArgumentList @('--db',$db,'--web','web','--port',"$Port") -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput 'data\demo.stdout.log' -RedirectStandardError 'data\demo.stderr.log'
 $p.Id|Set-Content $pidfile
 Start-Sleep -Seconds 2
 $h=Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 5
 Write-Output "Service started: http://127.0.0.1:$Port  (PID $($p.Id), HTTP $($h.StatusCode))"
 Write-Output 'Accounts: admin (administrator) and user01-user20, password = the one set at initialization'
} finally {Pop-Location}
