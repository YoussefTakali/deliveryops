'use strict';
// Automation steps that interact with other applications and websites (see the step catalog in
// backend/app/automations/catalog.py: desktop.*, web.browse):
//   - Windows desktop apps through UI Automation (the accessibility tree): elements are found by the
//     name/label and control type the user SEES, never by screen coordinates, so a step keeps working
//     when windows move or the layout shifts. Invoke/Value/Selection patterns first, a real mouse click
//     at the element's clickable point only as a fallback.
//   - Websites through a dedicated Electron browser window with its own persistent session
//     (partition persist:dm-automation) — the user logs in once via openBrowser(), later runs reuse it.
//   - Windows notification centre (best effort, read through UI Automation) and screenshots.
// Every PowerShell script here is static; step parameters travel as DM_* environment variables so
// user-provided text can never be interpreted as code.

const path = require('path');
const { runPowerShell } = require('./automation-local');

const PS_PRELUDE = `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class DmW {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
# True physical pixels everywhere: the screenshot (CopyFromScreen), UI Automation BoundingRectangle
# and SetCursorPos must share ONE coordinate space, or clicks land off by the display scale factor
# (the classic "clicks the wrong place" on a 125/150% or multi-monitor Windows setup). Per-Monitor-V2
# is the strongest; fall back gracefully on older Windows. Must run before any capture/UIA call.
try { [void][DmW]::SetProcessDpiAwarenessContext([IntPtr](-4)) }        # PER_MONITOR_AWARE_V2 (Win10 1703+)
catch { try { [void][DmW]::SetProcessDpiAwareness(2) }                  # PER_MONITOR (Win8.1+)
        catch { try { [void][DmW]::SetProcessDPIAware() } catch {} } }  # system-aware (legacy)
$A = [System.Windows.Automation.AutomationElement]
$T = [System.Windows.Automation.TreeScope]
$True_ = [System.Windows.Automation.Condition]::TrueCondition
function Norm([string]$s) { if (-not $s) { return '' }; return (($s.Normalize([Text.NormalizationForm]::FormD) -replace '\\p{Mn}', '') -replace '\\s+', ' ').ToLower().Trim() }
function CType($e) { return ($e.Current.ControlType.ProgrammaticName -replace '^ControlType\\.', '') }
function Describe($e) { return @{ name = [string]$e.Current.Name; control_type = (CType $e); automation_id = [string]$e.Current.AutomationId } }
function Emit($o) { [Console]::Out.WriteLine(($o | ConvertTo-Json -Compress -Depth 6)) }
function Find-Window([string]$title, [int]$timeoutS) {
  $want = Norm $title
  $deadline = (Get-Date).AddSeconds([Math]::Max(0, $timeoutS))
  do {
    foreach ($w in $A::RootElement.FindAll($T::Children, $True_)) {
      if ((Norm $w.Current.Name).Contains($want)) { return $w }
    }
    if ($timeoutS -le 0) { break }
    Start-Sleep -Milliseconds 400
  } while ((Get-Date) -lt $deadline)
  return $null
}
function Focus-Window($w) {
  $h = [IntPtr]$w.Current.NativeWindowHandle
  if ($h -ne [IntPtr]::Zero) {
    if ([DmW]::IsIconic($h)) { [void][DmW]::ShowWindow($h, 9) }
    # A background process may not steal focus; a synthetic Alt tap satisfies Windows' foreground rules.
    # Retry a couple of times: the first SetForegroundWindow after a window opens often loses a race
    # with the app still painting, which is a common cause of clicks landing on the wrong window.
    for ($i = 0; $i -lt 3; $i++) {
      [DmW]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [DmW]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
      if ([DmW]::SetForegroundWindow($h)) { break }
      Start-Sleep -Milliseconds 120
    }
  }
  try { $w.SetFocus() } catch {}
  Start-Sleep -Milliseconds 180
}
function Need-Window([string]$title, [int]$timeoutS) {
  $w = Find-Window $title $timeoutS
  if (-not $w) { throw "Fenêtre introuvable : aucune fenêtre dont le titre contient « $title »" }
  return $w
}
function Is-Interactive($e) {
  foreach ($p in @([System.Windows.Automation.InvokePattern]::Pattern, [System.Windows.Automation.SelectionItemPattern]::Pattern, [System.Windows.Automation.TogglePattern]::Pattern, [System.Windows.Automation.ExpandCollapsePattern]::Pattern, [System.Windows.Automation.ValuePattern]::Pattern)) {
    $o = $null
    if ($e.TryGetCurrentPattern($p, [ref]$o)) { return $true }
  }
  return $false
}
# Rank a candidate against the wanted (normalised) label. Higher = better. Returns -1 for no match at
# all. This is what stops the automation clicking the wrong element: instead of taking the first node
# whose name merely *contains* the text, we prefer an exact label, then a prefix, then a substring,
# and bias toward what is actually on screen, enabled and clickable.
function Score-Element($e, [string]$want, [bool]$preferInteractive) {
  $en = Norm $e.Current.Name
  if (-not $en) { return -1 }
  $s = -1
  # Accelerator ampersands ("&Enregistrer") and trailing ellipses/shortcuts ("Ouvrir...	Ctrl+O") never
  # count against a match.
  $en = (($en -replace '&', '') -replace '(\.\.\.|…)\s*$', '') -replace '\t.*$', ''
  if ($en -eq $want) { $s = 100 }
  elseif ($en.StartsWith($want + ' ') -or $en.StartsWith($want)) { $s = 78 - [Math]::Min(18, [int](($en.Length - $want.Length) / 3)) }
  elseif ($en -match ('(^|\s)' + [regex]::Escape($want) + '(\s|$)')) { $s = 62 - [Math]::Min(22, [int](($en.Length - $want.Length) / 3)) }  # whole-word inside
  elseif ($en.Contains($want) -and $want.Length -ge 4) { $s = 40 - [Math]::Min(25, [int](($en.Length - $want.Length) / 3)) }
  # The reverse case (the visible label is a *part* of what was asked, e.g. "Enregistrer" for
  # "Enregistrer le document") only counts when the label covers most of the request — a 2-3 letter
  # label like "OK" or "le" matching by accident is exactly how random elements got clicked.
  elseif ($want.Contains($en) -and $en.Length -ge 4 -and $en.Length -ge [int]($want.Length * 0.6)) { $s = 34 }
  if ($s -lt 0) { return -1 }
  if (-not $e.Current.IsOffscreen) { $s += 22 }
  try { if ($e.Current.IsEnabled) { $s += 8 } } catch {}
  if ($preferInteractive -and (Is-Interactive $e)) { $s += 14 }
  return $s
}
function Find-Element($root, [string]$name, [string]$autoId, [string]$ctype, [int]$index, [int]$timeoutS, [bool]$forClick = $false) {
  $conds = @()
  if ($autoId) { $conds += New-Object System.Windows.Automation.PropertyCondition($A::AutomationIdProperty, $autoId) }
  if ($ctype) {
    $f = [System.Windows.Automation.ControlType].GetField($ctype)
    if ($f) { $conds += New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, $f.GetValue($null)) }
  }
  $base = if ($conds.Count -gt 1) { New-Object System.Windows.Automation.AndCondition($conds) } elseif ($conds.Count -eq 1) { $conds[0] } else { $True_ }
  $want = Norm $name
  $deadline = (Get-Date).AddSeconds([Math]::Max(0, $timeoutS))
  do {
    if ($name) {
      # Rank every candidate under the base condition and keep the best; $index selects among equally
      # ranked matches (rarely needed once ranking is in play).
      $ranked = @($root.FindAll($T::Descendants, $base) | ForEach-Object {
        $sc = Score-Element $_ $want $forClick
        if ($sc -ge 0) { [pscustomobject]@{ el = $_; score = $sc; len = (Norm $_.Current.Name).Length } }
      } | Sort-Object -Property @{Expression = 'score'; Descending = $true}, @{Expression = 'len'; Descending = $false})
      if ($ranked.Count -gt $index) { return $ranked[$index].el }
    } else {
      $hits = @($root.FindAll($T::Descendants, $base))
      $onscreen = @($hits | Where-Object { -not $_.Current.IsOffscreen })
      if ($onscreen.Count) { $hits = $onscreen }
      if ($hits.Count -gt $index) { return $hits[$index] }
    }
    if ($timeoutS -le 0) { break }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  return $null
}
function Mouse-Click($e, [bool]$double) {
  $pt = $null
  try { $pt = $e.GetClickablePoint() } catch {
    $r = $e.Current.BoundingRectangle
    if ($r.Width -le 0) { throw "Élément non cliquable (hors écran)" }
    $pt = New-Object System.Windows.Point (($r.Left + $r.Width / 2), ($r.Top + $r.Height / 2))
  }
  [void][DmW]::SetCursorPos([int]$pt.X, [int]$pt.Y)
  $n = if ($double) { 2 } else { 1 }
  for ($i = 0; $i -lt $n; $i++) { [DmW]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero); [DmW]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 60 }
}
function Escape-Keys([string]$s) { return ($s -replace '([+^%~(){}\\[\\]])', '{$1}') -replace "\`r?\`n", '{ENTER}' }
`;

const PS_OPEN_APP = `
$app = $env:DM_APP; $argsLine = $env:DM_ARGS
if (Test-Path -LiteralPath $app) {
  if ($argsLine) { Start-Process -FilePath $app -ArgumentList $argsLine } else { Start-Process -FilePath $app }
} elseif ($app -match '\\.exe$' -or (Get-Command $app -CommandType Application -ErrorAction SilentlyContinue)) {
  if ($argsLine) { Start-Process -FilePath $app -ArgumentList $argsLine } else { Start-Process -FilePath $app }
} else {
  $want = Norm $app
  $apps = @(Get-StartApps)
  $sa = $apps | Where-Object { (Norm $_.Name) -eq $want } | Select-Object -First 1
  if (-not $sa) { $sa = $apps | Where-Object { (Norm $_.Name).Contains($want) } | Select-Object -First 1 }
  if (-not $sa) { throw "Application introuvable dans le menu Démarrer : $app" }
  Start-Process ("shell:AppsFolder\\" + $sa.AppID)
}
$win = $null
if ($env:DM_WAIT) { $w = Need-Window $env:DM_WAIT ([int]$env:DM_TIMEOUT); Focus-Window $w; $win = [string]$w.Current.Name }
Emit @{ opened = $true; window = $win }`;

const PS_WINDOW = `
$action = $env:DM_ACTION; $title = $env:DM_TITLE; $timeout = [int]$env:DM_TIMEOUT
if ($action -eq 'wait_close') {
  $deadline = (Get-Date).AddSeconds($timeout)
  while ((Find-Window $title 0) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
  $still = Find-Window $title 0
  if ($still) { throw "La fenêtre « $title » est toujours ouverte après $timeout s" }
  Emit @{ found = $false; window = $null }; exit 0
}
$w = Need-Window $title $(if ($action -eq 'wait') { $timeout } else { [Math]::Min($timeout, 5) })
$h = [IntPtr]$w.Current.NativeWindowHandle
switch ($action) {
  'close' { try { $w.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close() } catch { [System.Windows.Forms.SendKeys]::SendWait('%{F4}') } }
  'maximize' { [void][DmW]::ShowWindow($h, 3); Focus-Window $w }
  'minimize' { [void][DmW]::ShowWindow($h, 6) }
  default { Focus-Window $w }
}
Emit @{ found = $true; window = [string]$w.Current.Name }`;

const PS_CLICK = `
$w = Need-Window $env:DM_WINDOW 5
Focus-Window $w
$e = Find-Element $w $env:DM_NAME $env:DM_AUTOID $env:DM_CTYPE ([int]$env:DM_INDEX) ([int]$env:DM_TIMEOUT) $true
if (-not $e) { throw ("Élément introuvable dans « " + $w.Current.Name + " » : " + (@($env:DM_NAME, $env:DM_AUTOID, $env:DM_CTYPE) | Where-Object { $_ }) -join ' / ') }
$double = $env:DM_DOUBLE -eq '1'
$done = $false
if (-not $double) {
  foreach ($pat in @([System.Windows.Automation.InvokePattern]::Pattern, [System.Windows.Automation.SelectionItemPattern]::Pattern, [System.Windows.Automation.TogglePattern]::Pattern, [System.Windows.Automation.ExpandCollapsePattern]::Pattern)) {
    $p = $null
    if ($e.TryGetCurrentPattern($pat, [ref]$p)) {
      try {
        switch ($pat.ProgrammaticName) {
          'InvokePatternIdentifiers.Pattern' { $p.Invoke() }
          'SelectionItemPatternIdentifiers.Pattern' { $p.Select() }
          'TogglePatternIdentifiers.Pattern' { $p.Toggle() }
          'ExpandCollapsePatternIdentifiers.Pattern' { if ($p.Current.ExpandCollapseState -eq 'Collapsed') { $p.Expand() } else { $p.Collapse() } }
        }
        $done = $true; break
      } catch {}
    }
  }
}
if (-not $done) { Mouse-Click $e $double }
Emit @{ clicked = $true; element = (Describe $e) }`;

const PS_TYPE = `
$w = Need-Window $env:DM_WINDOW 5
Focus-Window $w
$e = $null
if ($env:DM_NAME -or $env:DM_AUTOID) {
  # forClick=$true biases toward an actually-editable control (ValuePattern) over a same-named label.
  $e = Find-Element $w $env:DM_NAME $env:DM_AUTOID '' 0 10 $true
  if (-not $e) { throw ("Champ introuvable : " + (@($env:DM_NAME, $env:DM_AUTOID) | Where-Object { $_ }) -join ' / ') }
} else { $e = $A::FocusedElement }
$text = $env:DM_TEXT
$set = $false
$vp = $null
if ($e -and $e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp) -and -not $vp.Current.IsReadOnly) {
  try {
    $e.SetFocus()
    $vp.SetValue($(if ($env:DM_CLEAR -eq '1') { $text } else { $vp.Current.Value + $text }))
    $set = $true
  } catch {}
}
if (-not $set) {
  if ($e) { try { $e.SetFocus() } catch {} }
  if ($env:DM_CLEAR -eq '1') { [System.Windows.Forms.SendKeys]::SendWait('^a{DEL}') }
  [System.Windows.Forms.SendKeys]::SendWait((Escape-Keys $text))
}
if ($env:DM_SUBMIT -eq '1') { Start-Sleep -Milliseconds 100; [System.Windows.Forms.SendKeys]::SendWait('{ENTER}') }
Emit @{ typed = $true; element = $(if ($e) { Describe $e } else { $null }) }`;

const PS_KEYS = `
if ($env:DM_WINDOW) { Focus-Window (Need-Window $env:DM_WINDOW 5) }
[System.Windows.Forms.SendKeys]::SendWait($env:DM_KEYS)
Emit @{ sent = $true }`;

const PS_READ = `
$w = Need-Window $env:DM_WINDOW 5
$lines = New-Object System.Collections.Generic.List[string]
function Text-Of($e) {
  $vp = $null; $tp = $null
  if ($e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp) -and $vp.Current.Value) { return [string]$vp.Current.Value }
  if ($e.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$tp)) { return $tp.DocumentRange.GetText(20000) }
  return [string]$e.Current.Name
}
if ($env:DM_NAME -or $env:DM_AUTOID -or $env:DM_CTYPE) {
  $e = Find-Element $w $env:DM_NAME $env:DM_AUTOID $env:DM_CTYPE 0 10
  if (-not $e) { throw "Élément introuvable" }
  $txt = Text-Of $e
  foreach ($l in ($txt -split "\`r?\`n")) { if ($l.Trim()) { $lines.Add($l.Trim()) } }
} else {
  $keep = @('Text', 'Edit', 'Document', 'DataItem', 'ListItem', 'TreeItem', 'HeaderItem', 'Hyperlink', 'TitleBar', 'Header')
  $n = 0; $prev = ''
  foreach ($e in $w.FindAll($T::Descendants, $True_)) {
    if ($n -ge 2500) { break }; $n++
    if ($e.Current.IsOffscreen -or $e.Current.IsPassword) { continue }
    if ($keep -notcontains (CType $e)) { continue }
    $t = (Text-Of $e)
    if (-not $t) { continue }
    foreach ($l in ($t -split "\`r?\`n")) { $l = $l.Trim(); if ($l -and $l -ne $prev) { $lines.Add($l); $prev = $l } }
    if ($lines.Count -ge 1500) { break }
  }
}
$all = ($lines -join "\`n")
if ($all.Length -gt 20000) { $all = $all.Substring(0, 20000) }
Emit @{ text = $all; lines = @($lines | Select-Object -First 1500) }`;

// Windows 11: Win+N opens the notification centre, Windows 10: Win+A (Action Center). Its window is a
// CoreWindow whose name contains "notif" in both English and French ("Notification Center" /
// "Centre de notifications"). Each notification is a ListItem whose name carries the full text.
const PS_NOTIFICATIONS = `
$build = [Environment]::OSVersion.Version.Build
$key = if ($build -ge 22000) { 0x4E } else { 0x41 }
[DmW]::keybd_event(0x5B, 0, 0, [UIntPtr]::Zero); [DmW]::keybd_event($key, 0, 0, [UIntPtr]::Zero)
[DmW]::keybd_event($key, 0, 2, [UIntPtr]::Zero); [DmW]::keybd_event(0x5B, 0, 2, [UIntPtr]::Zero)
$panel = $null
$deadline = (Get-Date).AddSeconds(4)
while (-not $panel -and (Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 300
  # Windows 11 doesn't list the panel among top-level windows, but it takes keyboard focus: walk up
  # from the focused element. Older builds expose it as a top-level CoreWindow.
  try {
    $p = $A::FocusedElement
    for ($i = 0; $p -and $i -lt 12; $i++) {
      if ($p.Current.ClassName -like '*CoreWindow*' -and (Norm $p.Current.Name).Contains('notif')) { $panel = $p; break }
      $p = [System.Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($p)
    }
  } catch {}
  if (-not $panel) {
    foreach ($w in $A::RootElement.FindAll($T::Children, $True_)) {
      if ($w.Current.ClassName -like '*CoreWindow*' -and (Norm $w.Current.Name).Contains('notif')) { $panel = $w; break }
    }
  }
}
$items = @()
if ($panel) {
  Start-Sleep -Milliseconds 500
  $group = ''
  foreach ($e in $panel.FindAll($T::Descendants, $True_)) {
    $ct = CType $e; $name = [string]$e.Current.Name
    if (-not $name) { continue }
    if ($ct -eq 'Group' -or $ct -eq 'Header') { $group = ($name -replace "^(Notifications from|Notifications de|Notifications d')", '').Trim(); continue }
    if ($ct -eq 'ListItem') { $items += @{ app = $group; text = $name } }
  }
}
[System.Windows.Forms.SendKeys]::SendWait('{ESC}')
if (-not $panel) { throw "Centre de notifications introuvable (utilisez plutôt une capture d'écran + analyse IA)" }
$wantApp = Norm $env:DM_APP; $wantText = Norm $env:DM_TEXT
$items = @($items | Where-Object { (-not $wantApp -or (Norm ($_.app + ' ' + $_.text)).Contains($wantApp)) -and (-not $wantText -or (Norm $_.text).Contains($wantText)) })
Emit @{ notifications = $items; count = $items.Count; latest = $(if ($items.Count) { $items[0] } else { $null }) }`;

// The interactive controls of a window, as the accessibility tree exposes them. This is what the
// AI locator resolver (backend /api/automations/resolve-element) chooses from when a compiled
// step's guessed label doesn't exist in the real UI — instead of clicking the nearest lookalike.
const PS_DUMP = `
$w = Need-Window $env:DM_WINDOW 5
$max = [int]$env:DM_MAX; if ($max -le 0) { $max = 300 }
$keep = @('Button','MenuItem','Edit','ComboBox','CheckBox','RadioButton','TabItem','ListItem','TreeItem','Hyperlink','SplitButton','Document','DataItem','MenuBar','Menu','Text','Group','ToolBar','Custom','Image')
$out = New-Object System.Collections.Generic.List[object]
$n = 0
foreach ($e in $w.FindAll($T::Descendants, $True_)) {
  if ($n -ge 4000) { break }; $n++
  try {
    if ($e.Current.IsOffscreen) { continue }
    $ct = CType $e; if ($keep -notcontains $ct) { continue }
    $name = [string]$e.Current.Name; $aid = [string]$e.Current.AutomationId
    if (-not $name -and -not $aid) { continue }
    # Text/Group/Image/Custom nodes are only useful when they are actually actionable
    if (@('Text','Group','Custom','Image','ToolBar','MenuBar','Menu') -contains $ct -and -not (Is-Interactive $e)) { continue }
    $r = $e.Current.BoundingRectangle
    $out.Add(@{ name = $name; control_type = $ct; automation_id = $aid; enabled = [bool]$e.Current.IsEnabled; x = [int]$r.Left; y = [int]$r.Top; w = [int]$r.Width; h = [int]$r.Height })
    if ($out.Count -ge $max) { break }
  } catch {}
}
Emit @{ window = [string]$w.Current.Name; elements = $out.ToArray(); count = $out.Count }`;

// Microsoft Teams live captions, read from Teams' own UI (the "Live captions" pane). Teams renders
// each caption as an author element followed by the text, so we get the exact speaker name from the
// meeting itself — far more reliable than separating voices in a mixed audio stream. The user must
// have turned captions on in the meeting (More > Language and speech > Turn on live captions).
const PS_TEAMS_CAPTIONS = `
$want = Norm $env:DM_WINDOW
$win = $null
foreach ($x in $A::RootElement.FindAll($T::Children, $True_)) {
  $n = Norm $x.Current.Name
  if (($want -and $n.Contains($want)) -or (-not $want -and ($n.Contains('teams') -or $n.Contains('réunion') -or $n.Contains('meeting')) -and -not $n.Contains('deliveryops'))) { $win = $x; break }
}
if (-not $win) { Emit @{ available = $false; window = $null; captions = @() }; exit 0 }
# Find the captions container: any node whose name/automation id mentions captions (EN/FR), then read
# its text nodes in document order. Fallback: all text nodes whose parent group carries an author name.
$container = $null
$cond = $True_
foreach ($e in $win.FindAll($T::Descendants, $True_)) {
  $nm = Norm ([string]$e.Current.Name + ' ' + [string]$e.Current.AutomationId + ' ' + [string]$e.Current.ClassName)
  if ($nm -match 'caption|sous-titre|soustitre|subtitle') { $container = $e; break }
}
$items = New-Object System.Collections.Generic.List[object]
$root = if ($container) { $container } else { $win }
$prevName = ''
$lastAuthor = ''
foreach ($e in $root.FindAll($T::Descendants, $True_)) {
  if ($items.Count -ge 60) { break }
  $ct = CType $e
  if ($ct -ne 'Text' -and $ct -ne 'ListItem' -and $ct -ne 'Group') { continue }
  $t = ([string]$e.Current.Name).Trim()
  if (-not $t -or $t -eq $prevName) { continue }
  $prevName = $t
  # "Name: text", "Name said text", or an author-only node followed by a text node
  if ($t -match '^([^:]{2,60}):\s+(.+)$') { $items.Add(@{ speaker = $Matches[1].Trim(); text = $Matches[2].Trim() }); $lastAuthor = ''; continue }
  if ($t.Length -le 40 -and $t -notmatch '[.?!,]' -and ($t -split '\s+').Count -le 4) { $lastAuthor = $t; continue }
  if ($lastAuthor) { $items.Add(@{ speaker = $lastAuthor; text = $t }); $lastAuthor = '' }
}
Emit @{ available = $true; window = [string]$win.Current.Name; captions = $items.ToArray() }`;

async function ps(script, env, timeoutMs) {
  const res = await runPowerShell(PS_PRELUDE + script, { env, timeoutMs });
  const line = (res.stdout || '').split(/\r?\n/).map((l) => l.trim()).filter((l) => l.startsWith('{')).pop();
  if (res.exit_code !== 0 || !line) {
    const err = (res.stderr || '').split(/\r?\n/).map((l) => l.trim()).find((l) => l && !l.startsWith('At line') && !l.startsWith('+')) || 'échec de l’action';
    throw new Error(err.replace(/^[^:]*Exception[^:]*:\s*/, '').slice(0, 400));
  }
  return JSON.parse(line);
}

// Interacting with a foreign UI is inherently racy: a window is still painting, an element hasn't
// been created yet, focus was briefly stolen, an accessibility tree node went stale mid-call. These
// fail fast and succeed on a second look, so a couple of quick retries turn "the automation stopped
// for no reason" into a step that just works. Each executor here emits its result ONLY on success,
// so retrying never repeats a side effect (a click/keystroke that already happened). `tries` is the
// TOTAL number of attempts. Genuinely absent targets still fail after the last attempt.
async function withRetry(fn, tries = 3, delayMs = 500) {
  let lastErr;
  for (let attempt = 1; attempt <= tries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (attempt < tries) await new Promise((r) => setTimeout(r, delayMs * attempt));
    }
  }
  throw lastErr;
}

const str = (v) => (v === undefined || v === null ? '' : String(v));
const secs = (v, d, max) => Math.min(Math.max(Number(v) || d, 0), max);

// ------------------------------------------------------------------ web page helper (runs in page)
const PAGE_HELPER = `(() => {
  if (window.__dm) return true;
  const norm = (s) => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const visible = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el); return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const label = (el) => norm(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '');
  function byText(t) {
    const want = norm(t);
    const enabled = (e) => !e.disabled && e.getAttribute('aria-disabled') !== 'true' && getComputedStyle(e).pointerEvents !== 'none';
    const cands = [...document.querySelectorAll('a,button,[role=button],[role=link],[role=menuitem],[role=tab],[role=option],input[type=submit],input[type=button],summary,label,li,td,th,span,div,p,h1,h2,h3,h4')].filter((e) => visible(e) && enabled(e));
    // Several nested elements often share the same text (<p><button>OK</button></p>): prefer what is
    // actually clickable, then the shortest text (avoids grabbing a whole container that merely
    // contains the label), then the deepest (most specific) element.
    const interactive = (e) => e.matches('a[href],button,input,select,textarea,summary,label,[role=button],[role=link],[role=menuitem],[role=tab],[role=option],[onclick]') || getComputedStyle(e).cursor === 'pointer';
    const depth = (e) => { let d = 0; for (let p = e; p; p = p.parentElement) d++; return d; };
    const size = (e) => (e.innerText || e.value || '').length;
    const rank = (a, b) => (interactive(b) - interactive(a)) || (size(a) - size(b)) || (depth(b) - depth(a));
    const exact = cands.filter((e) => label(e) === want).sort(rank);
    if (exact.length) return exact[0];
    const prefix = cands.filter((e) => label(e).startsWith(want)).sort(rank);
    if (prefix.length) return prefix[0];
    return cands.filter((e) => label(e).includes(want)).sort(rank)[0] || null;
  }
  function byLabel(t) {
    const want = norm(t);
    for (const l of document.querySelectorAll('label')) {
      if (norm(l.innerText).includes(want)) { if (l.control) return l.control; const i = l.querySelector('input,textarea,select'); if (i) return i; }
    }
    for (const e of document.querySelectorAll('input,textarea,select,[contenteditable=true],[role=textbox],[role=combobox]')) {
      const n = norm(e.getAttribute('aria-label') || e.placeholder || e.name || e.title || e.id);
      if (n && n.includes(want)) return e;
    }
    return null;
  }
  const find = (sel) => sel.startsWith('text=') ? byText(sel.slice(5)) : sel.startsWith('label=') ? byLabel(sel.slice(6)) : document.querySelector(sel);
  const findAll = (sel) => (sel.startsWith('text=') || sel.startsWith('label=')) ? [find(sel)].filter(Boolean) : [...document.querySelectorAll(sel)];
  function setValue(el, v) {
    el.focus();
    if (el.isContentEditable) { el.textContent = v; }
    else {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      const d = Object.getOwnPropertyDescriptor(proto, 'value');
      if (d && d.set) d.set.call(el, v); else el.value = v;
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function table(el) {
    const t = el.tagName === 'TABLE' ? el : el.querySelector('table');
    if (!t) return [];
    const rows = [...t.rows];
    if (!rows.length) return [];
    const head = [...rows[0].cells].map((c, i) => c.innerText.trim() || 'col' + (i + 1));
    return rows.slice(1).map((r) => Object.fromEntries([...r.cells].map((c, i) => [head[i] || 'col' + (i + 1), c.innerText.trim()])));
  }
  window.__dm = { find, findAll, setValue, table, text: (el) => (el.value !== undefined && el.tagName !== 'BUTTON' ? el.value : el.innerText || '').trim() };
  return true;
})()`;

const KEY_CODES = { enter: 'Enter', tab: 'Tab', escape: 'Escape', esc: 'Escape', backspace: 'Backspace', delete: 'Delete', up: 'Up', down: 'Down', left: 'Left', right: 'Right', space: 'Space', pageup: 'PageUp', pagedown: 'PageDown', home: 'Home', end: 'End' };

function createInteractSteps({ BrowserWindow, desktopCapturer, screen, app, session }) {
  // ---- desktop apps ----
  async function openApp({ app: target, args, wait_window, timeout_s } = {}) {
    if (!str(target).trim()) throw new Error('application requise');
    const t = secs(timeout_s, 30, 180);
    return ps(PS_OPEN_APP, { DM_APP: str(target).trim(), DM_ARGS: str(args), DM_WAIT: str(wait_window), DM_TIMEOUT: String(t) }, (t + 20) * 1000);
  }

  async function windowAction({ title_contains, action = 'focus', timeout_s } = {}) {
    if (!str(title_contains).trim()) throw new Error('titre de fenêtre requis');
    const allowed = ['focus', 'wait', 'wait_close', 'close', 'maximize', 'minimize'];
    const act = allowed.includes(action) ? action : 'focus';
    const t = secs(timeout_s, 30, 600);
    // 'wait'/'wait_close' already poll for the full timeout — retrying them would only double the wait.
    const run = () => ps(PS_WINDOW, { DM_TITLE: str(title_contains), DM_ACTION: act, DM_TIMEOUT: String(t) }, (t + 20) * 1000);
    return act === 'wait' || act === 'wait_close' ? run() : withRetry(run);
  }

  async function click({ window, name, automation_id, control_type, index, double, timeout_s } = {}) {
    if (!str(window).trim()) throw new Error('fenêtre requise');
    if (!str(name).trim() && !str(automation_id).trim()) throw new Error('libellé (name) ou automation_id requis');
    const t = secs(timeout_s, 15, 120);
    return withRetry(() => ps(PS_CLICK, {
      DM_WINDOW: str(window), DM_NAME: str(name), DM_AUTOID: str(automation_id), DM_CTYPE: str(control_type).replace(/[^A-Za-z]/g, ''),
      DM_INDEX: String(Math.max(0, Number(index) || 0)), DM_DOUBLE: double ? '1' : '0', DM_TIMEOUT: String(t),
    }, (t + 30) * 1000));
  }

  async function type({ window, text, name, automation_id, clear = true, submit } = {}) {
    if (!str(window).trim()) throw new Error('fenêtre requise');
    return withRetry(() => ps(PS_TYPE, {
      DM_WINDOW: str(window), DM_TEXT: str(text), DM_NAME: str(name), DM_AUTOID: str(automation_id),
      DM_CLEAR: clear === false || clear === 'false' ? '0' : '1', DM_SUBMIT: submit ? '1' : '0',
    }, 60_000));
  }

  async function keys({ keys: k, window } = {}) {
    if (!str(k)) throw new Error('touches requises');
    return ps(PS_KEYS, { DM_KEYS: str(k), DM_WINDOW: str(window) }, 30_000);
  }

  async function read({ window, name, automation_id, control_type } = {}) {
    if (!str(window).trim()) throw new Error('fenêtre requise');
    return withRetry(() => ps(PS_READ, { DM_WINDOW: str(window), DM_NAME: str(name), DM_AUTOID: str(automation_id), DM_CTYPE: str(control_type).replace(/[^A-Za-z]/g, '') }, 90_000));
  }

  async function readNotifications({ app_contains, text_contains } = {}) {
    return withRetry(() => ps(PS_NOTIFICATIONS, { DM_APP: str(app_contains), DM_TEXT: str(text_contains) }, 30_000), 2);
  }

  /** JPEG of the whole primary display, or of one window (title contains `window`). */
  async function screenshot({ window } = {}) {
    const display = screen.getPrimaryDisplay();
    const size = { width: Math.round(display.size.width * display.scaleFactor), height: Math.round(display.size.height * display.scaleFactor) };
    const want = str(window).trim().toLowerCase();
    const sources = await desktopCapturer.getSources({ types: want ? ['window'] : ['screen'], thumbnailSize: size });
    const src = want ? sources.find((s) => s.name.toLowerCase().includes(want)) : sources[0];
    if (!src) throw new Error(want ? `Fenêtre introuvable pour la capture : « ${window} »` : 'Aucun écran à capturer');
    const bytes = src.thumbnail.toJPEG(85);
    if (!bytes.length) throw new Error('Capture vide (fenêtre réduite ?)');
    return { bytes: new Uint8Array(bytes), name: `capture-${new Date().toISOString().replace(/[:.]/g, '-')}.jpg`, content_type: 'image/jpeg' };
  }

  /** Interactive controls of a window, for the AI locator resolver. */
  async function dumpElements({ window, max } = {}) {
    if (!str(window).trim()) throw new Error('fenêtre requise');
    return withRetry(() => ps(PS_DUMP, { DM_WINDOW: str(window), DM_MAX: String(Math.max(0, Number(max) || 0)) }, 60_000), 2);
  }

  /** Live captions (speaker + text) of the running Teams meeting, read from Teams' UI. */
  async function readTeamsCaptions({ window } = {}) {
    return ps(PS_TEAMS_CAPTIONS, { DM_WINDOW: str(window) }, 20_000);
  }

  // ---- web ----
  const PARTITION = 'persist:dm-automation';

  function newBrowser(show) {
    const win = new BrowserWindow({
      width: 1280, height: 860, show: !!show, title: 'Navigateur des automatisations — DeliveryOps',
      autoHideMenuBar: true,
      webPreferences: { partition: PARTITION, contextIsolation: true, nodeIntegration: false, sandbox: true },
    });
    // Popups open in the same window so the run keeps a single page to drive.
    win.webContents.setWindowOpenHandler(({ url }) => { if (/^https?:/i.test(url)) win.loadURL(url).catch(() => {}); return { action: 'deny' }; });
    return win;
  }

  function waitLoad(wc, timeoutMs) {
    return new Promise((resolve) => {
      if (!wc.isLoading()) { resolve(); return; }
      const timer = setTimeout(done, timeoutMs);
      function done() { clearTimeout(timer); wc.removeListener('did-stop-loading', done); resolve(); }
      wc.on('did-stop-loading', done);
    });
  }

  async function page(wc, code) {
    await wc.executeJavaScript(PAGE_HELPER, true);
    return wc.executeJavaScript(code, true);
  }

  async function waitFor(wc, selector, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    const sel = JSON.stringify(selector);
    while (Date.now() < deadline) {
      try { if (await page(wc, `!!window.__dm.find(${sel})`)) return true; } catch { /* navigating */ }
      await new Promise((r) => setTimeout(r, 300));
    }
    throw new Error(`Élément introuvable sur la page : ${selector}`);
  }

  async function browse({ url, actions = [], show, timeout_s } = {}) {
    if (!/^https?:\/\//i.test(str(url))) throw new Error('URL http(s) requise');
    if (!Array.isArray(actions)) throw new Error('actions doit être une liste');
    const t = secs(timeout_s, 30, 300) * 1000;
    const win = newBrowser(show);
    const wc = win.webContents;
    const downloads = [];
    const ses = session.fromPartition(PARTITION);
    const onDownload = (_e, item) => {
      const target = path.join(app.getPath('downloads'), item.getFilename());
      item.setSavePath(target);
      downloads.push(target);
    };
    ses.on('will-download', onDownload);
    const extracted = {};
    try {
      await win.loadURL(str(url)).catch(() => {});
      await waitLoad(wc, t);
      let n = 0;
      for (const a of actions) {
        n += 1;
        if (!a || typeof a !== 'object') throw new Error(`action n°${n} invalide`);
        const sel = str(a.selector);
        const where = `action n°${n} (${a.do})`;
        try {
          switch (a.do) {
            case 'goto':
              if (!/^https?:\/\//i.test(str(a.url))) throw new Error('url http(s) requise');
              await win.loadURL(str(a.url)).catch(() => {});
              await waitLoad(wc, t);
              break;
            case 'click':
              await waitFor(wc, sel, t);
              await page(wc, `(() => { const e = window.__dm.find(${JSON.stringify(sel)}); e.scrollIntoView({ block: 'center' }); e.click(); return true; })()`);
              await new Promise((r) => setTimeout(r, 600));
              await waitLoad(wc, t);
              break;
            case 'type':
              await waitFor(wc, sel, t);
              await page(wc, `(() => { window.__dm.setValue(window.__dm.find(${JSON.stringify(sel)}), ${JSON.stringify(str(a.text))}); return true; })()`);
              break;
            case 'select':
              await waitFor(wc, sel, t);
              await page(wc, `(() => { const e = window.__dm.find(${JSON.stringify(sel)}); const want = ${JSON.stringify(str(a.value))};
                const opt = [...(e.options || [])].find(o => o.value === want || o.text.trim() === want);
                window.__dm.setValue(e, opt ? opt.value : want); return true; })()`);
              break;
            case 'press': {
              const key = KEY_CODES[str(a.key).toLowerCase()] || str(a.key);
              if (!key) throw new Error('touche requise');
              wc.sendInputEvent({ type: 'keyDown', keyCode: key });
              if (key.length === 1) wc.sendInputEvent({ type: 'char', keyCode: key });
              wc.sendInputEvent({ type: 'keyUp', keyCode: key });
              await new Promise((r) => setTimeout(r, 500));
              await waitLoad(wc, t);
              break;
            }
            case 'wait':
              await new Promise((r) => setTimeout(r, Math.min(Math.max(Number(a.ms) || 1000, 0), 60_000)));
              break;
            case 'wait_for':
              await waitFor(wc, sel, t);
              break;
            case 'scroll':
              await page(wc, sel ? `(() => { const e = window.__dm.find(${JSON.stringify(sel)}); if (e) e.scrollIntoView({ block: 'center' }); return true; })()`
                : 'window.scrollBy(0, window.innerHeight); true');
              break;
            case 'extract': {
              await waitFor(wc, sel, t);
              const key = str(a.name) || `v${n}`;
              extracted[key] = await page(wc, a.all
                ? `window.__dm.findAll(${JSON.stringify(sel)}).map(e => window.__dm.text(e))`
                : `window.__dm.text(window.__dm.find(${JSON.stringify(sel)}))`);
              break;
            }
            case 'extract_table': {
              await waitFor(wc, sel, t);
              extracted[str(a.name) || `table${n}`] = await page(wc, `window.__dm.table(window.__dm.find(${JSON.stringify(sel)}))`);
              break;
            }
            default:
              throw new Error(`action inconnue « ${a.do} »`);
          }
        } catch (err) {
          throw new Error(`${where} : ${err && err.message ? err.message : err}`);
        }
      }
      const text = await page(wc, '(document.body ? document.body.innerText : "").slice(0, 4000)').catch(() => '');
      return { url: wc.getURL(), title: wc.getTitle(), extracted, text, downloads };
    } finally {
      ses.removeListener('will-download', onDownload);
      if (!win.isDestroyed()) win.destroy();
    }
  }

  /** Visible browser on the automation session, so the user can log into the sites their
   *  automations use once; cookies persist for later (hidden) runs. */
  async function openBrowser({ url } = {}) {
    const win = newBrowser(true);
    await win.loadURL(/^https?:\/\//i.test(str(url)) ? str(url) : 'https://www.bing.com').catch(() => {});
    return { opened: true };
  }

  return { openApp, windowAction, click, type, keys, read, readNotifications, screenshot, browse, openBrowser, dumpElements, readTeamsCaptions };
}

module.exports = { createInteractSteps, PS_PRELUDE };
