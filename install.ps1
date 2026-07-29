<#
.SYNOPSIS
Richtet Agent Deck auf diesem Rechner ein - in einem Lauf und wiederholbar.

.DESCRIPTION
Alles, was in einer Anleitung als "hier deinen Pfad einsetzen" stand, macht dieses
Skript: Voraussetzungen pruefen, Pillow holen, die VS-Code-Extension kopieren, die
sechs Hooks und die statusLine in ~/.claude/settings.json mergen - und danach
BEWEISEN, dass ein Hook wirklich schreibt. Der Beweis ist der Punkt: die Falle vom
2026-07-29 (ein 'cmd /c' vor dem Hook) endete mit Exit 0 und sah darum gesund aus.
Erkennbar war sie nur daran, dass in state\ keine Datei mehr frisch wurde - genau
das prueft Schritt 5.

Der Lauf ist idempotent: fremde Hooks anderer Werkzeuge bleiben stehen, eigene
werden ersetzt statt verdoppelt (siehe deck/claude/hook_setup.py). Ein zweiter
Aufruf ist also ein Nulldurchgang und kein Risiko.

Bewusst ASCII-only - wie install_watchdog.ps1. Eine .ps1 mit Umlauten liest die
Windows-PowerShell 5.1 ohne BOM als ANSI, und dann steht Muell auf dem Schirm.

.PARAMETER Check
Nichts aendern, nur pruefen und berichten (der Doctor). Exit 1 bei einem Befund.

.PARAMETER Remove
Unsere Hook-Eintraege und die installierte Extension wieder entfernen.

.PARAMETER Force
Auch eine FREMDE statusLine ersetzen. Ohne das bleibt sie stehen (und Modell,
Effort sowie Kontext-% bleiben auf den Kacheln leer).

.PARAMETER NoStart
Das Panel am Ende nicht starten.

.PARAMETER SettingsPath
Gegen eine ANDERE settings.json laufen statt gegen ~/.claude/settings.json. Nur zum
Probelauf gedacht: so biegt ein Test die Hooks des pruefenden Rechners nicht um.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Check
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Remove
#>
param([switch]$Check, [switch]$Remove, [switch]$Force, [switch]$NoStart,
      [string]$SettingsPath)

$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
$ExtDst = Join-Path $env:USERPROFILE '.vscode\extensions\agent-deck-bridge'
$StateDir = Join-Path $env:LOCALAPPDATA 'claude-agent-deck\state'

$script:Fails = 0
$script:Warns = 0
function Ok   ($t) { Write-Host "  [ok]   $t" -ForegroundColor Green }
function Warn ($t) { Write-Host "  [warn] $t" -ForegroundColor Yellow; $script:Warns++ }
function Fail ($t) { Write-Host "  [FAIL] $t" -ForegroundColor Red;    $script:Fails++ }
function Step ($t) { Write-Host ''; Write-Host $t -ForegroundColor Cyan }
function Info ($t) { Write-Host "         $t" -ForegroundColor DarkGray }

# Ein Kommando still laufen lassen und nur sagen, ob es geklappt hat.
# Der Parameter heisst NICHT $args: das ist eine automatische PowerShell-Variable
# (die Argumente der Funktion selbst). Ein gleichnamiger Parameter wird ueberschrieben,
# das Kommando startet dann ohne Argumente - bei python heisst das: die REPL geht auf,
# und ihr Banner landet als "Ausgabe" in der Auswertung.
function Try-Run([string]$exe, [string[]]$argv) {
    try {
        $out = & $exe @argv 2>&1
        return @{ ok = ($LASTEXITCODE -eq 0); code = $LASTEXITCODE; out = ($out -join "`n") }
    } catch {
        return @{ ok = $false; code = -1; out = "$_" }
    }
}

Write-Host ''
Write-Host 'Agent Deck - Einrichtung' -ForegroundColor White
Write-Host "  Repo: $Here" -ForegroundColor DarkGray

# ── 1. Voraussetzungen ───────────────────────────────────────────────────────
Step '1/5  Voraussetzungen'

$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $py) {
    Fail 'python.exe nicht auf dem PATH. Installer von python.org (3.12+), "Add to PATH" anhaken.'
} else {
    # Ob die Version reicht, entscheidet Python selbst (Exit 9 = zu alt). Ein
    # [version]-Cast in PowerShell verliert sich sonst an Ausgaben wie "3.14.0rc1"
    # oder am REPL-Banner, falls der Aufruf ins Interaktive kippt.
    # Der Store-Stub heisst auch python.exe, oeffnet aber nur den Microsoft Store.
    $v = Try-Run $py @('-c',
        'import sys; print("%d.%d.%d" % sys.version_info[:3]); sys.exit(0 if sys.version_info >= (3,12) else 9)')
    $ver = ($v.out -split "`n")[0].Trim()
    if ($v.code -eq 9)      { Fail "Python $ver gefunden, gebraucht wird 3.12+." }
    elseif (-not $v.ok)     { Fail "python.exe startet nicht (Microsoft-Store-Platzhalter?): $($v.out)" }
    else {
        Ok "Python $ver ($py)"
        # tkinter fehlt bei der Store-Python - und ohne es gibt es kein Fenster.
        if ((Try-Run $py @('-c', 'import tkinter')).ok) { Ok 'tkinter vorhanden' }
        else { Fail 'tkinter fehlt. Store-Python? Den Installer von python.org nehmen.' }
    }
}

if ((Get-Command code -ErrorAction SilentlyContinue)) { Ok 'VS Code auf dem PATH' }
elseif (Test-Path (Join-Path $env:USERPROFILE '.vscode')) { Ok 'VS Code gefunden (~/.vscode)' }
else { Warn 'VS Code nicht gefunden - ohne es gibt es keine Terminals zu ueberwachen.' }

# claude.cmd (npm) ist der Weg; eine native claude.exe auf dem PATH macht Probleme.
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) { Ok "Claude Code CLI ($($claude.Source))" }
else { Warn 'claude nicht auf dem PATH - ohne die CLI gibt es nichts zu ueberwachen.' }

if (Test-Path (Join-Path $env:USERPROFILE '.claude\.credentials.json')) {
    Ok 'Claude Code ist angemeldet'
} else {
    Warn 'Keine .credentials.json - einmal "claude auth login" ausfuehren (sonst bleibt die Usage-Anzeige auf "-").'
}

# ── 2. Pillow ────────────────────────────────────────────────────────────────
Step '2/5  Abhaengigkeit (Pillow)'

if (-not $py) {
    Fail 'ohne Python nicht pruefbar'
} elseif ((Try-Run $py @('-c', 'import PIL')).ok) {
    Ok 'Pillow vorhanden'
} elseif ($Check -or $Remove) {
    Fail 'Pillow fehlt - install.ps1 ohne -Check holt es.'
} else {
    Info 'pip install -r requirements.txt'
    $r = Try-Run $py @('-m', 'pip', 'install', '-q', '-r', (Join-Path $Here 'requirements.txt'))
    if ($r.ok -and (Try-Run $py @('-c', 'import PIL')).ok) { Ok 'Pillow installiert' }
    else { Fail "pip fehlgeschlagen: $($r.out)" }
}

# ── 3. Extension ─────────────────────────────────────────────────────────────
Step '3/5  VS-Code-Extension'

$srcJs = Join-Path $Here 'extension\extension.js'
$dstJs = Join-Path $ExtDst 'extension.js'

if ($Remove) {
    if (Test-Path $ExtDst) { Remove-Item $ExtDst -Recurse -Force; Ok 'Extension entfernt' }
    else { Ok 'Extension war nicht installiert' }
} elseif ($Check) {
    if (-not (Test-Path $dstJs)) {
        Fail "Extension nicht installiert ($ExtDst)"
    } elseif ((Get-FileHash $srcJs).Hash -ne (Get-FileHash $dstJs).Hash) {
        # Genau dieses Fehlerbild stand schon zweimal hinter "verbindet nicht mehr".
        Fail 'Installierte Extension weicht vom Repo ab - install.ps1 neu laufen lassen, dann Reload Window.'
    } else {
        Ok 'Extension installiert und aktuell'
    }
} else {
    $vorher = if (Test-Path $dstJs) { (Get-FileHash $dstJs).Hash } else { '' }
    New-Item -ItemType Directory -Force -Path $ExtDst | Out-Null
    Copy-Item (Join-Path $Here 'extension\*') $ExtDst -Recurse -Force
    if ($vorher -eq (Get-FileHash $dstJs).Hash) { Ok 'Extension war schon aktuell' }
    else { Ok "Extension kopiert -> $ExtDst"; Info 'In JEDEM offenen VS-Code-Fenster: "Developer: Reload Window"' }
}

# ── 4. Hooks in ~/.claude/settings.json ──────────────────────────────────────
$SettingsShown = if ($SettingsPath) { $SettingsPath } else { '~/.claude/settings.json' }
Step "4/5  Hooks und statusLine in $SettingsShown"

if (-not $py) {
    Fail 'ohne Python nicht moeglich'
} else {
    Push-Location $Here      # damit `python -m deck...` das Paket findet
    try {
        $a = @('-m', 'deck.claude.hook_setup', '--porcelain')
        if ($Check)  { $a += '--check' }
        if ($Remove) { $a += '--remove' }
        if ($Force)  { $a += '--force' }
        if ($SettingsPath) { $a += @('--settings', $SettingsPath) }
        # Die Bilanzzeile aus der Anzeige nehmen und in die Gesamtzaehlung geben. Ohne
        # sie muesste hier der Meldungstext geparst werden - eine Kopplung, die beim
        # naechsten Umformulieren still bricht und dann falsche Zahlen zeigt.
        & $py @a | ForEach-Object {
            if ($_ -match '^## fails=(\d+) warns=(\d+)$') {
                $script:Fails += [int]$Matches[1]
                $script:Warns += [int]$Matches[2]
            } else { Write-Host $_ }
        }
    } finally { Pop-Location }
}

# ── 5. Der Beweis: schreibt ein Hook wirklich? ───────────────────────────────
Step '5/5  Beweis (Hook feuern und state\ pruefen)'

if ($Remove) {
    Ok 'uebersprungen (-Remove)'
} elseif (-not $py) {
    Fail 'ohne Python nicht moeglich'
} else {
    # Ein Slot-Name, den kein Fenster benutzt. Die Datei wird danach geloescht - sonst
    # liegt eine Phantom-Meldung in state\ und das Deck poll sie ewig mit.
    $slot = '__deck_doctor__'
    $probe = Join-Path $StateDir "$slot.json"
    Remove-Item $probe -Force -ErrorAction SilentlyContinue
    $alt = $env:AGENT_SLOT
    $env:AGENT_SLOT = $slot
    try {
        # Leeres JSON auf stdin: so ruft Claude Code den Hook auch auf.
        '{}' | & $py (Join-Path $Here 'report.py') 'idle' 2>&1 | Out-Null
        $code = $LASTEXITCODE
    } finally {
        $env:AGENT_SLOT = $alt
    }
    if (-not (Test-Path $probe)) {
        Fail "report.py hat nichts geschrieben (Exit $code). Erwartet: $probe"
        Info 'Mit start_debug.bat / "python report.py idle" von Hand nachsehen.'
    } else {
        $age = (Get-Date) - (Get-Item $probe).LastWriteTime
        if ($age.TotalSeconds -gt 30) { Fail "Datei in state\ ist $([int]$age.TotalSeconds)s alt - nicht von diesem Lauf." }
        else { Ok "report.py schreibt nach state\ (Exit $code)" }
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
    }
}

# Laeuft schon ein Panel? Der Broker-Port ist der funktionale Test - dort verbinden
# sich die Extensions. SO_REUSEADDR ist in broker.py bewusst AUS, ein zweiter
# Listener auf 8765 ist also nicht harmlos.
$tcp = New-Object Net.Sockets.TcpClient
try {
    $tcp.Connect('127.0.0.1', 8765)
    Info 'Ein Panel laeuft bereits (Broker auf 127.0.0.1:8765 antwortet).'
    $script:PanelLaeuft = $true
} catch { $script:PanelLaeuft = $false } finally { $tcp.Close() }

# ── Fazit ────────────────────────────────────────────────────────────────────
Write-Host ''
if ($script:Fails -gt 0) {
    Write-Host "Ergebnis: $($script:Fails) Problem(e), $($script:Warns) Hinweis(e)." -ForegroundColor Red
    Write-Host 'Oben stehen sie einzeln. Nach dem Beheben nochmal laufen lassen.' -ForegroundColor DarkGray
    exit 1
}
if ($Remove) { Write-Host 'Entfernt. Die Laufzeitdateien im Repo und in state\ sind geblieben.' -ForegroundColor White; exit 0 }
if ($Check)  { Write-Host "Alles in Ordnung ($($script:Warns) Hinweis(e))." -ForegroundColor Green; exit 0 }

Write-Host "Fertig ($($script:Warns) Hinweis(e))." -ForegroundColor Green
Write-Host ''
Write-Host 'Noch zwei Handgriffe, die niemand fuer dich machen kann:' -ForegroundColor White
Write-Host '  1. In jedem offenen VS-Code-Fenster: "Developer: Reload Window"'
Write-Host '  2. Im Panel oben auf "Fenster A" klicken, dann das VS-Code-Fenster anklicken'
Write-Host ''

if (-not $NoStart -and -not $script:PanelLaeuft) {
    Write-Host 'Panel starten...' -ForegroundColor DarkGray
    Start-Process -FilePath (Join-Path $Here 'start.bat') -WorkingDirectory $Here
}
exit 0
