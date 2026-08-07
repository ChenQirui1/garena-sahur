[CmdletBinding()]
param(
    [string]$NpcId,
    [string]$Message = "Hello! This is a Spotlight speech-bubble test.",
    [string]$BaseUrl = "http://localhost:8000",
    [string]$SessionId = "minecraft-spotlight-001"
)

$ErrorActionPreference = "Stop"
$apiRoot = $BaseUrl.TrimEnd("/")

function Get-CandidateId($Candidate) {
    if ($null -ne $Candidate.npc_id -and -not [string]::IsNullOrWhiteSpace([string]$Candidate.npc_id)) {
        return [string]$Candidate.npc_id
    }
    return [string]$Candidate.uuid
}

if ([string]::IsNullOrWhiteSpace($Message)) {
    throw "Message must not be empty."
}

try {
    $state = Invoke-RestMethod `
        -Method Get `
        -Uri "$apiRoot/api/v1/candidates" `
        -TimeoutSec 5
} catch {
    throw "Could not read current candidates from $apiRoot. Restart mock_subscriber.py, then try again. $($_.Exception.Message)"
}

if ($state.websocket_connections -lt 1) {
    throw "No Minecraft WebSocket is connected. Run /spotlight connect in Minecraft first."
}

if ($null -eq $state.snapshot_age_ms -or $state.snapshot_age_ms -gt 2000) {
    throw "The latest Minecraft snapshot is stale or missing. Keep the game unpaused and wait for data to flow."
}

$candidates = @($state.candidates)
if ($candidates.Count -eq 0) {
    throw "There are no current NPC candidates. Move within 24 blocks of a villager and try again."
}

if ([string]::IsNullOrWhiteSpace($NpcId)) {
    $target = $candidates | Sort-Object @{ Expression = {
        $distance = $_.world_distance_blocks
        if ($null -eq $distance) {
            $distance = $_.distance
        }
        if ($null -eq $distance) {
            [double]::PositiveInfinity
        } else {
            [double]$distance
        }
    }} | Select-Object -First 1
    $NpcId = Get-CandidateId $target
} else {
    $target = $candidates | Where-Object {
        (Get-CandidateId $_).Equals($NpcId, [System.StringComparison]::OrdinalIgnoreCase)
    } | Select-Object -First 1
    if ($null -eq $target) {
        $available = ($candidates | ForEach-Object { Get-CandidateId $_ }) -join ", "
        throw "NPC $NpcId is not a current candidate. Current IDs: $available"
    }
}

$parsedNpcId = [guid]::Empty
if (-not [guid]::TryParse($NpcId, [ref]$parsedNpcId)) {
    throw "Candidate ID '$NpcId' is not a Minecraft UUID."
}

$now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$sourceSequence = if ($null -eq $state.last_sequence) { 0 } else { [long]$state.last_sequence }
$command = @{
    schema_version   = "1.0"
    message_type     = "behaviour_command"
    session_id       = $SessionId
    command_id       = [guid]::NewGuid().ToString()
    request_id       = [guid]::NewGuid().ToString()
    npc_id           = $parsedNpcId.ToString()
    tier             = "focused"
    event_id         = $null
    conversation_id  = $null
    turn_id          = $null
    source_sequence  = $sourceSequence
    command_sequence = $now
    created_at_ms    = $now
    expires_at_ms    = $now + 15000
    dialogue         = $Message
    action           = $null
    fallback_used    = $false
}

$response = Invoke-RestMethod `
    -Method Post `
    -Uri "$apiRoot/api/v1/commands/$SessionId" `
    -ContentType "application/json" `
    -Body ($command | ConvertTo-Json -Depth 6 -Compress) `
    -TimeoutSec 5

$displayName = [string]$target.name
if ([string]::IsNullOrWhiteSpace($displayName)) {
    $displayName = [string]$target.profession
}
if ([string]::IsNullOrWhiteSpace($displayName)) {
    $displayName = "villager"
}
Write-Host "Dialogue sent to $displayName ($NpcId) through $($response.connections) connection(s)." -ForegroundColor Green
Write-Host "Minecraft should log: Applied behaviour command $($command.command_id)" -ForegroundColor DarkGray
