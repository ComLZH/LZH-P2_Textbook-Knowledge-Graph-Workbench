# setup_runtime.ps1 - LZH-P2 布局运行时环境配置脚本
# 用于下载并配置 ELK 布局计算所需的免安装 Node.js 执行体

[CmdletBinding()]
param(
    [switch]$Force,
    [string]$NodeVersion = "v22.16.0"
)

$ErrorActionPreference = 'Stop'

$scriptRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $scriptRoot
$vendorNodeDir = Join-Path $projectRoot "vendor\layout_runtime\node"
$targetExe = Join-Path $vendorNodeDir "node.exe"

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  LZH-P2 布局引擎运行时安装与配置助手" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan

# 1. 检查 vendor 目录下是否已存在 node.exe
if ((Test-Path -LiteralPath $targetExe) -and (-not $Force)) {
    Write-Host "[OK] 本地独立 Node.js 运行时已就绪: $targetExe" -ForegroundColor Green
    & $targetExe --version
    exit 0
}

# 2. 检查系统 PATH 中是否已存在 node
$systemNode = Get-Command "node.exe" -ErrorAction SilentlyContinue
if ($null -ne $systemNode -and (-not $Force)) {
    Write-Host "[提示] 检测到系统环境变量中已安装 Node.js: $($systemNode.Source)" -ForegroundColor Yellow
    Write-Host "P2 布局引擎已内置自动回退机制，将直接优先使用系统 Node.js。" -ForegroundColor Yellow
    Write-Host "若您仍需为本项目下载专属的独立便携版 node.exe，请执行: .\setup_runtime.ps1 -Force`n" -ForegroundColor Gray
    exit 0
}

# 3. 创建目标目录
if (-not (Test-Path -LiteralPath $vendorNodeDir)) {
    New-Item -ItemType Directory -Path $vendorNodeDir -Force | Out-Null
}

$zipUrl = "https://nodejs.org/dist/$NodeVersion/node-$NodeVersion-win-x64.zip"
$tempZip = Join-Path $env:TEMP "node-$NodeVersion-win-x64.zip"
$tempExtract = Join-Path $env:TEMP "node-$NodeVersion-extract"

Write-Host "[下载] 正在从 Node.js 官方下载便携式运行时 ($NodeVersion)..." -ForegroundColor Cyan
Write-Host "URL: $zipUrl" -ForegroundColor Gray

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    Invoke-WebRequest -Uri $zipUrl -OutFile $tempZip -UseBasicParsing
    
    Write-Host "[解压] 正在提取 node.exe 二进制文件..." -ForegroundColor Cyan
    if (Test-Path $tempExtract) {
        Remove-Item -Path $tempExtract -Recurse -Force
    }
    Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force
    
    $extractedExe = Join-Path $tempExtract "node-$NodeVersion-win-x64\node.exe"
    if (-not (Test-Path $extractedExe)) {
        throw "解压包中未找到 node.exe: $extractedExe"
    }
    
    Copy-Item -Path $extractedExe -Destination $targetExe -Force
    Write-Host "[成功] 已成功将 node.exe 配置至: $targetExe" -ForegroundColor Green
    & $targetExe --version
}
catch {
    Write-Host "[错误] 下载或解压 Node.js 运行时失败: $_" -ForegroundColor Red
    Write-Host "您也可以手动安装 Node.js (https://nodejs.org/) 并加入系统 PATH，本项目同样支持自动识别。" -ForegroundColor Yellow
    exit 1
}
finally {
    if (Test-Path $tempZip) { Remove-Item -Path $tempZip -Force -ErrorAction SilentlyContinue }
    if (Test-Path $tempExtract) { Remove-Item -Path $tempExtract -Recurse -Force -ErrorAction SilentlyContinue }
}
