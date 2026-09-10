param([switch]$Analyze,[switch]$Harden)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
 New-Item -ItemType Directory -Path build -Force | Out-Null
 $includes=@('-Isrc','-Ivendor/civetweb/include','-Ivendor/cjson','-Ivendor/sqlite','-Ivendor/sodium/libsodium-win64/include')
 $flags=@('-std=c11','-O2','-DNO_SSL','-DNO_CGI','-D_GNU_SOURCE','-DSQLITE_THREADSAFE=1')+$includes
 # 本 MinGW 工具链无 libasan/libubsan 运行库；加固构建以 FORTIFY + 栈保护替代动态 sanitizer。
 if($Harden){$flags+=@('-D_FORTIFY_SOURCE=3','-fstack-protector-strong','-ftrivial-auto-var-init=zero','-fno-strict-overflow')}
 $sources=@('vendor/civetweb/src/civetweb.c','vendor/cjson/cJSON.c','vendor/sqlite/sqlite3.c')
 $objects=@()
 foreach($s in $sources){$o='build/'+[IO.Path]::GetFileNameWithoutExtension($s)+'.o'; if(-not (Test-Path -LiteralPath $o) -or (Get-Item -LiteralPath $s).LastWriteTimeUtc -gt (Get-Item -LiteralPath $o).LastWriteTimeUtc){ & gcc @flags -c $s -o $o; if($LASTEXITCODE -ne 0){throw "Compile failed: $s"} }; $objects+=$o}
 $own=@('src/main.c','src/util.c','src/db.c','src/service.c','src/http.c','src/ratelimit.c','src/metrics.c')
 $checks=@('-Wall','-Wextra','-Wformat=2','-Wshadow','-Wstrict-prototypes')
 if($Analyze){$checks+='-fanalyzer'}
 $libs=@('-Lvendor/sodium/libsodium-win64/lib','-lsodium','-lws2_32','-ladvapi32')
 $suffix=if($Harden){'-harden'}else{''}
 & gcc @flags @checks -municode @own @objects @libs -o ("build/lab-booking"+$suffix+".exe")
 if($LASTEXITCODE -ne 0){throw 'Release build failed'}
 & gcc @flags @checks -municode -DTEST_FAULTS @own @objects @libs -o ("build/lab-booking-test"+$suffix+".exe")
 if($LASTEXITCODE -ne 0){throw 'Test build failed'}
 & gcc @flags @checks -Ivendor/unity/src -DWATCHDOG_MS=50 vendor/unity/src/unity.c src/util.c src/db.c src/service.c src/ratelimit.c tests/unit.c @objects @libs -o ("build/unit-tests"+$suffix+".exe")
 if($LASTEXITCODE -ne 0){throw 'Unit test build failed'}
 Copy-Item -Path 'vendor/sodium/libsodium-win64/bin/*.dll' -Destination build -Force
 if(-not $Harden){ & ($root+'\build\unit-tests'+'.exe'); if($LASTEXITCODE -ne 0){throw 'Unit tests failed'} }
 Write-Output ("All builds completed"+$(if($Harden){' (hardened: FORTIFY+SSP+var-init).'}else{'; unit tests passed.'}))
} finally {Pop-Location}
