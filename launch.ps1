<#
.SYNOPSIS
    相机网络配置工具 - 启动脚本（支持自动提权）
#>

# 需要管理员权限
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "正在请求管理员权限..." -ForegroundColor Yellow
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"" + $MyInvocation.MyCommand.Path + "`""
    Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments
    exit
}

Write-Host "=== 相机网络配置工具 ===" -ForegroundColor Cyan
Write-Host ""

# 尝试寻找 Python
$pythonCmd = $null
foreach ($cmd in @("python3.14", "python3", "python", "py")) {
    $path = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($path) {
        $pythonCmd = $cmd
        break
    }
}

if (-not $pythonCmd) {
    Write-Error "未找到 Python，请先安装 Python 3.8+"
    Read-Host "按 Enter 退出"
    exit 1
}

Write-Host "使用 $pythonCmd 启动工具..." -ForegroundColor Green
cd $PSScriptRoot
& $pythonCmd camera_network_tool.py

if ($LASTEXITCODE -ne 0) {
    Read-Host "按 Enter 退出"
}
