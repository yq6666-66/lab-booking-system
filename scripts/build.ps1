param([switch]$Analyze)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
 New-Item -ItemType Directory -Path build -Force | Out-Null
 $includes=@('-Isrc','-Ivendor/civetweb/include','-Ivendor/cjson','-Ivendor/sqlite','-Ivendor/sodium/libsodium-win64/include')
 $flags=@('-std=c11','-O2','-DNO_SSL','-DNO_CGI','-D_GNU_SOURCE','-DSQLITE_THREADSAFE=1')+$includes
 $sources=@('vendor/civetweb/src/civetweb.c','vendor/cjson/cJSON.c','vendor/sqlite/sqlite3.c')
 $objects=@()
 foreach($s in $sources){$o='build/'+[IO.Path]::GetFileNameWithoutExtension($s)+'.o'; if(-not (Test-Path -LiteralPath $o) -or (Get-Item -LiteralPath $s).LastWriteTimeUtc -gt (Get-Item -LiteralPath $o).LastWriteTimeUtc){ & gcc @flags -c $s -o $o; if($LASTEXITCODE -ne 0){throw "Compile failed: $s"} }; $objects+=$o}
 $own=@('src/main.c','src/util.c','src/db.c','src/service.c','src/http.c')
 $checks=@('-Wall','-Wextra','-Wformat=2','-Wshadow','-Wstrict-prototypes')
 if($Analyze){$checks+='-fanalyzer'}
 $libs=@('-Lvendor/sodium/libsodium-win64/lib','-lsodium','-lws2_32','-ladvapi32')
 & gcc @flags @checks -municode @own @objects @libs -o build/lab-booking.exe
 if($LASTEXITCODE -ne 0){throw 'Release build failed'}
 & gcc @flags @checks -municode -DTEST_FAULTS @own @objects @libs -o build/lab-booking-test.exe
 if($LASTEXITCODE -ne 0){throw 'Test build failed'}
 Copy-Item -Path 'vendor/sodium/libsodium-win64/bin/*.dll' -Destination build -Force
 Write-Output 'Both C builds completed.'
} finally {Pop-Location}
