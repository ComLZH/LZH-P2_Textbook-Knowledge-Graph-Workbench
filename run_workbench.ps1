# run_workbench.ps1 - LZH-P2 教材知识图谱本地工作台启动脚本

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$workbenchScript = Join-Path $projectRoot 'run_workbench.py'

if (-not (Test-Path -LiteralPath $workbenchScript)) {
    Write-Host "错误: 未找到启动入口文件: $workbenchScript" -ForegroundColor Red
    exit 1
}

$pythonExe = $null
$pythonArgs = @()

# 候选解释器列表
$candidatePythons = @(
    # 当前激活环境
    $(if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe' }),
    $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX 'python.exe' }),
    # 本机常用 Conda 环境
    'D:\Anaconda\anaconda3\envs\K12_Test_Automation_Program\python.exe',
    'C:\Anaconda3\envs\K12_Test_Automation_Program\python.exe',
    # 系统 PATH 中的 python
    $(try { (Get-Command 'python.exe' -ErrorAction Stop).Source } catch { $null }),
    # py launcher
    $(try { (Get-Command 'py.exe' -ErrorAction Stop).Source } catch { $null })
)

foreach ($cand in $candidatePythons) {
    if ($null -ne $cand -and (Test-Path -LiteralPath $cand)) {
        # 测试该解释器是否能正常运行并包含 PySide6
        $hasPySide = & $cand -c "import PySide6; print('OK')" 2>$null
        if ($hasPySide -match 'OK') {
            $pythonExe = $cand
            break
        }
    }
}

# 若上述未命中包含 PySide6 的环境，再回退查找任意可用 Python
if ($null -eq $pythonExe) {
    foreach ($cand in $candidatePythons) {
        if ($null -ne $cand -and (Test-Path -LiteralPath $cand)) {
            $testResult = & $cand -c "import sys; sys.exit(0)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $pythonExe = $cand
                break
            }
        }
    }
}

if ($null -eq $pythonExe) {
    Write-Host "================================================================" -ForegroundColor Red
    Write-Host "错误: 未能在系统中检测到可用的 Python 3.10+ 解释器" -ForegroundColor Red
    Write-Host "请确保已安装 Python 并已将其添加至系统环境变量 PATH" -ForegroundColor Yellow
    Write-Host "推荐使用 Python 3.10 或 3.11 环境，并运行:" -ForegroundColor Yellow
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Red
    exit 1
}

Write-Host "----------------------------------------------------" -ForegroundColor Gray
Write-Host "正在启动 LZH-P2 教材知识图谱本地工作台..." -ForegroundColor Cyan
Write-Host "Python 解释器: $pythonExe" -ForegroundColor Gray
Write-Host "----------------------------------------------------`n" -ForegroundColor Gray

& $pythonExe @pythonArgs $workbenchScript @args
exit $LASTEXITCODE