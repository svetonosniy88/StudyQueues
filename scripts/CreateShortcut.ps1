param([string]$DataDir)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $appRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run Setup.cmd before creating a shortcut.'
}
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $appRoot 'StudyQueues.lnk'
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonPath
$shortcut.Arguments = '-m studyqueues'
if ($DataDir) {
    if ($DataDir.Contains('"')) { throw 'The data directory cannot contain a quote.' }
    $shortcut.Arguments += ' --data-dir "' + $DataDir + '"'
}
$shortcut.WorkingDirectory = $appRoot
$shortcut.IconLocation = (Join-Path $appRoot 'studyqueues\app.ico') + ',0'
$shortcut.Description = 'StudyQueues — учебные очереди'
$shortcut.Save()
Write-Output $shortcutPath
