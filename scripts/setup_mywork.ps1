# Corporate OS - OneDrive Folder Setup
# Run: powershell -ExecutionPolicy Bypass -File .\setup_mywork.ps1 -DryRun

param(
    [string]$OneDrivePath = "C:\Users\1028120\OneDrive - Blue Yonder",
    [switch]$DryRun = $false
)

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Corporate OS - MyWork Setup" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

if ($DryRun) {
    Write-Host "DRY RUN - No changes will be made`n" -ForegroundColor Yellow
}

Write-Host "OneDrive Path: $OneDrivePath`n" -ForegroundColor Gray

# Define folder structure
$folders = @(
    # Root
    "MyWork",
    
    # Current Role - Tech Pre-Sales
    "MyWork\00_Tech_PreSales",
    
    # Inbox
    "MyWork\00_Tech_PreSales\00_Inbox",
    "MyWork\00_Tech_PreSales\00_Inbox\recordings",
    "MyWork\00_Tech_PreSales\00_Inbox\documents",
    "MyWork\00_Tech_PreSales\00_Inbox\emails",
    
    # Projects (aktywne)
    "MyWork\00_Tech_PreSales\10_Projects",
    "MyWork\00_Tech_PreSales\10_Projects\_template",
    "MyWork\00_Tech_PreSales\10_Projects\_template\00_rfp",
    "MyWork\00_Tech_PreSales\10_Projects\_template\10_meetings",
    "MyWork\00_Tech_PreSales\10_Projects\_template\20_deliverables",
    "MyWork\00_Tech_PreSales\10_Projects\_template\30_correspondence",
    
    # Knowledge
    "MyWork\00_Tech_PreSales\20_Knowledge",
    "MyWork\00_Tech_PreSales\20_Knowledge\00_products",
    "MyWork\00_Tech_PreSales\20_Knowledge\10_competitors",
    "MyWork\00_Tech_PreSales\20_Knowledge\20_industry",
    "MyWork\00_Tech_PreSales\20_Knowledge\30_training",
    
    # Templates
    "MyWork\00_Tech_PreSales\30_Templates",
    "MyWork\00_Tech_PreSales\30_Templates\rfp",
    "MyWork\00_Tech_PreSales\30_Templates\presentations",
    "MyWork\00_Tech_PreSales\30_Templates\emails",
    
    # Archive (wewnątrz roli!)
    "MyWork\00_Tech_PreSales\80_Archive",
    "MyWork\00_Tech_PreSales\80_Archive\2023",
    "MyWork\00_Tech_PreSales\80_Archive\2024",
    "MyWork\00_Tech_PreSales\80_Archive\2025",
    "MyWork\00_Tech_PreSales\80_Archive\2026",
    
    # System
    "MyWork\00_Tech_PreSales\90_System",
    "MyWork\00_Tech_PreSales\90_System\index",
    "MyWork\00_Tech_PreSales\90_System\cache",
    "MyWork\00_Tech_PreSales\90_System\logs",
    "MyWork\00_Tech_PreSales\90_System\briefs",
    
    # Past Roles (puste, do migracji)
    "MyWork\Archive_BDR",
    "MyWork\Archive_TechnicalConsultant"
)

# Create folders
$created = 0
$exists = 0

foreach ($folder in $folders) {
    $fullPath = Join-Path $OneDrivePath $folder
    
    if (Test-Path $fullPath) {
        Write-Host "  EXISTS:  $folder" -ForegroundColor DarkGray
        $exists++
    } else {
        if ($DryRun) {
            Write-Host "  CREATE:  $folder" -ForegroundColor Yellow
        } else {
            New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
            Write-Host "  CREATED: $folder" -ForegroundColor Green
        }
        $created++
    }
}

# Create README files
$readmes = @{
    "MyWork\README.md" = @"
# MyWork

Career management folder for Blue Yonder.

## Structure

| Folder | Purpose |
|--------|---------|
| ``00_Tech_PreSales/`` | Current role - Technology Pre-Sales |
| ``Archive_BDR/`` | Past role - Business Development |
| ``Archive_TechnicalConsultant/`` | Past role - Technical Consultant |

## When Role Changes

1. Rename ``00_Tech_PreSales`` to ``Archive_Tech_PreSales``
2. Create new ``00_NewRole`` folder
3. Archive keeps full context of that role
"@

    "MyWork\00_Tech_PreSales\README.md" = @"
# Tech Pre-Sales

Current active role - Technology Pre-Sales.

## Structure

| Folder | Purpose |
|--------|---------|
| ``00_Inbox/`` | Drop files for processing |
| ``10_Projects/`` | Active opportunities (Firma_Rozwiązanie) |
| ``20_Knowledge/`` | Reference materials |
| ``30_Templates/`` | Reusable templates |
| ``80_Archive/`` | Completed projects by year |
| ``90_System/`` | Automation system data |

## Project Naming

``Firma_Rozwiązanie`` - flat list

Examples:
- Honda_PALOMA
- NEOM_WMS  
- PepsiCo_EMEA
- Corning_Planning

## Archiving Projects

``10_Projects/Honda_PALOMA`` → ``80_Archive/2025/Honda_PALOMA``
"@

    "MyWork\00_Tech_PreSales\10_Projects\README.md" = @"
# Projects

Active opportunities.

## Naming

``Firma_Rozwiązanie``

## New Project

Copy ``_template/`` → rename to ``Firma_Rozwiązanie``

## When Complete

Move to ``../80_Archive/YYYY/``
"@

    "MyWork\00_Tech_PreSales\10_Projects\_template\README.md" = @"
# Project Template

Copy and rename to: ``Firma_Rozwiązanie``

## Subfolders

| Folder | Purpose |
|--------|---------|
| ``00_rfp/`` | RFP documents, responses |
| ``10_meetings/`` | Notes, transcripts |
| ``20_deliverables/`` | Our work products |
| ``30_correspondence/`` | Emails, messages |
"@

    "MyWork\00_Tech_PreSales\20_Knowledge\README.md" = @"
# Knowledge

Reference materials (not project-specific).

## Subfolders

| Folder | Purpose |
|--------|---------|
| ``00_products/`` | BY product documentation |
| ``10_competitors/`` | Competitor information |
| ``20_industry/`` | Industry research |
| ``30_training/`` | Training materials |
"@

    "MyWork\00_Tech_PreSales\80_Archive\README.md" = @"
# Archive

Completed projects organized by year.

``80_Archive/YYYY/Firma_Rozwiązanie/``

Move entire project folder here when complete.
"@
}

Write-Host "`nCreating README files..." -ForegroundColor Cyan

foreach ($readme in $readmes.GetEnumerator()) {
    $fullPath = Join-Path $OneDrivePath $readme.Key
    
    if (Test-Path $fullPath) {
        Write-Host "  EXISTS:  $($readme.Key)" -ForegroundColor DarkGray
    } else {
        if ($DryRun) {
            Write-Host "  CREATE:  $($readme.Key)" -ForegroundColor Yellow
        } else {
            $readme.Value | Out-File -FilePath $fullPath -Encoding UTF8
            Write-Host "  CREATED: $($readme.Key)" -ForegroundColor Green
        }
    }
}

# Summary
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Folders to create: $created" -ForegroundColor $(if ($DryRun) { "Yellow" } else { "Green" })
Write-Host "  Folders existing:  $exists" -ForegroundColor Gray

if ($DryRun) {
    Write-Host "`nTo apply, run without -DryRun:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\setup_mywork.ps1" -ForegroundColor White
} else {
    Write-Host "`nDone! Structure created at:" -ForegroundColor Green
    Write-Host "  $OneDrivePath\MyWork" -ForegroundColor White
}
