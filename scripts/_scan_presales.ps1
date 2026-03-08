$onedrive = $env:CORP_ONEDRIVE_PATH
if (-not $onedrive) { $onedrive = "C:\Users\1028120\OneDrive - Blue Yonder" }

$src = Join-Path $onedrive "Projects\_Technical Presales"

Write-Output "=== Technical Presales structure ==="
Write-Output "Source: $src"
Write-Output ""

if (-not (Test-Path $src)) {
    Write-Output "ERROR: path not found"
    exit 1
}

# Depth-1 subfolders with file counts
Write-Output "--- Depth-1 subfolders ---"
Get-ChildItem $src -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object {
    $n = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue).Count
    Write-Output ("{0,6} files  {1}" -f $n, $_.Name)
}

# Root-level files
$rootFiles = Get-ChildItem $src -File -ErrorAction SilentlyContinue
Write-Output ""
Write-Output ("--- Root-level files ({0}) ---" -f $rootFiles.Count)
$rootFiles | Sort-Object Name | ForEach-Object { Write-Output "  $($_.Name)" }

Write-Output ""
Write-Output "--- Depth-2 breakdown ---"
Get-ChildItem $src -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object {
    $parent = $_.Name
    $subs = Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue | Sort-Object Name
    if ($subs) {
        foreach ($sub in $subs) {
            $n = (Get-ChildItem $sub.FullName -Recurse -File -ErrorAction SilentlyContinue).Count
            Write-Output ("{0,6} files  {1}\{2}" -f $n, $parent, $sub.Name)
        }
    } else {
        $n = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue).Count
        Write-Output ("{0,6} files  {1}  (no subfolders)" -f $n, $parent)
    }
}

Write-Output ""
Write-Output "--- Extension breakdown ---"
Get-ChildItem $src -Recurse -File -ErrorAction SilentlyContinue |
    Group-Object Extension |
    Sort-Object Count -Descending |
    ForEach-Object { Write-Output ("{0,6}  {1}" -f $_.Count, $_.Name) }

Write-Output ""
Write-Output "--- Sample filenames (first 30 pptx) ---"
Get-ChildItem $src -Recurse -Filter "*.pptx" -ErrorAction SilentlyContinue |
    Select-Object -First 30 |
    Sort-Object Name |
    ForEach-Object { Write-Output "  $($_.Name)" }
