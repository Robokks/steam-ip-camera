<#
  Start / stop recording an RTSP camera with VLC (no re-encoding).
  Designed to be called from LabVIEW's System Exec.vi.

  Start:  powershell -NoProfile -ExecutionPolicy Bypass -File vlc_record.ps1 start [-Url <rtsp>] [-OutDir <folder>] [-Minutes <n>]
  Stop:   powershell -NoProfile -ExecutionPolicy Bypass -File vlc_record.ps1 stop  [-OutDir <folder>]
  Status: powershell -NoProfile -ExecutionPolicy Bypass -File vlc_record.ps1 status [-OutDir <folder>]

  "start" prints the path of the new recording file (System Exec "standard output").
  -Minutes > 0 makes VLC stop by itself after that many minutes.
  Files are MPEG-TS (.ts): they stay playable even if recording is killed.
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("start", "stop", "status")]
    [string]$Action,
    [string]$Url = "rtsp://admin:admin@192.168.1.126:554/unicaststream/1",
    [string]$OutDir = "C:\CameraRecordings",
    [int]$Minutes = 0,
    [string]$Vlc = ""
)

$ErrorActionPreference = "Stop"
$pidFile = Join-Path $OutDir "vlc_recording.pid"

function Get-RecordingProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    $id = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $id) { return $null }
    $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -eq "vlc") { return $p }
    return $null
}

function Find-Vlc {
    if ($Vlc -and (Test-Path $Vlc)) { return $Vlc }
    $candidates = @(
        "$env:ProgramFiles\VideoLAN\VLC\vlc.exe",
        "${env:ProgramFiles(x86)}\VideoLAN\VLC\vlc.exe"
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    throw "vlc.exe not found. Install VLC or pass -Vlc 'C:\path\to\vlc.exe'."
}

try {
    switch ($Action) {
        "start" {
            if (Get-RecordingProcess) { throw "Already recording. Run 'stop' first." }
            New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
            $file = Join-Path $OutDir ("cam_{0}.ts" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
            # VLC's sout syntax wants single quotes around a path that may contain spaces.
            $sout = "#std{access=file,mux=ts,dst='$file'}"
            $vlcArgs = @("-I", "dummy", "--rtsp-tcp", "--network-caching=1000",
                         "`"$Url`"", "--sout", "`"$sout`"", "--no-sout-all", "--sout-keep")
            if ($Minutes -gt 0) { $vlcArgs += @("--run-time=$($Minutes * 60)", "vlc://quit") }
            $p = Start-Process -FilePath (Find-Vlc) -ArgumentList $vlcArgs -WindowStyle Hidden -PassThru
            Set-Content -Path $pidFile -Value $p.Id -Encoding ascii
            Start-Sleep -Seconds 3
            if ($p.HasExited) { throw "VLC exited immediately. Check the URL / password." }
            Write-Output $file
        }
        "stop" {
            $p = Get-RecordingProcess
            if (-not $p) { Write-Output "Not recording"; break }
            Stop-Process -Id $p.Id -Force
            Remove-Item $pidFile -ErrorAction SilentlyContinue
            Write-Output "Stopped"
        }
        "status" {
            if (Get-RecordingProcess) { Write-Output "Recording" } else { Write-Output "Not recording" }
        }
    }
    exit 0
}
catch {
    Write-Output "ERROR: $($_.Exception.Message)"
    exit 1
}
