# 覆盖率测量：--coverage 编译单元与服务器，运行单元测试与接口走查，gcov 汇总行覆盖率
param([string]$OutDir="docs/evidence/coverage")
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
 New-Item -ItemType Directory -Path build,$OutDir -Force | Out-Null
 $cov=@('--coverage')
 $includes=@('-Isrc','-Ivendor/civetweb/include','-Ivendor/cjson','-Ivendor/sqlite','-Ivendor/sodium/libsodium-win64/include','-Ivendor/unity/src')
 $base=@('-std=c11','-O0','-DNO_SSL','-DNO_CGI','-D_GNU_SOURCE','-DSQLITE_THREADSAFE=1')
 $objs=@('build/cov_civetweb.o','build/cov_cJSON.o','build/cov_sqlite3.o')
 foreach($s in @('vendor/civetweb/src/civetweb.c','vendor/cjson/cJSON.c','vendor/sqlite/sqlite3.c')){
  $o='build/cov_'+[IO.Path]::GetFileNameWithoutExtension($s)+'.o'
  if(-not (Test-Path $o)){ & gcc $base $includes $cov -c $s -o $o; if($LASTEXITCODE -ne 0){throw "compile $s"} }
 }
 $ownArgs=@('src/main.c','src/util.c','src/db.c','src/service.c','src/http.c','src/ratelimit.c','src/metrics.c','src/log.c')
 # 显式对象编译：gcno/gcda 落在 build/ 下，路径确定
 $owncov=@()
 foreach($s in $ownArgs){
  $o='build/cov_'+[IO.Path]::GetFileNameWithoutExtension($s)+'.o'
  & gcc $base $includes $cov -DWATCHDOG_MS=50 -c $s -o $o; if($LASTEXITCODE -ne 0){throw "compile $s"}
  $owncov+=$o
 }
 $utest='build/cov_unit.o'
 & gcc $base $includes $cov -DWATCHDOG_MS=50 -Ivendor/unity/src -c tests/unit.c -o $utest; if($LASTEXITCODE -ne 0){throw 'compile unit'}
 & gcc $base $includes $cov -DWATCHDOG_MS=50 vendor/unity/src/unity.c $utest $owncov $objs -Lvendor/sodium/libsodium-win64/lib -lsodium -lws2_32 -ladvapi32 -o build/unit-tests-cov.exe
 if($LASTEXITCODE -ne 0){throw 'unit cov build failed'}
 & gcc $base $includes $cov '-DWATCHDOG_MS=5000' -municode $owncov $objs -Lvendor/sodium/libsodium-win64/lib -lsodium -lws2_32 -ladvapi32 -o build/lab-booking-cov.exe
 if($LASTEXITCODE -ne 0){throw 'server cov build failed'}
 # 运行单元测试（gcda 落盘）
 # 项目路径含中文会让 libgcov 静默写失败：GCOV_PREFIX 重定向到 ASCII 临时目录，事后搬回 build/
 if(Test-Path C:\gcov-out){Remove-Item C:\gcov-out -Recurse -Force}
 $env:GCOV_PREFIX='C:\gcov-out';$env:GCOV_PREFIX_STRIP=0
 & .\build\unit-tests-cov.exe | Select-Object -Last 2
 if($LASTEXITCODE -ne 0){throw 'unit cov run failed'}
 # 接口走查 + 优雅停机（CTRL_BREAK → SIGBREAK → 正常退出刷写 gcda）
 $env:LAB_TEST_PASSWORD='Cov-Run-Password-9'
 python tests/exercise_cov.py --exe build/lab-booking-cov.exe --port 8799
 if($LASTEXITCODE -ne 0){throw 'exercise failed'}
 # gcov 汇总：把 GCOV_PREFIX 树中的 gcda 搬回 build/（与 gcno 同目录），逐文件生成行覆盖率
 Get-ChildItem C:\gcov-out -Recurse -Filter *.gcda | ForEach-Object { Copy-Item $_.FullName -Destination build -Force }
 $gcda=Get-ChildItem build -Filter cov_*.gcda
 $raw=foreach($g in $gcda){ & gcov -b $g.FullName 2>$null }
 ($raw | Out-String) | Set-Content -Path "$OutDir/gcov-raw.txt" -Encoding utf8
 $pairs=@()
 for($i=0;$i -lt $raw.Count;$i++){
  if($raw[$i] -match "File 'src/(.+)\.c'"){
   $a=$i+1;$b=$i+3
   $seg=($raw[$a..$b] | Select-String "Lines executed")
   if($seg){ $pairs+=("src/"+$Matches[1]+".c -> "+$seg[0].ToString().Trim()) }
  }
 }
 ($pairs -join "`n") | Set-Content -Path "$OutDir/summary.txt" -Encoding utf8
 Write-Output ($pairs -join "`n")
} finally {Pop-Location}
