$procs = Get-Process -Name "python*" -ErrorAction SilentlyContinue
if ($procs) {
    foreach ($p in $procs) {
        $run = (Get-Date) - $p.StartTime
        Write-Output ("PID {0}  CPU {1:F0}s  RAM {2}MB  Running {3:hh\:mm\:ss}" -f $p.Id, $p.CPU, [int]($p.WorkingSet/1MB), $run)
    }
} else {
    Write-Output "No python process running"
}
