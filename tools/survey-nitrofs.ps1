[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [string]$ProfilePath = '',

    [string]$OutputPath = '',

    [switch]$AllowUnregisteredSurvey
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($ProfilePath)) { $ProfilePath = Join-Path $projectRoot 'config\source_profiles.json' }
if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path $projectRoot 'work\nitrofs-inventory.json' }

function Read-U16Le([byte[]]$Bytes, [int]$Offset) {
    if ($Offset -lt 0 -or $Offset + 2 -gt $Bytes.Length) {
        throw "u16 read outside buffer at offset $Offset"
    }
    return [BitConverter]::ToUInt16($Bytes, $Offset)
}

function Read-U32Le([byte[]]$Bytes, [int]$Offset) {
    if ($Offset -lt 0 -or $Offset + 4 -gt $Bytes.Length) {
        throw "u32 read outside buffer at offset $Offset"
    }
    return [BitConverter]::ToUInt32($Bytes, $Offset)
}

function Read-Exact([IO.FileStream]$Stream, [uint32]$Offset, [uint32]$Length) {
    if ([uint64]$Offset + [uint64]$Length -gt [uint64]$Stream.Length) {
        throw "range exceeds source: offset=$Offset length=$Length source=$($Stream.Length)"
    }
    $buffer = [byte[]]::new([int]$Length)
    $Stream.Position = $Offset
    $total = 0
    while ($total -lt $buffer.Length) {
        $read = $Stream.Read($buffer, $total, $buffer.Length - $total)
        if ($read -eq 0) {
            throw "unexpected end of source at offset $($Offset + $total)"
        }
        $total += $read
    }
    return $buffer
}

function Convert-Name([byte[]]$Bytes, [int]$Offset, [int]$Length) {
    if ($Offset -lt 0 -or $Offset + $Length -gt $Bytes.Length) {
        throw "name exceeds FNT at offset $Offset length $Length"
    }
    return [Text.Encoding]::ASCII.GetString($Bytes, $Offset, $Length)
}

$sourceItem = Get-Item -LiteralPath $Source
$profileItem = Get-Item -LiteralPath $ProfilePath
$profilesDocument = Get-Content -Raw -LiteralPath $profileItem.FullName | ConvertFrom-Json
$sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceItem.FullName).Hash.ToLowerInvariant()
$profile = $profilesDocument.profiles |
    Where-Object { [int64]$_.size -eq $sourceItem.Length -and $_.sha256 -eq $sha256 } |
    Select-Object -First 1
if ($null -eq $profile -and -not $AllowUnregisteredSurvey) {
    throw "unsupported source: size=$($sourceItem.Length) sha256=$sha256"
}

$stream = [IO.File]::OpenRead($sourceItem.FullName)
try {
    $headerBytes = Read-Exact $stream 0 512
    $layout = if ($null -ne $profile) {
        $profile.header
    }
    else {
        [pscustomobject]@{
            fnt_offset = Read-U32Le $headerBytes 64
            fnt_size = Read-U32Le $headerBytes 68
            fat_offset = Read-U32Le $headerBytes 72
            fat_size = Read-U32Le $headerBytes 76
            arm9_overlay_offset = Read-U32Le $headerBytes 80
            arm9_overlay_size = Read-U32Le $headerBytes 84
        }
    }
    $script:fnt = Read-Exact $stream ([uint32]$layout.fnt_offset) ([uint32]$layout.fnt_size)
    $fatBytes = Read-Exact $stream ([uint32]$layout.fat_offset) ([uint32]$layout.fat_size)
    $arm9OverlayBytes = Read-Exact $stream ([uint32]$layout.arm9_overlay_offset) ([uint32]$layout.arm9_overlay_size)
}
finally {
    $stream.Dispose()
}

if ($fatBytes.Length % 8 -ne 0) {
    throw "FAT size is not divisible by 8: $($fatBytes.Length)"
}

$script:fat = @()
for ($fileId = 0; $fileId -lt $fatBytes.Length / 8; $fileId++) {
    $start = [uint32](Read-U32Le $fatBytes ($fileId * 8))
    $end = [uint32](Read-U32Le $fatBytes ($fileId * 8 + 4))
    if ($end -lt $start -or [uint64]$end -gt [uint64]$sourceItem.Length) {
        throw "invalid FAT extent for file ID $fileId`: start=$start end=$end"
    }
    $script:fat += [pscustomobject]@{
        file_id = $fileId
        start = $start
        end = $end
        size = [uint32]($end - $start)
    }
}

if ($arm9OverlayBytes.Length % 32 -ne 0) {
    throw "ARM9 overlay table size is not divisible by 32: $($arm9OverlayBytes.Length)"
}
$script:overlays = @()
for ($index = 0; $index -lt $arm9OverlayBytes.Length / 32; $index++) {
    $offset = $index * 32
    $overlayFileId = [uint32](Read-U32Le $arm9OverlayBytes ($offset + 24))
    if ($overlayFileId -ge $script:fat.Count) {
        throw "ARM9 overlay index $index references missing FAT file ID $overlayFileId"
    }
    $extent = $script:fat[$overlayFileId]
    $script:overlays += [pscustomobject]@{
        table_index = $index
        overlay_id = [uint32](Read-U32Le $arm9OverlayBytes $offset)
        ram_address = [uint32](Read-U32Le $arm9OverlayBytes ($offset + 4))
        ram_size = [uint32](Read-U32Le $arm9OverlayBytes ($offset + 8))
        bss_size = [uint32](Read-U32Le $arm9OverlayBytes ($offset + 12))
        static_init_start = [uint32](Read-U32Le $arm9OverlayBytes ($offset + 16))
        static_init_end = [uint32](Read-U32Le $arm9OverlayBytes ($offset + 20))
        file_id = $overlayFileId
        compressed_size_and_flags = [uint32](Read-U32Le $arm9OverlayBytes ($offset + 28))
        start = $extent.start
        end = $extent.end
        stored_size = $extent.size
    }
}

if ($script:fnt.Length -lt 8) {
    throw 'FNT is too short for the root directory entry'
}
$directoryCount = [int](Read-U16Le $script:fnt 6)
if ($directoryCount -lt 1 -or $directoryCount * 8 -gt $script:fnt.Length) {
    throw "invalid FNT directory count: $directoryCount"
}

$script:directories = @()
for ($index = 0; $index -lt $directoryCount; $index++) {
    $offset = $index * 8
    $subtableOffset = [uint32](Read-U32Le $script:fnt $offset)
    $firstFileId = [uint16](Read-U16Le $script:fnt ($offset + 4))
    $parent = [uint16](Read-U16Le $script:fnt ($offset + 6))
    if ($subtableOffset -lt $directoryCount * 8 -or $subtableOffset -ge $script:fnt.Length) {
        throw "invalid FNT subtable offset for directory index $index`: $subtableOffset"
    }
    $script:directories += [pscustomobject]@{
        directory_id = [uint16](0xF000 + $index)
        subtable_offset = $subtableOffset
        first_file_id = $firstFileId
        parent = $parent
    }
}

$script:files = [Collections.Generic.List[object]]::new()
$script:visitedDirectories = [Collections.Generic.HashSet[uint16]]::new()
$script:directoryPaths = @{}
$script:structuralErrors = [Collections.Generic.List[string]]::new()

function Read-Directory([uint16]$DirectoryId, [string]$Path) {
    $index = [int]$DirectoryId - 0xF000
    if ($index -lt 0 -or $index -ge $script:directories.Count) {
        $script:structuralErrors.Add("directory ID outside table: 0x$($DirectoryId.ToString('X4'))")
        return
    }
    if (-not $script:visitedDirectories.Add($DirectoryId)) {
        $script:structuralErrors.Add("directory referenced more than once or cycle: 0x$($DirectoryId.ToString('X4'))")
        return
    }

    $script:directoryPaths[$DirectoryId] = $Path
    $record = $script:directories[$index]
    $position = [int]$record.subtable_offset
    $nextFileId = [int]$record.first_file_id

    while ($true) {
        if ($position -ge $script:fnt.Length) {
            throw "unterminated FNT subtable for directory 0x$($DirectoryId.ToString('X4'))"
        }
        $typeAndLength = [int]$script:fnt[$position]
        $position++
        if ($typeAndLength -eq 0) {
            break
        }

        $isDirectory = ($typeAndLength -band 0x80) -ne 0
        $nameLength = $typeAndLength -band 0x7F
        if ($nameLength -eq 0) {
            throw "zero-length FNT name in directory 0x$($DirectoryId.ToString('X4'))"
        }
        $nameOffset = $position
        $name = Convert-Name $script:fnt $position $nameLength
        $nameHex = [BitConverter]::ToString([byte[]]$script:fnt[$nameOffset..($nameOffset + $nameLength - 1)]).Replace('-', '').ToLowerInvariant()
        $position += $nameLength
        $fullPath = if ([string]::IsNullOrEmpty($Path)) { $name } else { "$Path/$name" }

        if ($isDirectory) {
            $childId = [uint16](Read-U16Le $script:fnt $position)
            $position += 2
            Read-Directory $childId $fullPath
            continue
        }

        if ($nextFileId -ge $script:fat.Count) {
            throw "FNT references missing FAT file ID $nextFileId at $fullPath"
        }
        $extent = $script:fat[$nextFileId]
        $extension = [IO.Path]::GetExtension($name).ToLowerInvariant()
        if ([string]::IsNullOrEmpty($extension)) {
            $extension = '(none)'
        }
        $script:files.Add([pscustomobject]@{
            file_id = $nextFileId
            path = $fullPath
            name_hex = $nameHex
            extension = $extension
            start = $extent.start
            end = $extent.end
            size = $extent.size
        })
        $nextFileId++
    }
}

Read-Directory ([uint16]0xF000) ''

$fileIds = @($script:files | ForEach-Object { [int]$_.file_id })
$fileIdSet = [Collections.Generic.HashSet[int]]::new()
foreach ($id in $fileIds) {
    if (-not $fileIdSet.Add($id)) {
        $script:structuralErrors.Add("duplicate file ID in FNT: $id")
    }
}
$missingFileIds = @()
for ($id = 0; $id -lt $script:fat.Count; $id++) {
    if (-not $fileIdSet.Contains($id)) {
        $missingFileIds += $id
    }
}
$overlayFileIdSet = [Collections.Generic.HashSet[int]]::new()
foreach ($overlay in $script:overlays) {
    if (-not $overlayFileIdSet.Add([int]$overlay.file_id)) {
        $script:structuralErrors.Add("duplicate ARM9 overlay file ID: $($overlay.file_id)")
    }
}
$unclassifiedMissingFileIds = @($missingFileIds | Where-Object { -not $overlayFileIdSet.Contains([int]$_) })
$namedOverlayFileIds = @($fileIds | Where-Object { $overlayFileIdSet.Contains([int]$_) })

$pathSet = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($file in $script:files) {
    if (-not $pathSet.Add($file.path)) {
        $script:structuralErrors.Add("duplicate path in FNT: $($file.path)")
    }
}

$extentOverlaps = @()
$filePathById = @{}
foreach ($file in $script:files) {
    $filePathById[[int]$file.file_id] = $file.path
}
foreach ($overlay in $script:overlays) {
    if (-not $filePathById.ContainsKey([int]$overlay.file_id)) {
        $filePathById[[int]$overlay.file_id] = "<arm9-overlay:$($overlay.overlay_id)>"
    }
}
$nonEmptyExtents = @($script:fat | Where-Object { $_.size -gt 0 } | Sort-Object start, end, file_id)
for ($index = 1; $index -lt $nonEmptyExtents.Count; $index++) {
    $previous = $nonEmptyExtents[$index - 1]
    $current = $nonEmptyExtents[$index]
    if ([uint64]$current.start -lt [uint64]$previous.end) {
        $extentOverlaps += [ordered]@{
            first_file_id = $previous.file_id
            first_path = $filePathById[[int]$previous.file_id]
            second_file_id = $current.file_id
            second_path = $filePathById[[int]$current.file_id]
        }
    }
}

$extensionCounts = @($script:files |
    Group-Object extension |
    Sort-Object @{ Expression = 'Count'; Descending = $true }, @{ Expression = 'Name'; Descending = $false } |
    ForEach-Object {
        [ordered]@{ extension = $_.Name; count = $_.Count }
    })

$candidatePattern = '(?i)(font|glyph|moji|char|text|message|msg|script|scenario|event|talk|dialog|caption|title|menu|name|quest|item|help|tutorial|story|word|string)'
$nameCandidates = @($script:files |
    Where-Object { $_.path -match $candidatePattern } |
    Sort-Object path |
    Select-Object file_id, path, extension, start, size)

$largestFiles = @($script:files |
    Sort-Object size -Descending |
    Select-Object -First 30 file_id, path, extension, start, size)

$inventory = [ordered]@{
    schema_version = 1
    source = [ordered]@{
        profile_id = if ($null -ne $profile) { $profile.id } else { $null }
        profile_status = if ($null -ne $profile) { $profile.status } else { 'unregistered_survey' }
        size = $sourceItem.Length
        sha256 = $sha256
    }
    nitrofs = [ordered]@{
        fnt_offset = [uint32]$layout.fnt_offset
        fnt_size = [uint32]$layout.fnt_size
        fat_offset = [uint32]$layout.fat_offset
        fat_size = [uint32]$layout.fat_size
        directory_count = $directoryCount
        visited_directory_count = $script:visitedDirectories.Count
        fat_file_count = $script:fat.Count
        named_file_count = $script:files.Count
        missing_file_ids = $missingFileIds
        unclassified_missing_file_ids = $unclassifiedMissingFileIds
        named_overlay_file_ids = $namedOverlayFileIds
        extent_overlaps = $extentOverlaps
        structural_errors = @($script:structuralErrors)
    }
    arm9_overlays = $script:overlays
    extension_counts = $extensionCounts
    filename_candidates = $nameCandidates
    largest_files = $largestFiles
    files = @($script:files | Sort-Object file_id)
}

$outputItem = [IO.FileInfo]$OutputPath
if ($null -ne $outputItem.Directory -and -not $outputItem.Directory.Exists) {
    [void]$outputItem.Directory.Create()
}
$json = $inventory | ConvertTo-Json -Depth 10
[IO.File]::WriteAllText($outputItem.FullName, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

[ordered]@{
    output = $outputItem.FullName
    profile_id = if ($null -ne $profile) { $profile.id } else { $null }
    directory_count = $directoryCount
    visited_directory_count = $script:visitedDirectories.Count
    fat_file_count = $script:fat.Count
    named_file_count = $script:files.Count
    missing_file_id_count = $missingFileIds.Count
    arm9_overlay_count = $script:overlays.Count
    unclassified_missing_file_id_count = $unclassifiedMissingFileIds.Count
    named_overlay_file_id_count = $namedOverlayFileIds.Count
    extent_overlap_count = $extentOverlaps.Count
    structural_error_count = $script:structuralErrors.Count
    filename_candidate_count = $nameCandidates.Count
    top_extensions = @($extensionCounts | Select-Object -First 20)
} | ConvertTo-Json -Depth 6

if ($unclassifiedMissingFileIds.Count -ne 0 -or $extentOverlaps.Count -ne 0 -or $script:structuralErrors.Count -ne 0) {
    exit 4
}
