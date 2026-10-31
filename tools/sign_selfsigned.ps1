#requires -Version 5.1
<#
.SYNOPSIS
    用本机自签名的代码签名证书给仓库根的 pgassistant.exe 签名。

.DESCRIPTION
    流程：
      1. 在 Cert:\CurrentUser\My 找 Subject = CN=pgassistant (local dev) 的代码签名证书；
         没有就新建一张（有效期 5 年，私钥可导出）。
      2. 导出公钥证书到 data\dev-codesign.cer。
      3. 把证书导入当前用户的「受信任的根证书颁发机构」和「受信任的发布者」
         （Cert:\CurrentUser\Root / Cert:\CurrentUser\TrustedPublisher），
         范围是 CurrentUser，不需要管理员。
      4. 用 Set-AuthenticodeSignature 签名 pgassistant.exe，再用
         Get-AuthenticodeSignature 复核 Status 必须是 Valid。

    证书与私钥只在本机、当前 Windows 账户下有效；换机器要重新导入 .cer
    （只能验证已有签名，不能重新签名），重新签名需要私钥或重新生成证书。

    重新构建 exe 后文件哈希会变，必须重新运行本脚本签名。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# 管道 / 重定向时 Windows PowerShell 默认按控制台代码页（GBK）输出，中文会乱码；
# 统一成 UTF-8，和仓库其它脚本（launch.py / build_exe.py）一致。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root     = Split-Path -Parent $PSScriptRoot
$Exe      = Join-Path $Root 'pgassistant.exe'
$DataDir  = Join-Path $Root 'data'
$CerPath  = Join-Path $DataDir 'dev-codesign.cer'
$Subject  = 'CN=pgassistant (local dev)'
$MyStore  = 'Cert:\CurrentUser\My'

function Write-Step([string]$Message) {
    Write-Host "==> $Message"
}

function Write-Ok([string]$Message) {
    Write-Host "  ok  $Message"
}

function Write-Info([string]$Message) {
    Write-Host "  --  $Message"
}

function Write-Warn([string]$Message) {
    Write-Host "  !!  $Message" -ForegroundColor Yellow
}

function Test-CertInStore([string]$StorePath, [string]$Thumbprint) {
    $match = Get-ChildItem -Path $StorePath -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $Thumbprint }
    return [bool]$match
}

function Import-UserCertificate([string]$StoreName, [string]$Thumbprint) {
    $storePath = "Cert:\CurrentUser\$StoreName"
    if (Test-CertInStore -StorePath $storePath -Thumbprint $Thumbprint) {
        Write-Ok "证书已在 $storePath"
        return
    }
    # certutil 写当前用户存储；-user 是 CurrentUser 范围，不需要管理员。
    # 若系统弹出「安全警告 / 是否安装证书」的确认框，点「是」。
    $output = & certutil.exe -user -addstore -f $StoreName $CerPath 2>&1
    if (($LASTEXITCODE -ne 0) -or -not (Test-CertInStore -StorePath $storePath -Thumbprint $Thumbprint)) {
        throw "导入 $storePath 失败（certutil 退出码 $LASTEXITCODE）：$output"
    }
    Write-Ok "已导入 $storePath"
}

try {
    Write-Step "检查 pgassistant.exe"
    if (-not (Test-Path -LiteralPath $Exe)) {
        throw "找不到 $Exe；先运行 tools/build_exe.py 构建（见 docs/launcher-exe.md）。"
    }
    $exeInfo = Get-Item -LiteralPath $Exe
    Write-Ok "找到 $($exeInfo.FullName)（$([math]::Round($exeInfo.Length / 1MB, 1)) MiB）"

    Write-Step "查找代码签名证书（$Subject）"
    $cert = Get-ChildItem -Path $MyStore -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Subject -eq $Subject -and
            $_.HasPrivateKey -and
            $_.NotAfter -gt (Get-Date)
        } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1

    if ($cert) {
        Write-Ok "复用现有证书（Thumbprint $($cert.Thumbprint)，到期 $($cert.NotAfter.ToString('yyyy-MM-dd'))）"
    } else {
        Write-Info "没有可用证书，创建自签名代码签名证书（5 年）"
        $cert = New-SelfSignedCertificate `
            -Type CodeSigningCert `
            -Subject $Subject `
            -CertStoreLocation $MyStore `
            -NotAfter (Get-Date).AddYears(5) `
            -KeyExportPolicy Exportable `
            -KeyAlgorithm RSA `
            -KeyLength 2048 `
            -HashAlgorithm SHA256
        if (-not $cert -or -not $cert.HasPrivateKey) {
            throw "创建证书失败"
        }
        Write-Ok "已创建证书（Thumbprint $($cert.Thumbprint)，到期 $($cert.NotAfter.ToString('yyyy-MM-dd'))）"
    }

    Write-Step "导出公钥证书到 $CerPath"
    if (-not (Test-Path -LiteralPath $DataDir)) {
        New-Item -ItemType Directory -Path $DataDir | Out-Null
    }
    if (Test-Path -LiteralPath $CerPath) {
        Remove-Item -LiteralPath $CerPath -Force
    }
    Export-Certificate -Cert $cert -FilePath $CerPath -Type CERT | Out-Null
    Write-Ok "已导出 $CerPath（公钥，可安全分发；不含私钥）"

    Write-Step "导入当前用户信任存储（要信任这张证书才不会再弹警告）"
    Write-Info "如果 Windows 弹出「安全警告」确认框，请点「是」允许导入。"
    Import-UserCertificate -StoreName 'Root' -Thumbprint $cert.Thumbprint
    Import-UserCertificate -StoreName 'TrustedPublisher' -Thumbprint $cert.Thumbprint

    Write-Step "签名 pgassistant.exe"
    $signature = Set-AuthenticodeSignature -Certificate $cert -FilePath $Exe -HashAlgorithm SHA256
    if ($signature.Status -eq 'Valid') {
        Write-Ok "已写入 Authenticode 签名"
    } else {
        # 部分环境下签名后状态缓存未刷新，下面 Get-AuthenticodeSignature 以只读方式复核。
        Write-Info "Set-AuthenticodeSignature 返回 Status=$($signature.Status)，继续复核"
    }

    Write-Step "复核签名"
    $result = Get-AuthenticodeSignature -FilePath $Exe
    $signerSubject = if ($result.SignerCertificate) { $result.SignerCertificate.Subject } else { '(无)' }
    Write-Host "  Status              : $($result.Status)"
    Write-Host "  StatusMessage       : $($result.StatusMessage)"
    Write-Host "  SignerCertificate   : $signerSubject"
    Write-Host "  SignerThumbprint    : $(if ($result.SignerCertificate) { $result.SignerCertificate.Thumbprint } else { '(无)' })"
    Write-Host "  证书文件            : $CerPath"

    if ($result.Status -ne 'Valid') {
        throw "签名状态不是 Valid（实际 $($result.Status)）：$($result.StatusMessage)"
    }
    if ($result.SignerCertificate.Subject -ne $Subject) {
        throw "签名者 Subject 不是 $Subject（实际 $($result.SignerCertificate.Subject)）"
    }

    Write-Host ""
    Write-Ok "签名完成：$Exe"
    Write-Info "提示：重新构建 exe 后文件会变，需要重新运行本脚本签名。"
    exit 0
}
catch {
    Write-Host ""
    Write-Warn $_.Exception.Message
    exit 1
}
