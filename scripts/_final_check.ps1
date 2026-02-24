$mywork = 'C:\Users\1028120\OneDrive - Blue Yonder\MyWork'

Write-Host '=== MyWork folders ==='
Get-ChildItem $mywork -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object {
    $n = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue).Count
    Write-Host ([string]$n).PadLeft(6) ' files  ' $_.Name
}

Write-Host ''
Write-Host '=== Our ZIPs ==='
@('LCT_Diageo_Archive.zip','PSA_Project_Code_Archive.zip','TMS_Translation_Archive.zip') | ForEach-Object {
    $f = Get-ChildItem $mywork -Recurse -Filter $_ -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($f) { Write-Host '  ' ([int]($f.Length/1MB)) 'MB  ' $_ } else { Write-Host '  MISSING  ' $_ }
}

Write-Host ''
Write-Host '=== Recordings ==='
$inbox = $mywork + '\00_Tech_PreSales\00_Inbox\recordings'
$arch  = $mywork + '\Archive_TechnicalConsultant\Recordings'
if (Test-Path $inbox) {
    Write-Host 'Inbox (2024+):'
    Get-ChildItem $inbox -File -ErrorAction SilentlyContinue | ForEach-Object { Write-Host '  ' $_.Name }
} else { Write-Host 'Inbox: NOT CREATED' }
if (Test-Path $arch) {
    Write-Host 'Archive (<=2022):'
    Get-ChildItem $arch -File -ErrorAction SilentlyContinue | ForEach-Object { Write-Host '  ' $_.Name }
} else { Write-Host 'Archive Recordings: NOT CREATED' }
