# ============================================================================
# validate-xaml.ps1
#
# Structural sanity checks for Main.xaml that catch the mistakes a plain XML
# well-formedness check misses:
#   * If / TryCatch / Switch / While / Assign element ordering
#   * every bracketed expression references only declared variables
#   * the locked configuration (Outlook account, folder, 10-email limit) is
#     unchanged
#   * every template file read by the workflow exists
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File LocalHarness\validate-xaml.ps1
# Exit code 0 means all checks passed.
# ============================================================================

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$mainPath = Join-Path $repo 'Main.xaml'
$problems = New-Object System.Collections.Generic.List[string]
$checks = 0

function Check([string]$name, [bool]$ok) {
    $script:checks++
    if ($ok) {
        Write-Host ("  ok   " + $name)
    } else {
        Write-Host ("  FAIL " + $name) -ForegroundColor Red
        $script:problems.Add($name)
    }
}

Write-Host "Structural validation of $mainPath"
Write-Host ""

[xml]$doc = Get-Content -Raw -Encoding UTF8 $mainPath
$ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
$ns.AddNamespace('wf', 'http://schemas.microsoft.com/netfx/2009/xaml/activities')

# ---------------------------------------------------------------- cardinality
$ifs = $doc.SelectNodes("//*[local-name()='If']")
foreach ($if in $ifs) {
    $then = @($if.ChildNodes | Where-Object { $_.LocalName -eq 'If.Then' })
    $else = @($if.ChildNodes | Where-Object { $_.LocalName -eq 'If.Else' })
    # Else is optional in UiPath; what matters is exactly one Then and at most one Else.
    Check ("If '" + $if.DisplayName + "' has a Then and at most one Else") ($then.Count -eq 1 -and $else.Count -le 1)
    $ifLabels = @($if.ChildNodes | Where-Object { $_.LocalName -like 'If.*' } | ForEach-Object { $_.LocalName })
    Check ("If '" + $if.DisplayName + "' puts Then before Else") (((($ifLabels -join ',')) -eq 'If.Then') -or ((($ifLabels -join ',')) -eq 'If.Then,If.Else'))
}

$tryCatches = $doc.SelectNodes("//*[local-name()='TryCatch']")
foreach ($tc in $tryCatches) {
    $try = @($tc.ChildNodes | Where-Object { $_.LocalName -eq 'TryCatch.Try' })
    $catches = @($tc.ChildNodes | Where-Object { $_.LocalName -eq 'TryCatch.Catches' })
    Check ("TryCatch '" + $tc.DisplayName + "' has Try then Catches") ($try.Count -eq 1 -and $catches.Count -eq 1)
    $labels = @($tc.ChildNodes | Where-Object { $_.LocalName -like 'TryCatch.*' } | ForEach-Object { $_.LocalName })
    $expected = @('TryCatch.Try', 'TryCatch.Catches')
    if ($labels -contains 'TryCatch.Finally') { $expected += 'TryCatch.Finally' }
    Check ("TryCatch '" + $tc.DisplayName + "' children are in schema order") ((($labels -join ',') -eq ($expected -join ',')))
}

$switches = $doc.SelectNodes("//*[local-name()='Switch']")
foreach ($sw in $switches) {
    $default = @($sw.ChildNodes | Where-Object { $_.LocalName -eq 'Switch.Default' })
    Check ("Switch '" + $sw.DisplayName + "' has a Default") ($default.Count -eq 1)
}

$whiles = $doc.SelectNodes("//*[local-name()='While']")
foreach ($wh in $whiles) {
    $body = @($wh.ChildNodes | Where-Object { $_.LocalName -eq 'While.Body' })
    Check ("While '" + $wh.DisplayName + "' has a Body") ($body.Count -eq 1)
}

$assigns = $doc.SelectNodes("//*[local-name()='Assign']")
foreach ($a in $assigns) {
    $to = @($a.ChildNodes | Where-Object { $_.LocalName -eq 'Assign.To' })
    $val = @($a.ChildNodes | Where-Object { $_.LocalName -eq 'Assign.Value' })
    Check ("Assign '" + $a.DisplayName + "' has To then Value") ($to.Count -eq 1 -and $val.Count -eq 1)
}

# ------------------------------------------------------------- variable names
$declared = @($doc.SelectNodes("//*[local-name()='Variable']") | ForEach-Object { $_.Name })
$declaredSet = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::Ordinal)
foreach ($d in $declared) { [void]$declaredSet.Add($d) }

$arguments = @($doc.SelectNodes("//*[local-name()='DelegateInArgument']") | ForEach-Object { $_.Name })
$argumentSet = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::Ordinal)
foreach ($a in $arguments) { [void]$argumentSet.Add($a) }

$allowed = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::Ordinal)
foreach ($d in $declaredSet) { [void]$allowed.Add($d) }
foreach ($a in $argumentSet) { [void]$allowed.Add($a) }
foreach ($extra in @('New', 'Nothing', 'If', 'String', 'Directory', 'Path', 'Math', 'DateTime', 'Convert', 'Environment', 'Now', 'True', 'False',
                      'File', 'UiPath', 'System')) {
    [void]$allowed.Add($extra)
}

# Collect every [expression] attribute / text node.
$expressionTexts = New-Object System.Collections.Generic.List[string]
foreach ($attr in $doc.SelectNodes("//@*")) {
    $v = $attr.Value
    if ($v.StartsWith('[') -and $v.EndsWith(']')) { $expressionTexts.Add($v) }
}
foreach ($el in $doc.SelectNodes("//*[local-name()='InArgument' or local-name()='OutArgument']")) {
    if ($el.InnerText.StartsWith('[')) { $expressionTexts.Add($el.InnerText) }
}

$identifierPattern = [regex]'(?<![\.\w])([A-Za-z_][A-Za-z0-9_]*)'
$unknown = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::Ordinal)
$keywords = @('If','Then','Else','AndAlso','OrElse','Not','And','Or','Is','Nothing','True','False',
              'New','Of','String','Integer','Boolean','Date','Exception','System','TimeSpan','Double','CInt','CDate','CStr','CBool')
foreach ($expr in $expressionTexts) {
    $stripped = [regex]::Replace($expr, '"[^"]*"', '""')
    foreach ($m in $identifierPattern.Matches($stripped)) {
        $name = $m.Groups[1].Value
        if ($keywords -contains $name) { continue }
        # Skip property / method names that follow a dot.
        $idx = $m.Index
        if ($idx -gt 0 -and $stripped[$idx - 1] -eq '.') { continue }
        if (-not $allowed.Contains($name)) { [void]$unknown.Add($name) }
    }
}
if ($unknown.Count -gt 0) {
    Write-Host ("       undeclared identifiers: " + (($unknown | Sort-Object) -join ', '))
}
Check "all bracketed expressions only use declared variables" ($unknown.Count -eq 0)

# --------------------------------------------------------- locked configuration
$accounts = @($doc.SelectNodes("//*[local-name()='OutlookApplicationCard']") | ForEach-Object { $_.Account })
Check ("all Outlook cards keep the account Meeno2b@outlook.com") (($accounts.Count -ge 1) -and (($accounts | Where-Object { $_ -ne 'Meeno2b@outlook.com' }).Count -eq 0))

$forEaches = @($doc.SelectNodes("//*[local-name()='ForEachEmailX']"))
Check "exactly one ForEachEmailX drives the batch" ($forEaches.Count -eq 1)
if ($forEaches.Count -eq 1) {
    $fe = $forEaches[0]
    Check "the target folder is still UiPath Test" ($fe.Mails -match 'Folder\("UiPath Test"\)')
    Check "the 10-email limit is unchanged" ($fe.NumberOfEmailsLimit -eq '10')
    Check "unread-only filtering is unchanged" ($fe.UnreadOnly -eq 'True')
}

# ---------------------------------------------------------------- templates
foreach ($tpl in @('EmailSuccessTemplate.txt', 'EmailFailureTemplate.txt', 'EmailAbortedTemplate.txt')) {
    Check ("template exists: EmailTemplates\" + $tpl) (Test-Path (Join-Path $repo ('EmailTemplates\' + $tpl)))
}

# Every String.Format call must supply exactly the placeholders its template uses.
$formatCalls = @()
foreach ($el in $doc.SelectNodes("//*[local-name()='InArgument' or local-name()='OutArgument']")) {
    $t = $el.InnerText.Trim()
    $m = [regex]::Match($t, '^\[String\.Format\(\s*(\w+)\s*,(.*)\)\]$', 'Singleline')
    if ($m.Success) {
        $formatCalls += [pscustomobject]@{
            TemplateVar = $m.Groups[1].Value
            ArgCount    = ($m.Groups[2].Value -split ',').Count
        }
    }
}
Check ("found the expected String.Format calls") ($formatCalls.Count -eq 3)

$templateVarToFile = @{
    'varEmailSuccessTemplate' = 'EmailSuccessTemplate.txt'
    'varEmailFailureTemplate' = 'EmailFailureTemplate.txt'
    'varEmailAbortedTemplate' = 'EmailAbortedTemplate.txt'
}
foreach ($call in $formatCalls) {
    if (-not $templateVarToFile.ContainsKey($call.TemplateVar)) {
        Check ("String.Format uses a known template variable (" + $call.TemplateVar + ")") $false
        continue
    }
    $file = Join-Path $repo ('EmailTemplates\' + $templateVarToFile[$call.TemplateVar])
    $content = Get-Content -Raw -Encoding UTF8 $file
    $maxIndex = -1
    foreach ($pm in [regex]::Matches($content, '\{(\d+)\}')) {
        $i = [int]$pm.Groups[1].Value
        if ($i -gt $maxIndex) { $maxIndex = $i }
    }
    $expectedArgs = $maxIndex + 1
    Check ($templateVarToFile[$call.TemplateVar] + " has $expectedArgs placeholders and the call passes " + $call.ArgCount) ($expectedArgs -eq $call.ArgCount)
}

# --------------------------------------------------------------------- result
Write-Host ""
Write-Host ("$checks checks, " + $problems.Count + " problem(s)")
if ($problems.Count -eq 0) {
    Write-Host "RESULT: PASS" -ForegroundColor Green
    exit 0
}
Write-Host "RESULT: FAIL" -ForegroundColor Red
exit 1
