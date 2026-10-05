$ErrorActionPreference = 'Stop'
$reportRoot = Split-Path $PSScriptRoot -Parent
$reportInput = Join-Path $reportRoot 'report\AI_Travel_Agent_College_Report_Revised.docx'
$reportPdf = Join-Path $PSScriptRoot 'render\report.pdf'
New-Item -ItemType Directory -Path (Split-Path $reportPdf -Parent) -Force | Out-Null
$reportWord = $null
$reportDocument = $null
try {
    $reportWord = New-Object -ComObject Word.Application
    $reportWord.Visible = $false
    $reportWord.DisplayAlerts = 0
    $reportDocument = $reportWord.Documents.Open($reportInput, $false, $false)
    $reportDocument.Repaginate()
    $reportDocument.ExportAsFixedFormat($reportPdf, 17)
    Write-Output "Pages: $($reportDocument.ComputeStatistics(2))"
    $reportDocument.Close(0)
    $reportDocument = $null
} finally {
    if ($null -ne $reportDocument) { $reportDocument.Close(0) }
    if ($null -ne $reportWord) { $reportWord.Quit() }
}
