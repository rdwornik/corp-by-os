$onedrive = $env:CORP_ONEDRIVE_PATH
if (-not $onedrive) { $onedrive = "C:\Users\1028120\OneDrive - Blue Yonder" }

$src = Join-Path $onedrive "Projects\_Technical Presales\Presentations Delivered"

Write-Output "=== Presentations Delivered ==="
Write-Output "Path: $src"
Write-Output ""

$files = Get-ChildItem $src -File -ErrorAction SilentlyContinue | Sort-Object Name
Write-Output ("Total: {0} files" -f $files.Count)
Write-Output ""

$files | ForEach-Object {
    $size = [int]($_.Length / 1KB)
    Write-Output ("{0,6} KB  {1}" -f $size, $_.Name)
}
