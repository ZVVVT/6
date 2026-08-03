[CmdletBinding()]
param(
    [ValidatePattern('^\d{8}$')]
    [string]$BuildDate = '20260803',
    [string]$BuildRoot = '',
    [string]$OutputRoot = '',
    [string]$PythonExe = '',
    [string]$PipIndexUrl = 'https://pypi.tuna.tsinghua.edu.cn/simple'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Initialize-PipNetwork([string]$IndexUrl) {
    try {
        $uri = [System.Uri]$IndexUrl
    }
    catch {
        throw "无效的 pip 镜像地址：$IndexUrl"
    }

    if (-not $uri.IsAbsoluteUri -or [string]::IsNullOrWhiteSpace($uri.Host)) {
        throw "无效的 pip 镜像地址：$IndexUrl"
    }

    $hostName = $uri.Host
    $bypassItems = @(
        '127.0.0.1',
        'localhost',
        $hostName,
        ".$hostName"
    )

    $existingBypass = @()
    foreach ($value in @($env:NO_PROXY, $env:no_proxy)) {
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $existingBypass += $value.Split(',') |
                ForEach-Object { $_.Trim() } |
                Where-Object { $_ }
        }
    }

    $mergedBypass = @($existingBypass + $bypassItems) |
        Select-Object -Unique

    $env:NO_PROXY = $mergedBypass -join ','
    $env:no_proxy = $env:NO_PROXY

    Remove-Item Env:PIP_PROXY -ErrorAction SilentlyContinue
    Remove-Item Env:pip_proxy -ErrorAction SilentlyContinue

    # 禁止 pip 读取用户或系统级 pip.ini，所有关键参数由本脚本显式传入。
    $env:PIP_CONFIG_FILE = 'NUL'
    $env:PIP_INDEX_URL = $IndexUrl
    $env:PIP_TRUSTED_HOST = $hostName

    return $hostName
}

$PipTrustedHost = Initialize-PipNetwork $PipIndexUrl

if ([string]::IsNullOrWhiteSpace($BuildRoot)) {
    $BuildRoot = "F:\sperm_protein_analyzer_pack_$BuildDate"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = "F:\sperm_protein_analyzer_Test_$BuildDate"
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir '..\..')).TrimEnd('\')
$BuildRoot = [System.IO.Path]::GetFullPath($BuildRoot).TrimEnd('\')
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')

function Test-IsSameOrChild([string]$Path, [string]$Parent) {
    $pathFull = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\')
    return (
        $pathFull.Equals($parentFull, [System.StringComparison]::OrdinalIgnoreCase) -or
        $pathFull.StartsWith(
            $parentFull + '\',
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
}

function Assert-SafeTarget([string]$Path, [string]$Kind) {
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $root = [System.IO.Path]::GetPathRoot($full).TrimEnd('\')

    if ($full.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Kind 不能是磁盘根目录：$full"
    }
    if (Test-IsSameOrChild $full $RepoRoot) {
        throw "$Kind 不能位于主源码目录内：$full"
    }

    $ProtectedRoots = @(
        'F:\sperm_protein_analyzer_pack_20260709',
        'F:\sperm_protein_analyzer_Test_20260709',
        'F:\MvImageID',
        'F:\v1'
    )
    foreach ($protected in $ProtectedRoots) {
        if (Test-IsSameOrChild $full $protected) {
            throw "$Kind 指向受保护目录：$full"
        }
    }

    if ($full -match '(?i)\\sperm_protein_analyzer_cleanup_quarantine[^\\]*(?:\\|$)' -or
        $full -match '(?i)\\py38_fix_backups_[^\\]*(?:\\|$)') {
        throw "$Kind 指向隔离或项目外备份目录：$full"
    }
}

function Invoke-Git([string[]]$Arguments) {
    & git -c "safe.directory=$($RepoRoot.Replace('\','/'))" -C $RepoRoot @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git 命令失败：git $($Arguments -join ' ')"
    }
}

function Copy-RequiredFile(
    [string]$RelativePath,
    [string]$SourceRoot,
    [string]$DestinationRoot
) {
    $source = Join-Path $SourceRoot $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "正式 tools 白名单文件缺失：$RelativePath"
    }

    $destination = Join-Path $DestinationRoot $RelativePath
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
}

Assert-SafeTarget $BuildRoot '构建目录'
Assert-SafeTarget $OutputRoot '成品目录'

if ($BuildRoot.Equals($OutputRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '构建目录与成品目录不能相同。'
}
if ((Test-IsSameOrChild $BuildRoot $OutputRoot) -or
    (Test-IsSameOrChild $OutputRoot $BuildRoot)) {
    throw '构建目录与成品目录不能互相包含。'
}
if (Test-Path -LiteralPath $BuildRoot) {
    throw "构建目录已存在，拒绝覆盖：$BuildRoot"
}
if (Test-Path -LiteralPath $OutputRoot) {
    throw "成品目录已存在，拒绝覆盖：$OutputRoot"
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = Join-Path $RepoRoot '.venv\Scripts\python.exe'
}
$PythonExe = [System.IO.Path]::GetFullPath($PythonExe)

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "未找到 Python 解释器：$PythonExe"
}

$PythonVersion = (& $PythonExe -c "import platform; print(platform.python_version())").Trim()
if ($LASTEXITCODE -ne 0 -or $PythonVersion -ne '3.8.3') {
    throw "需要 Python 3.8.3，当前检测结果：$PythonVersion"
}

$Status = (& git -c "safe.directory=$($RepoRoot.Replace('\','/'))" -C $RepoRoot status --porcelain=v1)
if ($LASTEXITCODE -ne 0) {
    throw '无法检查 Git 工作区状态。'
}
if ($Status) {
    throw 'Git 工作区不是 clean，停止打包。'
}

$Branch = (& git -c "safe.directory=$($RepoRoot.Replace('\','/'))" -C $RepoRoot branch --show-current).Trim()
$Commit = (& git -c "safe.directory=$($RepoRoot.Replace('\','/'))" -C $RepoRoot rev-parse HEAD).Trim()

$ToolsWhitelist = @(
    'tools\analysis_v2\direct_cellpose_worker.py',
    'tools\analysis_v2\tail_joint_chain_candidate_mvp.py',
    'tools\analysis_v2\tail_joint_draft_editor_launcher_mvp.py',
    'tools\analysis_v2\tail_joint_draft_export_mvp.py',
    'tools\analysis_v2\tail_joint_final_candidate_export_mvp.py',
    'tools\analysis_v2\tail_joint_oneclick_v2.py',
    'tools\analysis_v2\tail_joint_promote_measure_v2.py',
    'tools\analysis_v2\tail_joint_promotion_staging_mvp.py',
    'tools\analysis_v2\tail_joint_refine_candidate_mvp.py',
    'tools\analysis_v2\tail_joint_region_preview_mvp.py',
    'tools\analysis_v2\tail_joint_start_candidate_mvp.py',
    'tools\analysis_v2\tail_legacy\tail_graph_stage1_extract.py',
    'tools\analysis_v2\tail_legacy\tail_graph_stage1_1_topology_clean.py',
    'tools\analysis_v2\tail_legacy\tail_graph_stage1_2_build_graph.py',
    'tools\analysis_v2\tail_legacy\tail_result_editor_v2_3_draft_mvp.py'
)

foreach ($item in $ToolsWhitelist) {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $item) -PathType Leaf)) {
        throw "当前工作树中的正式 tools 白名单文件缺失：$item"
    }
}

New-Item -ItemType Directory -Path $BuildRoot | Out-Null
$TranscriptPath = Join-Path $BuildRoot 'build.log'
$TranscriptStarted = $false
$BuildSucceeded = $false

try {
    Start-Transcript -LiteralPath $TranscriptPath | Out-Null
    $TranscriptStarted = $true

    $SourceRoot = Join-Path $BuildRoot 'source'
    $ArchivePath = Join-Path $BuildRoot 'source.zip'
    New-Item -ItemType Directory -Path $SourceRoot | Out-Null

    $ArchiveArgs = @(
        'archive',
        '--format=zip',
        "--output=$ArchivePath",
        'HEAD',
        '--',
        '.',
        ':(exclude)data/**',
        ':(exclude)reports/**',
        ':(exclude)workspace/**',
        ':(exclude)**/__pycache__/**',
        ':(exclude)**/*.pyc',
        ':(exclude)**/*.cppipe1'
    )
    Invoke-Git $ArchiveArgs
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $SourceRoot

    $RequiredPackagingFiles = @(
        'packaging\windows\SpermProteinAnalyzer.spec',
        'packaging\windows\requirements-build.txt',
        'config.ini',
        'pipeline_params.ini',
        'main.py'
    )
    foreach ($relative in $RequiredPackagingFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot $relative) -PathType Leaf)) {
            throw "Git 导出副本缺少必要文件：$relative"
        }
    }
    foreach ($item in $ToolsWhitelist) {
        if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot $item) -PathType Leaf)) {
            throw "Git HEAD 导出副本缺少正式 tools 文件：$item"
        }
    }

    $VenvRoot = Join-Path $BuildRoot '.venv_build'
    & $PythonExe -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) {
        throw '创建独立构建环境失败。'
    }

    $BuildPython = Join-Path $VenvRoot 'Scripts\python.exe'
    $BuildPythonVersion = (& $BuildPython -c "import platform; print(platform.python_version())").Trim()
    if ($LASTEXITCODE -ne 0 -or $BuildPythonVersion -ne '3.8.3') {
        throw "独立构建环境版本异常：$BuildPythonVersion"
    }

    & $BuildPython -m pip install `
        --disable-pip-version-check `
        --index-url $PipIndexUrl `
        --trusted-host $PipTrustedHost `
        --no-cache-dir `
        'pip==24.3.1'
    if ($LASTEXITCODE -ne 0) {
        throw '升级独立构建环境 pip 失败。'
    }

    $Requirements = Join-Path $SourceRoot 'packaging\windows\requirements-build.txt'
    & $BuildPython -m pip install `
        --disable-pip-version-check `
        --index-url $PipIndexUrl `
        --trusted-host $PipTrustedHost `
        --only-binary=:all: `
        --requirement $Requirements
    if ($LASTEXITCODE -ne 0) {
        throw '安装构建依赖失败。'
    }

    & $BuildPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw '构建环境依赖一致性检查失败。'
    }

    $ImportSmoke = @'
import importlib

modules = [
    "PyInstaller",
    "PySide6",
    "numpy",
    "pandas",
    "openpyxl",
    "PIL",
    "reportlab",
    "cv2",
    "tifffile",
]

for name in modules:
    module = importlib.import_module(name)
    version = getattr(module, "__version__", "unknown")
    print(f"{name}={version}")
'@
    $ImportSmokePath = Join-Path $BuildRoot 'verify_build_imports.py'
    $Utf8NoBomForChecks = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        $ImportSmokePath,
        $ImportSmoke,
        $Utf8NoBomForChecks
    )
    & $BuildPython $ImportSmokePath
    if ($LASTEXITCODE -ne 0) {
        throw '构建环境关键模块导入检查失败。'
    }

    $EnvironmentFile = Join-Path $BuildRoot 'build_environment.txt'
    & $BuildPython -m pip freeze |
        Set-Content -LiteralPath $EnvironmentFile -Encoding UTF8

    $DistRoot = Join-Path $BuildRoot 'dist'
    $WorkRoot = Join-Path $BuildRoot 'pyinstaller-work'
    $SpecPath = Join-Path $SourceRoot 'packaging\windows\SpermProteinAnalyzer.spec'

    Push-Location $SourceRoot
    try {
        & $BuildPython -m PyInstaller `
            --noconfirm `
            --clean `
            --distpath $DistRoot `
            --workpath $WorkRoot `
            $SpecPath

        if ($LASTEXITCODE -ne 0) {
            throw 'PyInstaller 构建失败。'
        }
    }
    finally {
        Pop-Location
    }

    $OnedirRoot = Join-Path $DistRoot 'SpermProteinAnalyzer'
    $ExePath = Join-Path $OnedirRoot 'SpermProteinAnalyzer.exe'
    $InternalPath = Join-Path $OnedirRoot '_internal'

    if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
        throw '未生成 SpermProteinAnalyzer.exe。'
    }
    if (-not (Test-Path -LiteralPath $InternalPath -PathType Container)) {
        throw '未生成 _internal 目录。'
    }

    New-Item -ItemType Directory -Path $OutputRoot | Out-Null

    Get-ChildItem -LiteralPath $OnedirRoot -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $OutputRoot -Recurse
    }

    Copy-Item -LiteralPath (Join-Path $SourceRoot 'assets') -Destination $OutputRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $SourceRoot 'pipelines') -Destination $OutputRoot -Recurse
    # 外置 core 仅供 MvImageID 的独立 Python 子进程导入。来源必须与
    # pipelines/tools 一样是 git archive 生成的同一份干净源码。
    Copy-Item -LiteralPath (Join-Path $SourceRoot 'core') -Destination $OutputRoot -Recurse

    foreach ($item in $ToolsWhitelist) {
        Copy-RequiredFile $item $SourceRoot $OutputRoot
    }

    Copy-Item -LiteralPath (Join-Path $SourceRoot 'config.ini') -Destination $OutputRoot
    Copy-Item -LiteralPath (Join-Path $SourceRoot 'pipeline_params.ini') -Destination $OutputRoot

    $ConfigPath = Join-Path $OutputRoot 'config.ini'
    $ConfigText = [System.IO.File]::ReadAllText($ConfigPath)

    if ($ConfigText -notmatch '(?m)^\[AnalysisV2\]\s*$') {
        throw 'config.ini 缺少 [AnalysisV2]，拒绝生成不完整配置。'
    }

    $AnalysisV2Match = [regex]::Match(
        $ConfigText,
        '(?ms)^\[AnalysisV2\]\s*\r?\n.*?(?=^\[|\z)'
    )
    if (-not $AnalysisV2Match.Success) {
        throw '无法解析 config.ini 的 [AnalysisV2]。'
    }

    $AnalysisV2Section = $AnalysisV2Match.Value
    if ($AnalysisV2Section -match '(?m)^enabled\s*=') {
        $AnalysisV2Section = [regex]::Replace(
            $AnalysisV2Section,
            '(?m)^(enabled\s*=\s*)[^\r\n]*',
            '${1}true'
        )
    }
    else {
        $AnalysisV2Section = $AnalysisV2Section -replace `
            '(^\[AnalysisV2\]\s*\r?\n)', `
            "`$1enabled = true`r`n"
    }

    $ConfigText = (
        $ConfigText.Substring(0, $AnalysisV2Match.Index) +
        $AnalysisV2Section +
        $ConfigText.Substring(
            $AnalysisV2Match.Index + $AnalysisV2Match.Length
        )
    )

    $RequiredConfigValues = @(
        'source_project_dir = F:\MvImageID',
        'plugins_directory = F:\MvImageID\C-plugins\active_plugins',
        'python_exe = F:\MvImageID\.venv\Scripts\python.exe',
        'module_name = MvImageID'
    )
    foreach ($value in $RequiredConfigValues) {
        if ($ConfigText -notmatch [regex]::Escape($value)) {
            throw "config.ini 缺少必要配置：$value"
        }
    }

    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ConfigPath, $ConfigText, $Utf8NoBom)

    $ConfigBytes = [System.IO.File]::ReadAllBytes($ConfigPath)
    if ($ConfigBytes.Length -ge 3 -and
        $ConfigBytes[0] -eq 0xEF -and
        $ConfigBytes[1] -eq 0xBB -and
        $ConfigBytes[2] -eq 0xBF) {
        throw 'config.ini 仍含 UTF-8 BOM。'
    }

    $ConfigCheck = @'
import configparser
import sys

parser = configparser.ConfigParser()
loaded = parser.read(sys.argv[1], encoding="utf-8")
if not loaded:
    raise SystemExit("config.ini 无法读取")
if not parser.getboolean("AnalysisV2", "enabled"):
    raise SystemExit("AnalysisV2 未启用")
print("config.ini 检查通过")
'@
    $ConfigCheckPath = Join-Path $BuildRoot 'verify_config.py'
    [System.IO.File]::WriteAllText(
        $ConfigCheckPath,
        $ConfigCheck,
        $Utf8NoBom
    )
    & $BuildPython $ConfigCheckPath $ConfigPath
    if ($LASTEXITCODE -ne 0) {
        throw '成品 config.ini 解析检查失败。'
    }

    $RequiredExternalRuntimePaths = @(
        'core',
        'core\analysis_v2\tail_measurement_service.py',
        'tools\analysis_v2\tail_joint_promote_measure_v2.py'
    )
    foreach ($relative in $RequiredExternalRuntimePaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $OutputRoot $relative))) {
            throw "成品缺少 MvImageID Python 运行时路径：$relative"
        }
    }

    $ForbiddenCoreFiles = Get-ChildItem -LiteralPath (Join-Path $OutputRoot 'core') `
        -Recurse -Force |
        Where-Object {
            $_.Name -eq '__pycache__' -or
            $_.Extension -eq '.pyc'
        }
    if ($ForbiddenCoreFiles) {
        throw "外置 core 含缓存文件：$($ForbiddenCoreFiles.FullName -join ', ')"
    }

    $ExternalRuntimeCheck = @'
import configparser
import importlib
import sys
from pathlib import Path

product_root = Path(sys.argv[1]).resolve()
config_path = product_root / "config.ini"
parser = configparser.ConfigParser()
if not parser.read(str(config_path), encoding="utf-8"):
    raise SystemExit("无法读取成品 config.ini：{}".format(config_path))

sys.path.insert(0, str(product_root))
from core.analysis_v2.tail_measurement_service import TailMeasurementService

module = importlib.import_module(
    "tools.analysis_v2.tail_joint_promote_measure_v2"
)
resolved_root = module.find_project_root(
    product_root / "tools" / "analysis_v2" /
    "tail_joint_promote_measure_v2.py",
    str(product_root),
)
if resolved_root != product_root:
    raise SystemExit(
        "find_project_root 返回异常：{} != {}".format(
            resolved_root, product_root
        )
    )
print("MvImageID Python 外置 core/tools 导入检查通过：{}".format(product_root))
'@
    $ExternalRuntimeCheckPath = Join-Path $BuildRoot 'verify_external_runtime.py'
    [System.IO.File]::WriteAllText(
        $ExternalRuntimeCheckPath,
        $ExternalRuntimeCheck,
        $Utf8NoBom
    )

    $RuntimeConfigParser = @'
import configparser
import sys

parser = configparser.ConfigParser()
if not parser.read(sys.argv[1], encoding="utf-8"):
    raise SystemExit("无法读取成品 config.ini")
value = parser.get("MvImageID", "python_exe", fallback="").strip()
if not value:
    raise SystemExit("config.ini 缺少 [MvImageID] python_exe")
print(value)
'@
    $RuntimeConfigParserPath = Join-Path $BuildRoot 'read_mvimageid_python.py'
    [System.IO.File]::WriteAllText(
        $RuntimeConfigParserPath,
        $RuntimeConfigParser,
        $Utf8NoBom
    )
    $MvImageIDPython = (& $BuildPython $RuntimeConfigParserPath $ConfigPath).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($MvImageIDPython)) {
        throw '无法从成品 config.ini 读取 MvImageID Python。'
    }
    if (-not (Test-Path -LiteralPath $MvImageIDPython -PathType Leaf)) {
        throw "成品 config.ini 配置的 MvImageID Python 不存在：$MvImageIDPython"
    }
    & $MvImageIDPython $ExternalRuntimeCheckPath $OutputRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'MvImageID Python 无法导入成品外置 core/tools。'
    }

    foreach ($emptyDir in @('data', 'reports', 'workspace')) {
        New-Item -ItemType Directory -Path (Join-Path $OutputRoot $emptyDir) |
            Out-Null
    }

    foreach ($emptyDir in @('data', 'reports', 'workspace')) {
        $target = Join-Path $OutputRoot $emptyDir
        if (Get-ChildItem -LiteralPath $target -Force) {
            throw "$emptyDir 目录不是空目录。"
        }
    }

    $PipelineBackups = Get-ChildItem -LiteralPath $OutputRoot -Recurse -File |
        Where-Object { $_.Name -like '*.cppipe1' }
    if ($PipelineBackups) {
        throw "成品误含 cppipe1 备份：$($PipelineBackups.FullName -join ', ')"
    }

    foreach ($item in $ToolsWhitelist) {
        if (-not (Test-Path -LiteralPath (Join-Path $OutputRoot $item) -PathType Leaf)) {
            throw "成品缺少正式 tools 文件：$item"
        }
    }

    $ExcludedTool = Join-Path $OutputRoot 'tools\analysis_v2\tail_corner_guard_replay_v1.py'
    if (Test-Path -LiteralPath $ExcludedTool) {
        throw '成品误含排除的 tail_corner_guard_replay_v1.py。'
    }

    $RequiredOutputPaths = @(
        'SpermProteinAnalyzer.exe',
        '_internal',
        'assets',
        'core',
        'pipelines',
        'tools\analysis_v2',
        'config.ini',
        'pipeline_params.ini',
        'data',
        'reports',
        'workspace'
    )
    foreach ($relative in $RequiredOutputPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $OutputRoot $relative))) {
            throw "成品缺少必要路径：$relative"
        }
    }

    $CommitText = "branch=$Branch`r`ncommit=$Commit`r`n"
    [System.IO.File]::WriteAllText(
        (Join-Path $OutputRoot 'git_source_commit.txt'),
        $CommitText,
        $Utf8NoBom
    )

    Copy-Item -LiteralPath $EnvironmentFile `
        -Destination (Join-Path $OutputRoot 'build_environment.txt')

    $ManifestLines = Get-ChildItem -LiteralPath $OutputRoot -Recurse -File |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($OutputRoot.Length).TrimStart('\')
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            "$hash  $relative"
        }

    [System.IO.File]::WriteAllLines(
        (Join-Path $OutputRoot 'build_manifest.txt'),
        $ManifestLines,
        $Utf8NoBom
    )

    $BuildSucceeded = $true
}
finally {
    if ($TranscriptStarted) {
        try {
            Stop-Transcript | Out-Null
        }
        catch {
            Write-Warning "停止构建日志记录失败：$($_.Exception.Message)"
        }
    }
}

if ($BuildSucceeded) {
    Write-Host "构建完成：$OutputRoot"
}
