param(
    [string]$Repo = "",
    [string]$BaselineDir = "",
    [string]$OutputDir = "",
    [string]$BaselineFile = "baseline.txt",
    [string]$OriginalBaselineFile = "original_baseline.txt",
    [ValidateSet("algorithmic", "empirical")]
    [string]$Class = "empirical",
    [switch]$Rebaseline,
    [int]$MeasureRuns = 0,
    [int]$WarmupRuns = 1,
    [double]$MinImproveSeconds = 5.0,
    [double]$WelchPThreshold = 0.05,
    [switch]$BaselineOnly,
    [switch]$WithCompare,
    [switch]$WithBuild,
    [string]$RunnerPath = "",
    [string]$RunnerWorkDir = "",
    [string]$ComparePath = "",
    [string]$BuildCommand = "",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

function Fail($Message) {
    Write-Host "FAIL: $Message"
    exit 1
}

function Is-WindowsHost() {
    return [System.IO.Path]::DirectorySeparatorChar -eq '\'
}

function Default-Jobs() {
    if ($env:NUMBER_OF_PROCESSORS) {
        return [int]$env:NUMBER_OF_PROCESSORS
    }
    return 4
}

function Resolve-DefaultPath($Provided, $WindowsPath, $LinuxPath) {
    if (![string]::IsNullOrWhiteSpace($Provided)) {
        return $Provided
    }
    if (Is-WindowsHost) {
        return $WindowsPath
    }
    return $LinuxPath
}

function Resolve-RelativeToRepo($PathValue, $RepoRoot) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return Join-Path $RepoRoot $PathValue
}

function Add-WindowsToolPath() {
    if (!(Is-WindowsHost)) {
        return $null
    }

    $prefixes = @(
        "C:\msys64\ucrt64\bin",
        "C:\msys64\usr\bin"
    )
    $existing = @($prefixes | Where-Object { Test-Path $_ })
    if ($existing.Count -eq 0) {
        return $null
    }

    $oldPath = $env:Path
    $env:Path = (($existing + @($oldPath)) -join ";")
    return $oldPath
}

function Restore-Path($OldPath) {
    if ($null -ne $OldPath) {
        $env:Path = $OldPath
    }
}

function Median($Values) {
    $sorted = @($Values | Sort-Object)
    if ($sorted.Count -eq 0) {
        Fail "no timing values"
    }
    return [double]$sorted[[int][math]::Floor($sorted.Count / 2)]
}

function Mean($Values) {
    if ($Values.Count -eq 0) {
        return $null
    }
    $sum = 0.0
    foreach ($value in $Values) {
        $sum += [double]$value
    }
    return [math]::Round($sum / $Values.Count, 6)
}

function Read-BaselineRecord($Path) {
    if (!(Test-Path $Path)) {
        return $null
    }

    $samples = @()
    $medianValue = $null
    foreach ($line in Get-Content $Path) {
        if ($line -match '^run_\d+=(.+)$') {
            $samples += [double]$Matches[1]
        } elseif ($line -match '^median=(.+)$') {
            $medianValue = [double]$Matches[1]
        }
    }

    return [pscustomobject]@{
        Path = $Path
        Samples = @($samples)
        Median = $medianValue
        Mean = (Mean $samples)
    }
}

function Invoke-WelchTest($PythonExe, $HelperPath, $CandidateSamples, $BaselineSamples) {
    if (!(Test-Path $HelperPath)) {
        Fail "Welch helper not found: $HelperPath"
    }
    if ($CandidateSamples.Count -lt 2 -or $BaselineSamples.Count -lt 2) {
        Fail "Welch test requires at least 2 candidate and 2 baseline samples"
    }

    $candidate = ($CandidateSamples | ForEach-Object { [string]$_ }) -join ","
    $baseline = ($BaselineSamples | ForEach-Object { [string]$_ }) -join ","
    $json = & $PythonExe $HelperPath --candidate $candidate --baseline $baseline 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "Welch helper failed with exit $LASTEXITCODE`: $json"
    }
    return ($json | ConvertFrom-Json)
}

function Ensure-ResultsHeader($Path, $Header) {
    if (!(Test-Path $Path)) {
        $Header | Set-Content -Encoding ASCII $Path
        return
    }

    $existing = Get-Content $Path
    if ($existing.Count -eq 0) {
        $Header | Set-Content -Encoding ASCII $Path
        return
    }

    if ($existing[0] -ne $Header) {
        @($Header) + $existing | Set-Content -Encoding ASCII $Path
    }
}

function Run-RunnerToFile($Exe, $WorkDir, $LogPath) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Push-Location $WorkDir
    try {
        $oldPath = Add-WindowsToolPath
        try {
            & $Exe > $LogPath 2>&1
            if ($LASTEXITCODE -ne 0) {
                Fail "runner exited with code $LASTEXITCODE; log=$LogPath"
            }
        }
        finally {
            Restore-Path $oldPath
        }
    }
    finally {
        Pop-Location
    }
    $sw.Stop()
    return [math]::Round($sw.Elapsed.TotalSeconds, 3)
}

function Run-CompareToFile($CompareExe, $Candidate, $Baseline, $LogPath) {
    $oldPath = Add-WindowsToolPath
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $script:ErrorActionPreference = "Continue"
        & $CompareExe $Candidate $Baseline *> $LogPath
        return $LASTEXITCODE
    }
    catch {
        $_ | Out-File -FilePath $LogPath -Append -Encoding ASCII
        return 1
    }
    finally {
        $script:ErrorActionPreference = $oldErrorActionPreference
        Restore-Path $oldPath
    }
}

$Repo = Resolve-DefaultPath $Repo `
    "C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming" `
    "/root/work/Code1/psi-trader-liangjunming"
$BaselineDir = Resolve-DefaultPath $BaselineDir `
    "C:\Users\liangjunming\Desktop\work\Code1\dataset\baseline\psi-factor-20140102-20140103" `
    "/root/work/Code1/dataset/baseline/psi-factor-20140102-20140103"
$OutputDir = Resolve-DefaultPath $OutputDir `
    "C:\Users\liangjunming\Desktop\work\Code1\dataset\output" `
    "/root/work/Code1/dataset/output"

if ($MeasureRuns -le 0) {
    if ($Rebaseline) {
        $MeasureRuns = 7
    } elseif ($BaselineOnly) {
        $MeasureRuns = 3
    } elseif ($Class -eq "empirical") {
        $MeasureRuns = 5
    } else {
        $MeasureRuns = 3
    }
}

if ($WarmupRuns -lt 0) {
    Fail "WarmupRuns must be >= 0"
}
if ($MeasureRuns -lt 1) {
    Fail "MeasureRuns must be >= 1"
}
if (!$BaselineOnly -and !$WithCompare) {
    Fail "candidate and bundle gates require -WithCompare; use -BaselineOnly for timing-only bootstrap runs"
}

if ([string]::IsNullOrWhiteSpace($Python)) {
    if (Is-WindowsHost) {
        $Python = "python"
    } else {
        $Python = "python3"
    }
}

$gateClass = $Class
if ($Rebaseline) {
    $gateClass = "bundle"
} elseif ($BaselineOnly) {
    $gateClass = "baseline"
}

Write-Host "=== PSI OPTIMIZATION EVALUATOR ==="
Write-Host "Repo: $Repo"
Write-Host "BaselineDir: $BaselineDir"
Write-Host "OutputDir: $OutputDir"
Write-Host "GateClass: $gateClass"
Write-Host "BaselineOnly: $BaselineOnly"
Write-Host "WithCompare: $WithCompare"
Write-Host "WithBuild: $WithBuild"
Write-Host "MeasureRuns: $MeasureRuns"
Write-Host "WarmupRuns: $WarmupRuns"

if (!(Test-Path $Repo)) {
    Fail "repo not found"
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$HftRoot = Split-Path -Parent $ScriptDir
$EvidenceRoot = Join-Path $HftRoot "experiments\evaluator-runs"
$ResultsFile = Join-Path $HftRoot "experiments\results.tsv"
$WelchHelper = Join-Path $ScriptDir "welch_test.py"
$RunId = Get-Date -Format "yyyyMMdd-HHmmss"
$EvidenceDir = Join-Path $EvidenceRoot $RunId
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ResultsFile) | Out-Null

if ([string]::IsNullOrWhiteSpace($RunnerPath)) {
    if (Is-WindowsHost) {
        $RunnerPath = Join-Path $Repo "build\build_x64\RelWithDebInfo\bin\PsiTraderRunner\PsiTraderRunner.exe"
    } else {
        $RunnerPath = Join-Path $Repo "build/build_x64/RelWithDebInfo/bin/PsiTraderRunner/PsiTraderRunner"
    }
}

if ([string]::IsNullOrWhiteSpace($ComparePath)) {
    if (Is-WindowsHost) {
        $ComparePath = Join-Path $Repo "build\build_x64\RelWithDebInfo\bin\tools\compare_parquet_factor.exe"
    } else {
        $ComparePath = Join-Path $Repo "build/build_x64/RelWithDebInfo/bin/tools/compare_parquet_factor"
    }
}

if ([string]::IsNullOrWhiteSpace($RunnerWorkDir)) {
    $RunnerWorkDir = Join-Path $Repo "PsiTraderRunner"
}

if ($WithBuild -and [string]::IsNullOrWhiteSpace($BuildCommand)) {
    if (Is-WindowsHost) {
        $BuildCommand = "cmake --build --preset mingw-relwithdebinfo --target PsiTraderRunner"
    } else {
        $BuildCommand = "cmake --build build --target PsiTraderRunner --parallel $(Default-Jobs)"
    }
}

if (!(Test-Path $RunnerPath)) {
    Fail "runner not found: $RunnerPath"
}

if (!(Test-Path $RunnerWorkDir)) {
    Fail "runner work dir not found: $RunnerWorkDir"
}

if ($WithCompare -and !(Test-Path $ComparePath)) {
    Write-Host "WARN: compare tool not found at expected path: $ComparePath"
    Write-Host "Build integration for tools/compare_parquet_factor.cpp may still need to be wired."
}

if ($WithBuild) {
    Write-Host ""
    Write-Host "=== BUILD ==="
    Push-Location $Repo
    try {
        $oldPath = Add-WindowsToolPath
        try {
            Invoke-Expression $BuildCommand
            $buildExit = $LASTEXITCODE
            if ($buildExit -ne 0) {
                Fail "cmake build failed (exit $buildExit)"
            }
        }
        finally {
            Restore-Path $oldPath
        }
    }
    finally {
        Pop-Location
    }
}

$runLogDir = Join-Path $Repo "timing_logs\evaluate"
New-Item -ItemType Directory -Force -Path $runLogDir | Out-Null
$compareLogDir = Join-Path $Repo "compare_logs\evaluate"
New-Item -ItemType Directory -Force -Path $compareLogDir | Out-Null

Write-Host ""
Write-Host "=== WARMUP ==="
for ($i = 1; $i -le $WarmupRuns; $i++) {
    $log = Join-Path $runLogDir "warmup_$i.log"
    $elapsed = Run-RunnerToFile $RunnerPath $RunnerWorkDir $log
    Write-Host "Warmup $i wall clock: $elapsed s"
}

Write-Host ""
Write-Host "=== MEASURE ==="
$samples = @()
for ($i = 1; $i -le $MeasureRuns; $i++) {
    $log = Join-Path $runLogDir "measure_$i.log"
    $elapsed = Run-RunnerToFile $RunnerPath $RunnerWorkDir $log
    $samples += $elapsed
    Write-Host "Run $i wall clock: $elapsed s"
}

$median = Median $samples
$sampleMean = Mean $samples
Write-Host "Mean: $sampleMean s"
Write-Host "Median: $median s"

$compareResults = @()
$correctnessPass = $true
$correctnessText = "SKIPPED"

if ($WithCompare) {
    Write-Host ""
    Write-Host "=== CORRECTNESS ==="
    if (!(Test-Path $BaselineDir)) {
        Fail "baseline dir not found; create it from an accepted output run first"
    }

    if (!(Test-Path $ComparePath)) {
        Fail "compare_parquet_factor not available"
    }

    $baselineFiles = @(Get-ChildItem -Path $BaselineDir -Filter "*.parquet" | Sort-Object Name)
    $outputFiles = @(Get-ChildItem -Path $OutputDir -Filter "*.parquet" |
        Where-Object { $_.Name -match '2014010[23]2014010[23]_0_54(47|48|49|50)\.parquet$' } |
        Sort-Object Name)

    if ($baselineFiles.Count -ne 8) {
        Write-Host "FAIL_CORRECTNESS: baseline parquet count must be 8, got $($baselineFiles.Count)"
        Write-Host ""
        Write-Host "VERDICT: FAIL_CORRECTNESS"
        exit 1
    }
    if ($outputFiles.Count -ne 8) {
        Write-Host "FAIL_CORRECTNESS: output parquet count for target factors must be 8, got $($outputFiles.Count)"
        Write-Host ""
        Write-Host "VERDICT: FAIL_CORRECTNESS"
        exit 1
    }

    $baselineNames = @($baselineFiles | ForEach-Object { $_.Name })
    $outputNames = @($outputFiles | ForEach-Object { $_.Name })
    $missingFromOutput = @($baselineNames | Where-Object { $_ -notin $outputNames })
    $extraInOutput = @($outputNames | Where-Object { $_ -notin $baselineNames })
    if ($missingFromOutput.Count -gt 0 -or $extraInOutput.Count -gt 0) {
        Write-Host "Baseline/output filename mismatch."
        if ($missingFromOutput.Count -gt 0) {
            Write-Host "Missing from output: $($missingFromOutput -join ', ')"
        }
        if ($extraInOutput.Count -gt 0) {
            Write-Host "Extra in output: $($extraInOutput -join ', ')"
        }
        Write-Host "VERDICT: FAIL_CORRECTNESS"
        exit 1
    }

    foreach ($base in $baselineFiles) {
        $candidate = Join-Path $OutputDir $base.Name
        $log = Join-Path $compareLogDir ($base.BaseName + ".compare.log")
        $exitCode = Run-CompareToFile $ComparePath $candidate $base.FullName $log

        $compareResults += [pscustomobject]@{
            Name = $base.Name
            ExitCode = $exitCode
            Log = $log
        }

        Write-Host "compare $($base.Name): exit=$exitCode"
        if ($exitCode -ne 0) {
            $correctnessPass = $false
        }
    }

    $correctnessText = "FAIL_CORRECTNESS"
    if ($correctnessPass) {
        $correctnessText = "PASS"
    }
}

$baselinePath = Resolve-RelativeToRepo $BaselineFile $Repo
$acceptedBaselinePath = Join-Path $Repo "baseline.txt"
$originalBaselinePath = Resolve-RelativeToRepo $OriginalBaselineFile $Repo

if (!(Test-Path $originalBaselinePath) -and (Test-Path $acceptedBaselinePath)) {
    Copy-Item -LiteralPath $acceptedBaselinePath -Destination $originalBaselinePath
    Write-Host "Original baseline snapshot created: $originalBaselinePath"
}

$comparisonPath = $acceptedBaselinePath
if ($Rebaseline) {
    $comparisonPath = $originalBaselinePath
}

$baselineRecord = $null
$baselineMedian = $null
$baselineMean = $null
$improve = $null
$welch = $null
$perfPass = $true
$perfReason = "not gated"

if ((Test-Path (Split-Path -Parent $baselinePath)) -and
    (Test-Path $comparisonPath) -and
    ([System.IO.Path]::GetFullPath($baselinePath) -ne [System.IO.Path]::GetFullPath($comparisonPath))) {
    $baselineRecord = Read-BaselineRecord $comparisonPath
}

$baselineTiming = Join-Path $BaselineDir "baseline_timing_seconds.txt"
if ($null -ne $baselineRecord) {
    $baselineMedian = $baselineRecord.Median
    $baselineMean = $baselineRecord.Mean
} elseif (Test-Path $baselineTiming) {
    $baselineMedian = [double](Get-Content $baselineTiming | Select-Object -First 1)
}

if ($null -ne $baselineMedian) {
    $improve = [math]::Round($baselineMedian - $median, 3)
    Write-Host "baseline_median_seconds=$baselineMedian"
    Write-Host "improve_seconds=$improve"
} else {
    Write-Host "WARN: no comparison baseline; timing gate skipped for bootstrap run"
}

if ($WithCompare -and !$correctnessPass) {
    $perfPass = $false
    $perfReason = "correctness failed"
} elseif ($gateClass -eq "empirical" -and $null -ne $baselineRecord) {
    if ($samples.Count -lt 5 -or $baselineRecord.Samples.Count -lt 5) {
        $perfPass = $false
        $perfReason = "empirical gate requires at least 5 candidate and 5 baseline samples"
    } else {
        $welch = Invoke-WelchTest $Python $WelchHelper $samples $baselineRecord.Samples
        $perfPass = (($welch.p_two_tailed -lt $WelchPThreshold) -and ($welch.candidate_mean -lt $welch.baseline_mean))
        $perfReason = "Welch p=$($welch.p_two_tailed), candidate_mean=$($welch.candidate_mean), baseline_mean=$($welch.baseline_mean)"
    }
} elseif ($gateClass -eq "bundle" -and $null -ne $baselineMedian) {
    if ($samples.Count -lt 7) {
        $perfPass = $false
        $perfReason = "bundle gate requires 7 measured samples"
    } elseif ($improve -lt $MinImproveSeconds) {
        $perfPass = $false
        $perfReason = "bundle median improvement $improve s < $MinImproveSeconds s"
    } else {
        $perfReason = "bundle median improvement $improve s >= $MinImproveSeconds s"
    }
} elseif ($gateClass -eq "algorithmic") {
    $perfPass = $true
    $perfReason = "Class A records performance but does not gate on it"
}

# Write candidate result file before any gate exit so it is always available for inspection.
$lines = @()
$lines += "repo=$Repo"
$lines += "runner=$RunnerPath"
$lines += "runner_work_dir=$RunnerWorkDir"
$lines += "gate_class=$gateClass"
$lines += "created_at=$((Get-Date).ToString('s'))"
for ($i = 0; $i -lt $samples.Count; $i++) {
    $runNumber = $i + 1
    $lines += "run_$runNumber=$($samples[$i])"
}
$lines += "mean=$sampleMean"
$lines += "median=$median"
if ($null -ne $baselineMedian) {
    $lines += "comparison_baseline=$comparisonPath"
    $lines += "baseline_median=$baselineMedian"
    if ($null -ne $baselineMean) {
        $lines += "baseline_mean=$baselineMean"
    }
    $lines += "improve_seconds=$improve"
}
if ($null -ne $welch) {
    $lines += "welch_p_two_tailed=$($welch.p_two_tailed)"
    $lines += "welch_t=$($welch.t_statistic)"
    $lines += "welch_df=$($welch.degrees_of_freedom)"
}
$lines += "perf_gate=$perfPass"
$lines += "perf_reason=$perfReason"
$lines += ""
$lines += "[compare]"
$lines += "enabled=$WithCompare"
if ($WithCompare) {
    $lines += "baseline_dir=$BaselineDir"
    $lines += "output_dir=$OutputDir"
    $lines += "compare_exe=$ComparePath"
    foreach ($result in $compareResults) {
        $lines += "$($result.Name)=exit:$($result.ExitCode);log:$($result.Log)"
    }
    $lines += "correctness=$correctnessText"
}
$lines | Set-Content -Encoding ASCII $baselinePath
Write-Host "Written to: $baselinePath"

$verdict = "PASS"
if ($WithCompare -and !$correctnessPass) {
    $verdict = "FAIL_CORRECTNESS"
} elseif (!$perfPass) {
    if ($gateClass -eq "bundle") {
        $verdict = "FAIL_BUNDLE"
    } else {
        $verdict = "FAIL_PERF"
    }
}

$evidenceLines = @()
$evidenceLines += "# Evaluator Run $RunId"
$evidenceLines += ""
$evidenceLines += "## Command"
$evidenceLines += ""
$evidenceLines += '```powershell'
$evidenceLines += "$($MyInvocation.Line)"
$evidenceLines += '```'
$evidenceLines += ""
$evidenceLines += "## Configuration"
$evidenceLines += ""
$evidenceLines += "- Repo: $Repo"
$evidenceLines += "- BaselineDir: $BaselineDir"
$evidenceLines += "- OutputDir: $OutputDir"
$evidenceLines += "- BaselineFile: $baselinePath"
$evidenceLines += "- OriginalBaselineFile: $originalBaselinePath"
$evidenceLines += "- ComparisonBaseline: $comparisonPath"
$evidenceLines += "- GateClass: $gateClass"
$evidenceLines += "- WithBuild: $WithBuild"
$evidenceLines += "- WithCompare: $WithCompare"
$evidenceLines += "- WarmupRuns: $WarmupRuns"
$evidenceLines += "- MeasureRuns: $MeasureRuns"
$evidenceLines += "- MinImproveSeconds: $MinImproveSeconds"
$evidenceLines += "- WelchPThreshold: $WelchPThreshold"
$evidenceLines += "- RunnerPath: $RunnerPath"
$evidenceLines += "- RunnerWorkDir: $RunnerWorkDir"
$evidenceLines += "- ComparePath: $ComparePath"
if ($WithBuild) {
    $evidenceLines += "- BuildCommand: $BuildCommand"
}
$evidenceLines += ""
$evidenceLines += "## Timing"
$evidenceLines += ""
for ($i = 0; $i -lt $samples.Count; $i++) {
    $runNumber = $i + 1
    $evidenceLines += "- Run $runNumber`: $($samples[$i]) s"
}
$evidenceLines += "- Mean: $sampleMean s"
$evidenceLines += "- Median: $median s"
if ($null -ne $baselineMedian) {
    $evidenceLines += "- Baseline median: $baselineMedian s"
    if ($null -ne $baselineMean) {
        $evidenceLines += "- Baseline mean: $baselineMean s"
    }
    $evidenceLines += "- Improve seconds: $improve"
}
$evidenceLines += ""
$evidenceLines += "## Correctness"
$evidenceLines += ""
if ($WithCompare) {
    foreach ($result in $compareResults) {
        $evidenceLines += "- $($result.Name): exit=$($result.ExitCode), log=$($result.Log)"
    }
    $evidenceLines += "- Correctness: $correctnessText"
} else {
    $evidenceLines += "- Compare gate disabled"
}
$evidenceLines += ""
$evidenceLines += "## Performance Gate"
$evidenceLines += ""
$evidenceLines += "- Gate class: $gateClass"
$evidenceLines += "- Pass: $perfPass"
$evidenceLines += "- Reason: $perfReason"
if ($null -ne $welch) {
    $evidenceLines += "- Welch p two-tailed: $($welch.p_two_tailed)"
    $evidenceLines += "- Welch t statistic: $($welch.t_statistic)"
    $evidenceLines += "- Welch degrees of freedom: $($welch.degrees_of_freedom)"
}
$evidenceLines += ""
$evidenceLines += "## Verdict"
$evidenceLines += ""
$evidenceLines += $verdict
$evidencePath = Join-Path $EvidenceDir "run.md"
$evidenceLines | Set-Content -Encoding UTF8 $evidencePath

$resultsHeader = "timestamp`tverdict`tgate_class`twith_build`twith_compare`tmeasure_runs`twarmup_runs`tmedian`tmean`tbaseline_median`tbaseline_mean`timprove_seconds`twelch_p`twelch_candidate_mean`twelch_baseline_mean`tperf_gate`tperf_reason`tbaseline_file`tcomparison_baseline`tevidence"
Ensure-ResultsHeader $ResultsFile $resultsHeader

$welchP = ""
$welchCandidateMean = ""
$welchBaselineMean = ""
if ($null -ne $welch) {
    $welchP = $welch.p_two_tailed
    $welchCandidateMean = $welch.candidate_mean
    $welchBaselineMean = $welch.baseline_mean
}

"$RunId`t$verdict`t$gateClass`t$WithBuild`t$WithCompare`t$MeasureRuns`t$WarmupRuns`t$median`t$sampleMean`t$baselineMedian`t$baselineMean`t$improve`t$welchP`t$welchCandidateMean`t$welchBaselineMean`t$perfPass`t$perfReason`t$baselinePath`t$comparisonPath`t$evidencePath" |
    Add-Content -Encoding ASCII $ResultsFile

Write-Host "Evidence written to: $evidencePath"
Write-Host "Result appended to: $ResultsFile"

if ($WithCompare -and !$correctnessPass) {
    Write-Host ""
    Write-Host "VERDICT: FAIL_CORRECTNESS"
    exit 1
}

if (!$perfPass) {
    Write-Host ""
    Write-Host "VERDICT: $verdict ($perfReason)"
    exit 1
}

if ($WithCompare) {
    Write-Host "correctness=PASS"
}

Write-Host ""
Write-Host "VERDICT: PASS"
exit 0
