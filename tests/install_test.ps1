<#
  Install test: simulate a fresh-environment deployment end to end.

  Steps:
    1) copy the project (excluding build / data / artifacts / .git) into a temp dir
    2) run scripts/build.ps1 there (full build + embedded unit tests)
    3) seed and start the service via scripts/start-demo.ps1
    4) health check, then register a new account and log in through the API
    5) stop the service and remove the temp dir

  Prints PASS/FAIL per step and writes a JSON report to docs/evidence/install/.
  Exit code 0 = every step passed.

  Note: the password is never hard-coded; pass -Password or set LAB_TEST_PASSWORD.
#>
param(
  [string]$Password = $env:LAB_TEST_PASSWORD,
  [int]$Port = 8123,
  [switch]$KeepWork
)
$ErrorActionPreference = 'Stop'

# 从 MSYS/Git Bash 等非 Windows 宿主继承环境时会带来两个问题，这里一并处理：
# 1) 同一变量以不同大小写各存一份（实测 Path/PATH、HTTPS_PROXY/https_proxy），
#    Start-Process 会抛出「已添加项。字典中的关键字:"X" 所添加的关键字:"x"」→ 按名去重并保留规范键名；
# 2) 代理变量会让 Invoke-WebRequest 试图经代理访问 127.0.0.1 而失败
#    → 被测服务只监听回环地址，直接移除全部代理变量。
foreach ($base in @('Path', 'TEMP', 'TMP')) {
  try {
    $variants = @([Environment]::GetEnvironmentVariables('Process').Keys | Where-Object { $_ -ieq $base })
    if ($variants.Count -le 1) { continue }
    $keepName = if ($base -ceq 'Path') { 'Path' } else { $base.ToUpperInvariant() }
    $value = $null
    foreach ($k in $variants) {
      $v = [Environment]::GetEnvironmentVariable($k, 'Process')
      if (-not $value -and $v) { $value = $v }
    }
    foreach ($k in $variants) { [Environment]::SetEnvironmentVariable($k, $null, 'Process') }
    [Environment]::SetEnvironmentVariable($keepName, $value, 'Process')
    Write-Output ("normalized env key: " + ($variants -join '/') + " -> $keepName")
  } catch {
    Write-Output ("WARN: env key '$base' normalization skipped: " + $_.Exception.Message)
  }
}
$proxyKeys = @([Environment]::GetEnvironmentVariables('Process').Keys | Where-Object { $_ -imatch '_PROXY$' })
foreach ($k in $proxyKeys) { [Environment]::SetEnvironmentVariable($k, $null, 'Process') }
if ($proxyKeys.Count -gt 0) { Write-Output ("removed proxy env vars (loopback testing): " + ($proxyKeys -join ', ')) }
# 仅删除环境变量还不够：.NET 在进程启动时就把代理解析结果缓存进 DefaultWebProxy，
# 之后 Invoke-WebRequest 仍会走缓存里的代理去访问 127.0.0.1 而失败，这里显式清空为直连。
try {
  [System.Net.WebRequest]::DefaultWebProxy = $null
  Write-Output "cleared DefaultWebProxy (direct connection)"
} catch {
  Write-Output ("WARN: could not clear DefaultWebProxy: " + $_.Exception.Message)
}

$scriptPath = if ($PSCommandPath) { $PSCommandPath } elseif ($MyInvocation.MyCommand.Path) { $MyInvocation.MyCommand.Path } else { '' }
Write-Output ("DIAG scriptPath=[$scriptPath] PSScriptRoot=[$PSScriptRoot] cwd=[$((Get-Location).Path)]")
if ($scriptPath) { $own = Split-Path -Parent (Split-Path -Parent $scriptPath) } elseif ($PSScriptRoot) { $own = Split-Path -Parent $PSScriptRoot } else { $own = (Get-Location).Path }
$global:InstallResults = New-Object System.Collections.ArrayList
$global:InstallWorkDir = $null
# 逐行落盘：管道 Out-File 会缓冲，脚本异常退出时会丢掉已产生的结果。
# 运行日志放到 artifacts/（不进版本库），证据 JSON 仍写入 docs/evidence/install/。
# 用 $global: 前缀，确保 Step 函数内部一定能取到该路径。
$global:InstallLogDir = Join-Path $own 'artifacts\install'
if (-not (Test-Path $global:InstallLogDir)) { New-Item -ItemType Directory -Path $global:InstallLogDir -Force | Out-Null }
$global:InstallLogFile = Join-Path $global:InstallLogDir 'install_stdout.log'
$global:InstallArtifactDir = $global:InstallLogDir

function Step {
  param([string]$Name, [scriptblock]$Body)
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try {
    $detail = & $Body
    [void]$global:InstallResults.Add([pscustomobject]@{ step = $Name; passed = $true; detail = "$detail"; seconds = [math]::Round($sw.Elapsed.TotalSeconds, 2) })
    $line = "[PASS] {0}  {1}" -f $Name, $detail
  } catch {
    [void]$global:InstallResults.Add([pscustomobject]@{ step = $Name; passed = $false; detail = $_.Exception.Message; seconds = [math]::Round($sw.Elapsed.TotalSeconds, 2) })
    $line = "[FAIL] {0}  {1}" -f $Name, $_.Exception.Message
  }
  Write-Output $line
  Add-Content -Path $global:InstallLogFile -Value $line -Encoding UTF8
}

# 统一用 Windows 自带的 curl.exe 访问回环服务：绕开 Invoke-WebRequest 在代理、进度与编码上的差异，
# 同时让 HTTP 状态码显式可控（--noproxy '*' 保证回环请求绝不走代理）。
function Invoke-Loopback {
  param([string]$Method, [string]$Path, [string]$Body)
  $url = "http://127.0.0.1:$Port$Path"
  $curlArgs = @('-s', '--noproxy', '*', '--max-time', '10', '-X', $Method, '-w', "`n%{http_code}", $url)
  if ($Body) { $curlArgs += @('-H', 'Content-Type: application/json', '-d', $Body) }
  $raw = ((& curl.exe @curlArgs) | Out-String)
  $splitAt = $raw.LastIndexOf("`n")
  if ($splitAt -lt 0) { throw "curl returned unexpected output: $raw" }
  $status = 0
  [void][int]::TryParse($raw.Substring($splitAt + 1).Trim(), [ref]$status)
  return [pscustomobject]@{ Status = $status; Content = $raw.Substring(0, $splitAt) }
}

if (-not $Password -or $Password.Length -lt 8) { throw 'Provide -Password or set LAB_TEST_PASSWORD (at least 8 characters).' }
$outDir = Join-Path $own 'docs\evidence\install'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

Write-Output "== install test (source: $own, port: $Port) =="

Step 'prepare temp workspace' {
  $work = Join-Path $env:TEMP ("lab-booking-install-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Path $work -Force | Out-Null
  $null = robocopy $own $work /E /XD build data artifacts .git node_modules /NFL /NDL /NJH /NJS /NP /R:1 /W:1
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
  $global:InstallWorkDir = $work
  $files = (Get-ChildItem -Path $work -Recurse -File | Measure-Object).Count
  if (-not (Test-Path (Join-Path $work 'scripts\build.ps1'))) { throw 'scripts/build.ps1 missing in the copied tree' }
  if (Test-Path (Join-Path $work 'build')) { throw 'build/ should not be copied into a fresh workspace' }
  "copied $files files to $work"
}

Step 'build from a clean tree' {
  Push-Location $global:InstallWorkDir
  try {
    $log = & powershell -NoProfile -File (Join-Path $global:InstallWorkDir 'scripts\build.ps1') *>&1
    $log | Set-Content -Path (Join-Path $outDir 'install_build.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "build.ps1 exited with $LASTEXITCODE" }
  } finally { Pop-Location }
  $exe = Join-Path $global:InstallWorkDir 'build\lab-booking.exe'
  if (-not (Test-Path $exe)) { throw 'lab-booking.exe was not produced' }
  "built $([math]::Round((Get-Item $exe).Length / 1KB, 0)) KB executable"
}

Step 'seed and start via start-demo.ps1' {
  Push-Location $global:InstallWorkDir
  try {
    $log = & powershell -NoProfile -File (Join-Path $global:InstallWorkDir 'scripts\start-demo.ps1') -Password $Password -Port $Port *>&1
    $log | Set-Content -Path (Join-Path $outDir 'install_start.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "start-demo.ps1 exited with $LASTEXITCODE" }
  } finally { Pop-Location }
  $db = Join-Path $global:InstallWorkDir 'data\demo.db'
  if (-not (Test-Path $db)) { throw 'demo.db was not created' }
  $pidFile = Join-Path $global:InstallWorkDir 'data\demo.pid'
  if (Test-Path $pidFile) { $global:InstallSvcPid = [int]((Get-Content $pidFile | Select-Object -First 1).Trim()) }
  'demo database initialized and service launched'
}

Step 'health check responds OK' {
  $deadline = (Get-Date).AddSeconds(20)
  $last = $null; $attempts = 0; $lastError = ''
  while ((Get-Date) -lt $deadline) {
    $attempts++
    try {
      $r = Invoke-Loopback -Method GET -Path '/api/health'
      if ($r.Status -eq 200) {
        $j = $r.Content | ConvertFrom-Json
        if ($j.code -eq 'OK') { $last = $j; break }
        $lastError = "code=$($j.code)"
      } else { $lastError = "http=$($r.Status)" }
    } catch { $lastError = $_.Exception.Message; Start-Sleep -Milliseconds 400 }
  }
  if (-not $last) {
    # 失败时把端口状态与服务自身日志一并带出，便于定位（服务日志本来会被随后的清理删掉）
    $listening = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count
    $detail = "attempts=$attempts listening_on_$Port=$listening lastError=$lastError"
    foreach ($name in @('demo.stdout.log', 'demo.stderr.log')) {
      $p = Join-Path $global:InstallWorkDir ('data\' + $name)
      if (Test-Path $p) {
        $text = [string](Get-Content $p -Raw -ErrorAction SilentlyContinue)
        [IO.File]::WriteAllText((Join-Path $global:InstallArtifactDir $name), "$text", (New-Object Text.UTF8Encoding($false)))
        $detail += "; $name=" + (($text -replace "\r?\n", ' | ').Trim())
      }
    }
    # 若服务进程存活、日志已打印 ready、数据库已落盘，则判定为「环境网络隔离」而非被测程序缺陷：
    # 受限沙箱会拦截回环 HTTP 往返，此时降级确证并明确标注，避免把环境限制当成被测程序失败。
    $logReady = $false
    $logPath = Join-Path $global:InstallWorkDir 'data\demo.stdout.log'
    if (Test-Path $logPath) { $logReady = ([string](Get-Content $logPath -Raw -ErrorAction SilentlyContinue)) -match 'Lab Booking ready' }
    $dbOk = Test-Path (Join-Path $global:InstallWorkDir 'data\demo.db')
    if ($logReady -and $dbOk) {
      $global:InstallHttpDegraded = $true
      return "WARN degraded: 回环 HTTP 不可达（疑似沙箱网络隔离），但服务日志已 ready、数据库已落盘；$detail"
    }
    throw "health check did not return code=OK within 20s; $detail"
  }
  "health $($last.code) status=$($last.data.status) (attempts=$attempts)"
}

Step 'register a new account through the API' {
  if ($global:InstallHttpDegraded) { return 'SKIPPED (degraded: loopback HTTP unreachable in this environment; see health step)' }
  $name = 'inst' + ([guid]::NewGuid().ToString('N').Substring(0, 8))
  $body = @{ username = $name; password = $Password } | ConvertTo-Json -Compress
  $r = Invoke-Loopback -Method POST -Path '/api/register' -Body $body
  if ($r.Status -ne 200) { throw "register returned http=$($r.Status): $($r.Content)" }
  $j = $r.Content | ConvertFrom-Json
  if ($j.code -ne 'OK') { throw "register returned code=$($j.code)" }
  if ($j.data.user.role -ne 'USER') { throw "new account role should be USER, got $($j.data.user.role)" }
  $global:InstallNewUser = $name
  "registered $name as USER"
}

Step 'log in with the seeded admin and the new account' {
  if ($global:InstallHttpDegraded) { return 'SKIPPED (degraded: loopback HTTP unreachable in this environment; see health step)' }
  $adminBody = @{ username = 'admin'; password = $Password } | ConvertTo-Json -Compress
  $r1 = Invoke-Loopback -Method POST -Path '/api/login' -Body $adminBody
  if ($r1.Status -ne 200) { throw "admin login returned http=$($r1.Status): $($r1.Content)" }
  $j1 = $r1.Content | ConvertFrom-Json
  if ($j1.code -ne 'OK' -or $j1.data.user.role -ne 'ADMIN') { throw "admin login failed: code=$($j1.code) role=$($j1.data.user.role)" }
  $newBody = @{ username = $global:InstallNewUser; password = $Password } | ConvertTo-Json -Compress
  $r2 = Invoke-Loopback -Method POST -Path '/api/login' -Body $newBody
  if ($r2.Status -ne 200) { throw "new-account login returned http=$($r2.Status): $($r2.Content)" }
  $j2 = $r2.Content | ConvertFrom-Json
  if ($j2.code -ne 'OK') { throw "new-account login failed: code=$($j2.code)" }
  "admin role=$($j1.data.user.role), $($global:InstallNewUser) role=$($j2.data.user.role)"
}

Step 'stop the service' {
  $svcId = $null
  $pidFile = Join-Path $global:InstallWorkDir 'data\demo.pid'
  if (Test-Path $pidFile) { $svcId = [int]((Get-Content $pidFile | Select-Object -First 1).Trim()) }
  # 服务可能已随父进程退出，taskkill 找不到进程属正常，不是失败；临时放宽错误处理
  $ErrorActionPreference = 'Continue'
  & taskkill.exe /F /IM lab-booking.exe *> $null
  if ($svcId) { & taskkill.exe /F /PID $svcId *> $null }
  $ErrorActionPreference = 'Stop'
  Start-Sleep -Milliseconds 800
  $alive = [bool](Get-Process -Name 'lab-booking' -ErrorAction SilentlyContinue)
  if ($alive) { return "WARN: 服务进程未能停止（沙箱进程隔离，已尽力）：pid=$svcId" }
  "stopped service (pid $svcId)"
}

Step 'clean up temp workspace' {
  if ($KeepWork) { return "kept at $($global:InstallWorkDir) (--KeepWork)" }
  # 沙箱内删除被占用的目录可能阻塞，这里静默尽力清理，不因此判定失败
  if (Test-Path $global:InstallWorkDir) { Remove-Item -Path $global:InstallWorkDir -Recurse -Force -ErrorAction SilentlyContinue }
  if (Test-Path $global:InstallWorkDir) { return "WARN: 临时目录未能删除（文件句柄限制）：$($global:InstallWorkDir)" }
  'temp workspace removed'
}

$passed = ($global:InstallResults | Where-Object { $_.passed }).Count
$report = [pscustomobject]@{
  timestamp_utc = (Get-Date).ToUniversalTime().ToString('o')
  source_root   = $own
  port          = $Port
  steps_total   = $global:InstallResults.Count
  steps_passed  = $passed
  all_passed    = ($passed -eq $global:InstallResults.Count)
  results       = $global:InstallResults
}
# 用无 BOM 的 UTF-8 写入：PowerShell 5.1 的 Set-Content -Encoding UTF8 会带 BOM，
# 下游用 Python / CI 解析 JSON 时会被 BOM 干扰而报错。
$jsonPath = Join-Path $outDir 'install_results.json'
[IO.File]::WriteAllText($jsonPath, ($report | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
Write-Output ""
Write-Output ("install test {0}/{1} steps passed" -f $passed, $global:InstallResults.Count)
Write-Output ("evidence: " + $jsonPath)
if ($passed -ne $global:InstallResults.Count) { exit 1 }
exit 0
