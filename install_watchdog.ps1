<#
.SYNOPSIS
Registriert den Panel-Waechter (watchdog.py) in der Windows-Aufgabenplanung.

.DESCRIPTION
Der Waechter ist absichtlich kein Dauerprozess (der koennte am selben Problem
mitsterben), sondern ein kurzer Lauf im Takt. Die Aufgabenplanung ruft ihn auf:

  * bei der Anmeldung
  * danach alle 3 Minuten

Ein Lauf dauert Millisekunden, wenn das Panel lebt. Fehlt es, startet er es neu –
ausser es hat sich sauber selbst beendet (dann hat der Nutzer es geschlossen und
der Waechter laesst es in Ruhe, siehe watchdog.py).

Die Aufgabe laeuft im eigenen Benutzerkonto und braucht KEINE Admin-Rechte; sie
laeuft nur bei angemeldetem Nutzer (ein Fenster braucht eine Sitzung).

Alternative OHNE Aufgabenplanung: -Autostart legt eine Verknuepfung im Autostart-
Ordner an, die den Dauerwaechter (watchdog.py --loop) bei der Anmeldung startet.
Bewusst eine .lnk und keine .cmd: in einer .cmd haengt ein Pfad mit Umlauten an der
Codepage und wird dann nicht gefunden - eine Verknuepfung speichert ihn binaer.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install_watchdog.ps1
  powershell -ExecutionPolicy Bypass -File .\install_watchdog.ps1 -Autostart
  powershell -ExecutionPolicy Bypass -File .\install_watchdog.ps1 -Remove
#>
param([switch]$Remove, [switch]$Autostart)

$ErrorActionPreference = 'Stop'
$TaskName = 'Agent Deck Waechter'
$LinkPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Agent Deck Waechter.lnk'

if ($Autostart) {
    $bat = Join-Path $PSScriptRoot 'start_watchdog.bat'
    if (-not (Test-Path $bat)) { throw "start_watchdog.bat nicht gefunden in $PSScriptRoot" }
    if ($Remove) {
        if (Test-Path $LinkPath) { Remove-Item $LinkPath -Force }
        "Autostart-Verknuepfung entfernt."
        return
    }
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($LinkPath)
    $lnk.TargetPath = $bat
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.Description = 'Startet den Agent-Deck-Waechter (haelt das Panel am Leben).'
    $lnk.Save()
    "Autostart-Verknuepfung angelegt: $LinkPath"
    "  Ziel: $bat"
    "Jetzt sofort starten:  .\start_watchdog.bat"
    "Entfernen mit:         .\install_watchdog.ps1 -Autostart -Remove"
    return
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    "Aufgabe '$TaskName' entfernt."
    return
}

$here = $PSScriptRoot
$script = Join-Path $here 'watchdog.py'
if (-not (Test-Path $script)) { throw "watchdog.py nicht gefunden in $here" }

# pythonw = ohne Konsolenfenster. Sonst blitzt im Takt eine schwarze Box auf.
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $py = (Get-Command python.exe).Source
    $pythonw = Join-Path (Split-Path $py) 'pythonw.exe'
}
if (-not (Test-Path $pythonw)) { throw "pythonw.exe nicht gefunden" }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $here

# Zwei Trigger: einmal bei der Anmeldung, und ein wiederholender ab jetzt.
$atLogon = New-ScheduledTaskTrigger -AtLogOn
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 3) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($atLogon, $repeat) -Settings $settings `
    -Description 'Startet das Agent-Deck-Panel neu, wenn es ungewollt verschwunden ist.' `
    -Force | Out-Null

"Aufgabe '$TaskName' registriert:"
"  Programm : $pythonw"
"  Skript   : $script"
"  Takt     : bei Anmeldung + alle 3 Minuten"
"Entfernen mit:  .\install_watchdog.ps1 -Remove"
