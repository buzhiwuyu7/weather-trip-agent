$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$project = $PSScriptRoot
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    foreach ($version in @('-3.14', '-3.13', '-3.12', '-3.11', '-3.10')) {
        $ErrorActionPreference = 'Continue'
        & py $version -c 'import sys; sys.exit(0)' 2>$null
        $probeExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($probeExit -eq 0) {
            & py $version (Join-Path $project 'app.py')
            exit $LASTEXITCODE
        }
    }
}
& python -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Please install Python 3.10 or newer.' }
& python (Join-Path $project 'app.py')
exit $LASTEXITCODE
