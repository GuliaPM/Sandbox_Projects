<#
Install, run, refuse an upgrade, upgrade and uninstall the built MeetMiner in a sandbox, and print one result line.

    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\sandbox_test.ps1

It needs the installer in dist\ (python packaging\build.py). Nothing touches your real Start menu, Desktop, Installed apps
list or settings: shortcuts and the registry entry go to a temporary folder and a test-only registry key, and the app
talks to tools\fake_api.py instead of Anthropic, so it costs nothing and needs no key. It opens the app window for a few
seconds. The last line is "SANDBOX RESULT: PASS (n/n checks)" or "SANDBOX RESULT: FAIL (...)".
#>
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$version = (Select-String -Path (Join-Path $root 'src\meetminer\version.py') -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
$appName = (Select-String -Path (Join-Path $root 'src\meetminer\version.py') -Pattern 'APP_NAME = "([^"]+)"').Matches[0].Groups[1].Value
$setup = Get-ChildItem (Join-Path $root 'dist') -Filter "*-Setup-$version.exe" | Select-Object -First 1
if (-not $setup) { Write-Output "SANDBOX RESULT: FAIL (no setup program for $version in dist\; run packaging\build.py first)"; exit 1 }

$work = Join-Path ([IO.Path]::GetTempPath()) ('meetminer-sandbox-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$install = Join-Path $work 'install'; $sb = Join-Path $work 'sandbox'; $data = Join-Path $work 'data'; $docs = Join-Path $work 'transcripts'
$regKey = 'HKCU:\Software\' + ($appName -replace ' ', '') + 'SetupTest'
New-Item -ItemType Directory -Path $sb, $data, $docs | Out-Null
Copy-Item -Path (Join-Path $root 'samples\transcripts\*.txt') -Destination $docs
(@{ transcripts_dir = $docs } | ConvertTo-Json) | Set-Content -LiteralPath (Join-Path $data 'settings.json') -Encoding utf8

$results = New-Object System.Collections.Generic.List[object]
function Check([string]$name, [bool]$passed) { $results.Add([pscustomobject]@{ Name = $name; Passed = $passed }); Write-Output ("  [{0}] {1}" -f $(if ($passed) { 'PASS' } else { 'FAIL' }), $name) }
function RunSetup() { Start-Process -FilePath $setup.FullName -ArgumentList @('/S', '--dir', "`"$install`"", '--sandbox', "`"$sb`"", '--no-launch') -Wait -PassThru }
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
$api = $null; $app = $null

try {
    Write-Output "1. install into a sandbox"
    $p = RunSetup
    Check 'setup exits with 0' ($p.ExitCode -eq 0)
    Check 'the app program was installed' (Test-Path -LiteralPath (Join-Path $install "$($appName -replace ' ', '').exe"))
    Check 'shortcuts were created' ((Get-ChildItem -LiteralPath $sb -Recurse -Filter *.lnk | Measure-Object).Count -ge 1)
    Check 'the Installed-apps entry was created' (Test-Path $regKey)

    Write-Output "2. run it against the fake API"
    $api = Start-Process -FilePath $python -ArgumentList @("`"$(Join-Path $root 'tools\fake_api.py')`"", '8798') -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 2
    $env:MEETMINER_HOME = $data; $env:ANTHROPIC_BASE_URL = 'http://127.0.0.1:8798'; $env:ANTHROPIC_API_KEY = 'sk-ant-api03-FAKE-sandbox-0123456789abcdef'
    $exe = Join-Path $install "$($appName -replace ' ', '').exe"
    $app = Start-Process -FilePath $exe -ArgumentList @('--ask', '"What was decided about the launch date?"') -PassThru
    Remove-Item Env:\MEETMINER_HOME, Env:\ANTHROPIC_BASE_URL, Env:\ANTHROPIC_API_KEY
    Start-Sleep -Seconds 14
    $app.Refresh()
    Check "the app opened a window called $appName" ((-not $app.HasExited) -and $app.MainWindowTitle -eq $appName)
    $log = Join-Path $data 'usage.csv'
    Check 'a question was answered and logged' ((Test-Path $log) -and ((Get-Content $log).Count -ge 2))
    Check 'no crash log was written' (-not (Test-Path (Join-Path $data 'desktop-error.log')))

    Write-Output "3. upgrade while the app is open must be refused"
    $before = (Get-ChildItem -LiteralPath $install -Recurse -File | Measure-Object).Count
    $p2 = RunSetup
    $after = (Get-ChildItem -LiteralPath $install -Recurse -File | Measure-Object).Count
    Check 'the upgrade is refused with exit code 3' ($p2.ExitCode -eq 3)
    Check 'the installed files are untouched' ($before -eq $after)

    Write-Output "4. close the app, upgrade, uninstall"
    Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $p3 = RunSetup
    Check 'the upgrade works once the app is closed' ($p3.ExitCode -eq 0)
    $u = Start-Process -FilePath $exe -ArgumentList @('--uninstall', '--silent', '--sandbox', "`"$sb`"") -Wait -PassThru
    Check 'uninstall exits with 0' ($u.ExitCode -eq 0)
    Start-Sleep -Seconds 8
    Check 'the install folder is gone' (-not (Test-Path -LiteralPath $install))
    Check 'the shortcuts are gone' ((Get-ChildItem -LiteralPath $sb -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0)
    Check 'the Installed-apps entry is gone' (-not (Test-Path $regKey))
}
finally {
    if ($app) { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue }
    if ($api) { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

$failed = @($results | Where-Object { -not $_.Passed })
if ($failed.Count -eq 0) { Write-Output "SANDBOX RESULT: PASS ($($results.Count)/$($results.Count) checks)"; exit 0 }
Write-Output "SANDBOX RESULT: FAIL ($($failed.Count) of $($results.Count) checks failed: $(($failed | ForEach-Object Name) -join '; '))"
exit 1
