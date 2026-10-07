# Post-deploy smoke test (PowerShell):  ./scripts/smoke_test.ps1 -Api https://finbase-api.onrender.com
param([string]$Api = $(if ($env:API_URL) { $env:API_URL } else { "http://localhost:8000" }))
$ErrorActionPreference = "Stop"
$Api = $Api.TrimEnd("/")
function Fail($msg) { Write-Error "FAIL: $msg"; exit 1 }

Write-Host "1/4 health (allowing up to 90 s for a cold start)"
$health = $null
for ($i = 0; $i -lt 18 -and -not $health; $i++) {
  try { $health = Invoke-RestMethod -Uri "$Api/api/health" -TimeoutSec 10 } catch { Start-Sleep -Seconds 5 }
}
if (-not $health) { Fail "health did not respond" }
Write-Host ("status={0} provider={1} chat={2} embed={3} chunks={4}" -f $health.status, $health.provider, $health.chat_model, $health.embed_model, $health.chunks)

Write-Host "2/4 answerable question"
$body = @{ message = "What is the foreclosure charge if I close my personal loan after 18 months?" } | ConvertTo-Json
$ans = Invoke-RestMethod -Uri "$Api/api/chat" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 60
if (-not $ans.answerable -or $ans.sources.Count -lt 1) { Fail "expected a grounded answer with sources" }
Write-Host ("answerable={0} confidence={1} source={2}" -f $ans.answerable, $ans.confidence.label, $ans.sources[0].citation)

Write-Host "3/4 not-in-knowledge-base question"
$body = @{ message = "What is the FinBase home loan interest rate?" } | ConvertTo-Json
$nf = Invoke-RestMethod -Uri "$Api/api/chat" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 60
if ($nf.answerable) { Fail "expected abstention" }
Write-Host "abstained: $($nf.answer.Substring(0, [Math]::Min(80, $nf.answer.Length)))"

Write-Host "4/4 SSE stream"
$body = @{ message = "What is the UPI daily limit?"; stream = $true } | ConvertTo-Json
$sse = (Invoke-WebRequest -Uri "$Api/api/chat" -Method Post -ContentType "application/json" -Headers @{ Accept = "text/event-stream" } -Body $body -TimeoutSec 60 -UseBasicParsing).Content
foreach ($e in "meta", "token", "done") { if ($sse -notmatch "(?m)^event: $e") { Fail "no $e event" } }
Write-Host ("SSE events: {0}" -f ([regex]::Matches($sse, "(?m)^event:")).Count)
Write-Host "SMOKE TEST PASSED"
