param([switch]$Once)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$dashboardOrigin = "http://127.0.0.1:8767"
$stateUrl = "http://127.0.0.1:8767/api/state"
$intervalSeconds = 900
$gateStaleSeconds = 15
$tradingViewStaleMilliseconds = 45000
$logDirectory = Join-Path $projectRoot "logs"
$logPath = Join-Path $logDirectory "health-check.log"

function Write-HealthLog {
    param([string]$Level, [string]$Message)

    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $line = "{0} [{1}] {2}" -f [DateTime]::UtcNow.ToString("o"), $Level, $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-DashboardState {
    Invoke-RestMethod -Uri $stateUrl -TimeoutSec 10
}

function Get-ListenerOwners {
    @(
        Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort 8767 -State Listen `
            -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Invoke-HealthScan {
    $recovery = "none"
    try {
        $state = Get-DashboardState
    }
    catch {
        Write-HealthLog "WARN" "dashboard unreachable; running one project autostart recovery"
        & (Join-Path $projectRoot "autostart_dashboard.bat") --recover | Out-Null
        try {
            $state = Get-DashboardState
            $recovery = "dashboard-restarted"
        }
        catch {
            Write-HealthLog "ERROR" "dashboard recovery failed: $($_.Exception.Message)"
            return $false
        }
    }

    $issues = New-Object System.Collections.Generic.List[string]
    if ($state.mode -ne "PAPER") { $issues.Add("mode=$($state.mode)") }
    if ($state.engine.status -ne "SIMULATION_READY") {
        $issues.Add("engine=$($state.engine.status)")
    }
    if ($state.engine.risk_engine_enabled -ne $true) { $issues.Add("risk-engine-disabled") }
    if ($state.data.status -ne "LIVE") {
        $issues.Add("data=$($state.data.status)")
    }
    elseif ($null -eq $state.data.age_seconds -or [double]$state.data.age_seconds -gt $gateStaleSeconds) {
        $issues.Add("data-age=$($state.data.age_seconds)s")
    }

    $listenerOwners = @(Get-ListenerOwners)
    if ($listenerOwners.Count -ne 1) {
        $issues.Add("listener-processes=$($listenerOwners.Count)")
    }
    else {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listenerOwners[0])"
        if ($null -eq $owner -or [string]$owner.CommandLine -notmatch "(?i)-m\s+autotrade\s+dashboard\s+--config\s+config\\paper\.toml") {
            $issues.Add("listener-owner-mismatch")
        }
    }

    $hasAutoResume = $state.controls.PSObject.Properties.Name -contains "auto_resume_allowed"
    if (
        $issues.Count -eq 0 -and
        $state.trading_state -ne "ACTIVE" -and
        $hasAutoResume -and
        $state.controls.auto_resume_allowed -eq $true
    ) {
        try {
            $state = Invoke-RestMethod `
                -Method Post `
                -Uri "$dashboardOrigin/api/control/resume" `
                -Headers @{ Origin = $dashboardOrigin } `
                -ContentType "application/json" `
                -Body "{}" `
                -TimeoutSec 10
            if ($state.trading_state -ne "ACTIVE" -or $state.strategy.armed -ne $true) {
                $issues.Add("safe-resume-did-not-activate")
            }
            else {
                $recovery = if ($recovery -eq "none") { "paper-resumed" } else { "$recovery+paper-resumed" }
                Write-HealthLog "INFO" "safe PAPER resume restored ACTIVE trading"
            }
        }
        catch {
            $issues.Add("safe-resume-failed=$($_.Exception.Message)")
        }
    }

    if ($state.tradingview.enabled -eq $true -and $state.tradingview.status -in @("UNAVAILABLE", "DISCONNECTED")) {
        Write-HealthLog "WARN" "TradingView $($state.tradingview.status); running one sidecar recovery"
        & (Join-Path $projectRoot "start_tradingview_debug.bat") | Out-Null
        Start-Sleep -Seconds 20
        try {
            $state = Get-DashboardState
            $recovery = if ($recovery -eq "none") { "tradingview-restarted" } else { "$recovery+tradingview-restarted" }
        }
        catch {
            $issues.Add("dashboard-unreachable-after-tradingview-recovery")
        }
    }

    if ($state.tradingview.enabled -eq $true) {
        $tv = $state.tradingview
        if ($tv.status -ne "CONNECTED") { $issues.Add("tradingview=$($tv.status)") }
        if ($tv.advisory_only -ne $true) { $issues.Add("tradingview-not-advisory") }
        if ($tv.execution_influence -ne "NONE") { $issues.Add("tradingview-influence=$($tv.execution_influence)") }
        if ([double]$tv.configured_weight -ne 0) { $issues.Add("tradingview-weight=$($tv.configured_weight)") }
        if ($null -eq $tv.freshness_ms -or [double]$tv.freshness_ms -gt $tradingViewStaleMilliseconds) {
            $issues.Add("tradingview-freshness=$($tv.freshness_ms)ms")
        }
        if ($tv.symbol -ne $tv.expected_symbol) { $issues.Add("tradingview-symbol=$($tv.symbol)") }
        if ($tv.timeframe -ne $tv.expected_timeframe) { $issues.Add("tradingview-timeframe=$($tv.timeframe)") }
    }

    $alerts = @($state.alerts) -join " | "
    if ($issues.Count -gt 0) {
        Write-HealthLog "ERROR" "unhealthy: $($issues -join ', '); trading=$($state.trading_state); alerts=$alerts; recovery=$recovery"
        return $false
    }
    if ($state.trading_state -ne "ACTIVE" -or $state.strategy.armed -ne $true) {
        Write-HealthLog "WARN" "service healthy but trading inactive: state=$($state.trading_state), armed=$($state.strategy.armed); alerts=$alerts; recovery=$recovery"
        return $true
    }

    Write-HealthLog "INFO" "healthy: mode=PAPER, engine=SIMULATION_READY, data=LIVE/$($state.data.age_seconds)s, trading=ACTIVE, strategy=$($state.strategy.status), listeners=1, tradingview=$($state.tradingview.status), recovery=$recovery"
    return $true
}

$mutex = $null
$ownsMutex = $false
if (-not $Once) {
    $mutex = New-Object System.Threading.Mutex($false, "Local\Autotrade7HealthWatchdog")
    try {
        $ownsMutex = $mutex.WaitOne(0, $false)
    }
    catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
    }
    if (-not $ownsMutex) { exit 0 }
}

try {
    do {
        $healthy = Invoke-HealthScan
        if ($Once) { exit $(if ($healthy) { 0 } else { 1 }) }
        Start-Sleep -Seconds $intervalSeconds
    } while ($true)
}
finally {
    if ($ownsMutex -and $null -ne $mutex) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
