$mywork = "C:\Users\1028120\OneDrive - Blue Yonder\MyWork"

Write-Output "=== MyWork subfolders ==="
Get-ChildItem $mywork -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $files = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue).Count
    Write-Output ("{0,6} files  {1}" -f $files, $_.Name)
}

Write-Output ""
Write-Output "=== ZIPs created ==="
Get-ChildItem $mywork -Recurse -Filter "*.zip" -ErrorAction SilentlyContinue | ForEach-Object {
    $mb = [int]($_.Length / 1MB)
    Write-Output ("{0,6} MB  {1}" -f $mb, $_.Name)
}
