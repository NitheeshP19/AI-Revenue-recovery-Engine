param(
    [string]$ErrorCode   = "GATEWAY_TIMEOUT",
    [string]$PaymentId   = ("pay_LIVE_" + [System.Guid]::NewGuid().ToString("N").Substring(0,8).ToUpper()),
    [int]   $AmountPaise = 249900,
    [string]$Bank        = "HDFC",
    [string]$Method      = "card"
)

$WebhookSecret = "opensource_webhook_secret_2026"
$Url           = "http://localhost:8080/api/v1/webhooks/razorpay"

# Build payload
$payload = @{
    entity   = "event"
    event    = "payment.failed"
    contains = @("payment")
    payload  = @{
        payment = @{
            entity = @{
                id                = $PaymentId
                amount            = $AmountPaise
                currency          = "INR"
                status            = "failed"
                method            = $Method
                error_code        = $ErrorCode
                error_description = "Simulated $ErrorCode on $Bank during payment_authorization"
                error_source      = "gateway"
                error_step        = "payment_authorization"
                error_reason      = "gateway_error"
                bank              = $Bank
                email             = "customer@example.com"
                contact           = "+919876543210"
            }
        }
    }
} | ConvertTo-Json -Depth 10 -Compress

# Compute HMAC-SHA256 signature
$secretBytes  = [System.Text.Encoding]::UTF8.GetBytes($WebhookSecret)
$payloadBytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
$hmac         = [System.Security.Cryptography.HMACSHA256]::new($secretBytes)
$hashBytes    = $hmac.ComputeHash($payloadBytes)
$signature    = [System.BitConverter]::ToString($hashBytes).Replace("-","").ToLower()

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  AI Revenue Recovery -- Live Webhook Test" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Payment ID  : $PaymentId" -ForegroundColor White
Write-Host "  Error Code  : $ErrorCode" -ForegroundColor Yellow
Write-Host "  Amount      : INR $(($AmountPaise / 100).ToString('N2'))" -ForegroundColor White
Write-Host "  Bank        : $Bank   Method: $Method" -ForegroundColor White
Write-Host "  Signature   : $($signature.Substring(0,16))..." -ForegroundColor DarkGray
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# POST to Go ingestion API
try {
    $response = Invoke-RestMethod `
        -Method POST `
        -Uri $Url `
        -ContentType "application/json" `
        -Headers @{ "X-Razorpay-Signature" = $signature } `
        -Body $payload

    Write-Host "" 
    Write-Host "  [SUCCESS] WEBHOOK ACCEPTED" -ForegroundColor Green
    Write-Host "" 
    Write-Host ($response | ConvertTo-Json -Depth 10) -ForegroundColor Cyan
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    $body       = $_.ErrorDetails.Message
    Write-Host "  [ERROR] HTTP $statusCode" -ForegroundColor Red
    Write-Host $body -ForegroundColor Red
}
Write-Host ""
