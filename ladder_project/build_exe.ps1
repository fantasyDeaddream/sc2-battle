param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"

$processNames = @("LadderServer", "LadderClient")
foreach ($processName in $processNames) {
    Get-Process -Name $processName -ErrorAction SilentlyContinue | Stop-Process -Force
}

$PythonHome = ""
$CondaExe = ""
$UseCondaFallback = $false
$TkBinaryArgs = @()

if (Test-Path $PythonExe) {
    $pythonItem = Get-Item $PythonExe
    if (-not $pythonItem.PSIsContainer) {
        $PythonHome = Split-Path -Parent $pythonItem.FullName
        $candidateConda = Join-Path $PythonHome "_conda.exe"
        if (Test-Path $candidateConda) {
            $CondaExe = $candidateConda
            $UseCondaFallback = $true
        }
    }
}

function Invoke-PythonStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StepName,
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    Write-Host $StepName
    if ($UseCondaFallback) {
        & $CondaExe run -p $PythonHome python @Args
        $code = $LASTEXITCODE
    }
    else {
        & $PythonExe @Args
        $code = $LASTEXITCODE
    }

    if ($code -ne 0) {
        throw "$StepName failed with exit code $code"
    }
}

function Find-TkRuntimeDll {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SearchDir,
        [Parameter(Mandatory = $true)]
        [string]$RegexPattern
    )

    if (-not (Test-Path $SearchDir)) {
        return $null
    }

    $candidate = Get-ChildItem $SearchDir -Filter "*.dll" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match $RegexPattern } |
        Select-Object -First 1

    if ($null -eq $candidate) {
        return $null
    }

    return $candidate.FullName
}

Write-Host "Python executable: $PythonExe"
if ($UseCondaFallback) {
    Write-Host "Conda mode enabled: $CondaExe"
}

if ($PythonHome) {
    $condaBin = Join-Path $PythonHome "Library\bin"
    $dllDirs = @($condaBin, (Join-Path $PythonHome "DLLs"))

    $tclDll = $null
    $tkDll = $null
    foreach ($dir in $dllDirs) {
        if (-not $tclDll) {
            $tclDll = Find-TkRuntimeDll -SearchDir $dir -RegexPattern '^tcl\d+t\.dll$'
        }
        if (-not $tkDll) {
            $tkDll = Find-TkRuntimeDll -SearchDir $dir -RegexPattern '^tk\d+t\.dll$'
        }
    }

    if ($tclDll) {
        Write-Host "Including Tcl DLL: $tclDll"
        $TkBinaryArgs += @("--add-binary", "$tclDll;.")
    }
    if ($tkDll) {
        Write-Host "Including Tk DLL: $tkDll"
        $TkBinaryArgs += @("--add-binary", "$tkDll;.")
    }
}

$basePyInstallerArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed")

Invoke-PythonStep -StepName "Installing or upgrading PyInstaller..." -Args @("-m", "pip", "install", "--upgrade", "pyinstaller")
Invoke-PythonStep -StepName "Building LadderServer EXE..." -Args ($basePyInstallerArgs + $TkBinaryArgs + @("--name", "LadderServer", "server_app.py"))
Invoke-PythonStep -StepName "Building LadderClient EXE..." -Args ($basePyInstallerArgs + $TkBinaryArgs + @("--name", "LadderClient", "client_app.py"))

Write-Host "Build completed. EXE files are in the dist directory."
Write-Host "If python is not available, specify full executable path, for example:"
Write-Host '.\build_exe.ps1 -PythonExe "E:\ProgramData\anaconda3new\python.exe"'
