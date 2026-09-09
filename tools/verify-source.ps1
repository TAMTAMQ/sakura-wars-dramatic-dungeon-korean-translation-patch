[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [string]$ProfilePath = (Join-Path $PSScriptRoot '..\config\source_profiles.json')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-Ascii([byte[]]$Bytes, [int]$Offset, [int]$Length) {
    return [Text.Encoding]::ASCII.GetString($Bytes, $Offset, $Length).TrimEnd([char]0)
}

function Read-U32Le([byte[]]$Bytes, [int]$Offset) {
    return [BitConverter]::ToUInt32($Bytes, $Offset)
}

$sourceItem = Get-Item -LiteralPath $Source
$profileItem = Get-Item -LiteralPath $ProfilePath
$profilesDocument = Get-Content -Raw -LiteralPath $profileItem.FullName | ConvertFrom-Json
$sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceItem.FullName).Hash.ToLowerInvariant()

$profile = $profilesDocument.profiles |
    Where-Object { [int64]$_.size -eq $sourceItem.Length -and $_.sha256 -eq $sha256 } |
    Select-Object -First 1

if ($null -eq $profile) {
    [ordered]@{
        valid = $false
        error = 'unsupported_source'
        actual = [ordered]@{
            size = $sourceItem.Length
            sha256 = $sha256
        }
        known_profile_ids = @($profilesDocument.profiles | ForEach-Object { $_.id })
    } | ConvertTo-Json -Depth 6
    exit 2
}

$stream = [IO.File]::OpenRead($sourceItem.FullName)
try {
    $headerBytes = [byte[]]::new(512)
    $read = $stream.Read($headerBytes, 0, $headerBytes.Length)
    if ($read -ne $headerBytes.Length) {
        throw "NDS header is truncated: expected 512 bytes, read $read"
    }
}
finally {
    $stream.Dispose()
}

$actualHeader = [ordered]@{
    title = Read-Ascii $headerBytes 0 12
    game_code = Read-Ascii $headerBytes 12 4
    maker_code = Read-Ascii $headerBytes 16 2
    unit_code = [int]$headerBytes[18]
    device_capacity = [int]$headerBytes[20]
    revision = [int]$headerBytes[30]
    arm9_rom_offset = Read-U32Le $headerBytes 32
    arm9_ram_address = Read-U32Le $headerBytes 40
    arm9_size = Read-U32Le $headerBytes 44
    arm7_rom_offset = Read-U32Le $headerBytes 48
    arm7_ram_address = Read-U32Le $headerBytes 56
    arm7_size = Read-U32Le $headerBytes 60
    fnt_offset = Read-U32Le $headerBytes 64
    fnt_size = Read-U32Le $headerBytes 68
    fat_offset = Read-U32Le $headerBytes 72
    fat_size = Read-U32Le $headerBytes 76
    arm9_overlay_offset = Read-U32Le $headerBytes 80
    arm9_overlay_size = Read-U32Le $headerBytes 84
    banner_offset = Read-U32Le $headerBytes 104
    used_rom_size = Read-U32Le $headerBytes 128
    header_size = Read-U32Le $headerBytes 132
}

$mismatches = @()
foreach ($property in $profile.header.PSObject.Properties) {
    $actual = $actualHeader[$property.Name]
    if ($actual -ne $property.Value) {
        $mismatches += [ordered]@{
            field = $property.Name
            expected = $property.Value
            actual = $actual
        }
    }
}

$valid = $mismatches.Count -eq 0
[ordered]@{
    valid = $valid
    profile_id = $profile.id
    profile_status = $profile.status
    size = $sourceItem.Length
    sha256 = $sha256
    header = $actualHeader
    mismatches = $mismatches
} | ConvertTo-Json -Depth 8

if (-not $valid) {
    exit 3
}
