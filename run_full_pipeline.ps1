Write-Host "============================================="
Write-Host "1. Environment Spin-up: Tearing down existing"
Write-Host "============================================="
docker compose down

Write-Host "============================================="
Write-Host "Starting cluster (with observability profiles)"
Write-Host "============================================="
docker compose --profile langfuse --profile phoenix up -d --build

Write-Host "============================================="
Write-Host "Health Check: Waiting for agentic-engine (8003)"
Write-Host "============================================="
$engineHealthy = $false
while (-not $engineHealthy) {
    try {
        $res = Invoke-WebRequest -Uri "http://localhost:8003/docs" -Method Get -ErrorAction Stop -UseBasicParsing
        if ($res.StatusCode -eq 200) { $engineHealthy = $true }
    } catch {
        Write-Host "Waiting for agentic-engine to respond on 8003..."
        Start-Sleep -Seconds 5
    }
}
Write-Host "agentic-engine is healthy!"

Write-Host "============================================="
Write-Host "Health Check: Waiting for Langfuse (3000)"
Write-Host "============================================="
$langfuseHealthy = $false
while (-not $langfuseHealthy) {
    try {
        $res = Invoke-WebRequest -Uri "http://localhost:3000/api/public/health" -Method Get -ErrorAction Stop -UseBasicParsing
        if ($res.StatusCode -eq 200) { $langfuseHealthy = $true }
    } catch {
        Write-Host "Waiting for Langfuse to respond on 3000..."
        Start-Sleep -Seconds 5
    }
}
Write-Host "Langfuse is healthy!"

Write-Host "Cluster Status: HEALTHY"

Write-Host "============================================="
Write-Host "2. Evaluation Matrix Execution"
Write-Host "============================================="
python scripts/run_eval_matrix.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Evaluation Matrix encountered an error!"
    exit $LASTEXITCODE
}
Write-Host "Evaluation Matrix Execution: COMPLETED"

Write-Host "============================================="
Write-Host "3. Data Aggregation & Verification"
Write-Host "============================================="
python scripts/aggregate_eval_data.py --pretty
if ($LASTEXITCODE -ne 0) {
    Write-Host "Data Aggregation encountered an error!"
    exit $LASTEXITCODE
}

Write-Host "============================================="
Write-Host "Final Validation Check"
Write-Host "============================================="
$valRes = Invoke-WebRequest -Uri "http://localhost:8003/eval/summary" -Method Get -UseBasicParsing
if ($valRes.StatusCode -eq 200) {
    Write-Host "Validation: SUCCESS (Payload received)"
} else {
    Write-Host "Validation: FAILED"
    exit 1
}

Write-Host "PIPELINE COMPLETE."
