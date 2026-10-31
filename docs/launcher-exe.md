# 单文件签名 exe（pgassistant.exe）

`pgassistant.exe` 是本机可选的双击入口：它把 `launch.py` 和一个 Python 运行时打进
单个 exe，再用本机自签名的代码签名证书签名。签名生效后，Windows 能读出发布者
`pgassistant (local dev)`，不再显示「发布者: 无法验证」。如果签名已生效但仍弹安全警告，
说明拦截来自安全软件或组策略，按第 8 节加白名单（签名只解决发布者身份，不能绕过拦截策略）。

> 它仍然是**启动器**，不是独立应用：后端、前端、内容、数据库和 `.venv` 都还在仓库里。
> exe 必须放在**仓库根**（和 `launch.py`、`backend/`、`frontend/`、`content/` 同层），
> 不能单独拷到别处运行。

---

## 1. 为什么需要 exe

`start.cmd` 双击时会弹「打开文件 - 安全警告 / 发布者: 无法验证」。已确认该文件
**没有**网络标记（Zone.Identifier），所以弹窗来自本机安全软件或系统策略，而不是
「从网上下载」的 Attachment Manager。

- `.cmd` / `.bat` 是交给 `cmd.exe` 解释的脚本，**不能被 Authenticode 有效签名**：
  可以给文件附加签名，但 `cmd.exe` 加载时不会校验，系统也不拿它当发布者凭据。
- 只有 PE 文件（`.exe` / `.dll`）的 Authenticode 签名会在启动时被校验。
- 因此走「把 `launch.py` 打成 exe → 用本机信任的代码签名证书签名」这条路。

自签名证书只在**本机、当前 Windows 账户**下被信任；它不是公开 CA 证书，不能拿去分发。
见第 5 节限制。

---

## 2. 前置条件

- Python 3.11+（本仓库的 `.venv`）与 Node 20+（`launch.py` 启动前端要用）。
- PyInstaller 只作为**构建工具**装进 `.venv`，不进 `backend/requirements*.txt`：

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
```

签名只用 Windows 自带的 PowerShell 5.1（`Set-AuthenticodeSignature`），不需要
Windows SDK、不需要 `signtool.exe`、也不需要管理员权限。

---

## 3. 构建

在仓库根执行：

```powershell
.venv\Scripts\python.exe tools\build_exe.py
```

脚本做的事：

1. 删除旧的 `pgassistant.exe`、`build/` 和仓库根的 `*.spec`（可重复运行）；
2. 用当前 `.venv` 的 PyInstaller 构建 `--onefile --console`：
   - `--onefile`：单文件；`--console`：双击后保留控制台，能看到启动日志和报错；
   - `--distpath` 指向仓库根，产出 `pgassistant.exe`（约 8–9 MiB）；
   - 中间产物放进 `build/`，`.spec` 也放 `build/`。
3. 构建失败时打印 PyInstaller 的退出码并非 0 退出；成功后打印 exe 大小。

为什么输出到仓库根而不是 `dist/`：PyInstaller onefile 运行时，`launch.py` 的
`__file__` 指向 `%TEMP%\_MEIxxxx` 临时解包目录，不能用它推仓库根。`launch.py`
在 `sys.frozen` 为真时改用 `Path(sys.executable).parent`，也就是 exe 所在目录。
exe 放在仓库根，`backend/`、`frontend/`、`content/`、`data/`、`.venv/` 就都能找到。

构建产物不入库，`.gitignore` 已忽略：

```
pgassistant.exe
build/
*.spec
```

（`dist/` 原本就忽略。）

---

## 4. 签名

exe 构建完成后，在仓库根执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\sign_selfsigned.ps1
```

脚本做的事：

1. 在 `Cert:\CurrentUser\My` 找 `Subject = CN=pgassistant (local dev)`、带私钥且未过期的
   代码签名证书；没有就新建一张（`New-SelfSignedCertificate -Type CodeSigningCert`，RSA 2048，
   有效期 5 年，私钥可导出）。
2. 把公钥证书导出到 `data\dev-codesign.cer`（`data/` 已 gitignore，便于以后复用/分发公钥）。
3. 把证书导入当前用户的 `Cert:\CurrentUser\Root`（受信任的根证书颁发机构）和
   `Cert:\CurrentUser\TrustedPublisher`（受信任的发布者）。范围是 **CurrentUser**，
   不需要管理员。导入根证书时 Windows 可能弹一次「安全警告」确认框，点「是」即可；
   本脚本用 `certutil -user -addstore` 静默写入，通常在无弹窗的情况下完成。
4. 用 `Set-AuthenticodeSignature -HashAlgorithm SHA256` 给 `pgassistant.exe` 签名。
5. 用 `Get-AuthenticodeSignature` 复核，`Status` 必须是 `Valid`、
   `SignerCertificate.Subject` 必须是 `CN=pgassistant (local dev)`，否则以非 0 退出。

成功时输出类似：

```
  Status              : Valid
  StatusMessage       : 签名已通过验证。
  SignerCertificate   : CN=pgassistant (local dev)
  SignerThumbprint    : <本机证书指纹>
  证书文件            : <仓库根>\data\dev-codesign.cer
```

**每次重新构建 exe 后都必须重新签名**：`build_exe.py` 会先删掉旧文件再重新生成，
新文件里没有签名块，`Get-AuthenticodeSignature` 显示 `NotSigned`；只有已签名的文件被
**原地**改写时才会显示 `HashMismatch`。两种情况结论相同——重建或改动后必须重签。

---

## 5. 证书只在本机有效

- 私钥只存在当前用户证书库 `Cert:\CurrentUser\My`，受 Windows 用户配置保护；
  导出的 `.cer` 只有公钥，**不含私钥**。
- 信任只写进当前用户的两个存储（`Root` / `TrustedPublisher`），不是全机器范围，
  也不影响其他 Windows 账户。
- 换一台机器（或换一个 Windows 账户）后，同一个 exe 的签名会显示
  `UnknownError` / `NotTrusted`，发布者仍可能是「无法验证」，SmartScreen 也可能继续拦截。
  自签名证书不能替代公开 CA，**不要**用它给对外分发的安装包签名。

### 换机器怎么处理

| 目的 | 做法 |
|---|---|
| 只让已有的 exe 在新机器通过校验 | 把 `data\dev-codesign.cer` 拷过去，导入该用户的 `Root` 与 `TrustedPublisher`（见下方命令） |
| 在新机器重新签名 / 重新构建 | 新机器上重新跑 `tools\sign_selfsigned.ps1`，会生成新证书（Subject 相同、指纹不同）并重签 |
| 继续用原证书签名 | 需要带私钥的 `.pfx`（`Export-PfxCertificate`，要设密码），安全风险高，不建议 |

在新机器导入公钥证书：

```powershell
certutil -user -addstore -f Root data\dev-codesign.cer
certutil -user -addstore -f TrustedPublisher data\dev-codesign.cer
```

注意：`.cer` 只包含公钥，导入后只能**验证**已经签好的 exe，不能用来给新 exe 签名。

### 有效期

证书 5 年有效（自脚本创建证书时算起）。脚本没有加时间戳服务器，
签名有效期跟证书绑定；证书过期后重新运行签名脚本即可（脚本会因证书过期而新建一张）。
`Get-AuthenticodeSignature` 在证书过期后可能显示 `NotTimeValid`，不是签名坏了。

---

## 6. 运行与验证

```powershell
# 只看将要做什么，不产生副作用；输出里的路径应指向仓库根
.\pgassistant.exe --dry-run

# 正常启动（等价于 start.cmd）
.\pgassistant.exe

# 停止（参数与 launch.py 完全一致）
.\pgassistant.exe stop
.\pgassistant.exe stop --dry-run
.\pgassistant.exe --help
```

`launch.py` 的所有参数/子命令都可用：`--no-browser`、`--dev`、`--port <n>`、
`--runtime-file <path>`、`--dry-run`、`stop`。参数由 exe 原样转发。

签名验证：

```powershell
Get-AuthenticodeSignature .\pgassistant.exe |
    Select-Object Status, @{n='Subject';e={$_.SignerCertificate.Subject}}
```

应为 `Valid` / `CN=pgassistant (local dev)`。

> 首次运行 onefile exe 会先解包到 `%TEMP%`，比直接跑 Python 慢 1–2 秒；
> 解包目录在进程退出后自动删除。

---

## 7. 杀毒软件误报

PyInstaller 的 onefile 产物是「自解压 + 内嵌 Python 运行时」结构，启发式引擎经常
把它判成可疑/木马（尤其没签名、刚从网上下载时）。可以：

- 在 Windows Defender / 第三方安全软件里给**仓库目录**或 `pgassistant.exe` 加排除项；
- 先在本机自行构建、不要从别处下载 exe；
- 需要排查时把文件哈希提交 VirusTotal 等平台确认；
- 不建议为此整体关闭实时防护。

> 如果第一次构建出来的 exe 被直接隔离，先加排除再重新运行 `tools\build_exe.py`。

---

## 8. 不想装证书，也想「少一次提示」

签名不是唯一的路；按场景选：

1. **从终端运行 `start.cmd`**：在已打开的 cmd / PowerShell 里执行 `start.cmd`
   不会经过 Explorer 的「打开文件 - 安全警告」路径，通常直接起服务。
2. **未签名 exe + SmartScreen**：双击未签名 exe 会弹「Windows 已保护你的电脑」，
   点「更多信息」→「仍要运行」可放行，但**每次重建 exe 都会重新提示**（哈希变了）。
3. **给目录/文件加白名单**：Windows 安全中心 →「病毒和威胁防护」→「管理设置」→
   「排除项」添加仓库目录；第三方安全软件同理。企业组策略/受控文件夹访问可能需要管理员。
4. **直接跑 Python**：`python launch.py` 或 `.venv\Scripts\python.exe launch.py`，
   完全绕开 exe 的 SmartScreen/AV 启发式。
5. **只导入公钥证书**：如果 exe 已经签好，在别的机器上只想不弹「发布者无法验证」，
   按第 5 节导入 `.cer` 即可，不必安装私钥。

---

## 9. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `Could not create temporary directory!` | `%TEMP%` 不可写（安全软件、磁盘满、TEMP 指向无效路径）。换一个可写目录：`$env:TEMP='D:\temp'; $env:TMP=$env:TEMP`,再运行 |
| 双击闪退、看不到报错 | exe 是 `--console` 构建的；从 cmd 运行 `.\pgassistant.exe` 看完整日志 |
| `Status: NotSigned` / `Status: HashMismatch` | `NotSigned`：exe 被重新构建/替换，新文件没有签名块；`HashMismatch`：已签名文件被原地修改。两者都重新运行签名脚本 |
| `Status: UnknownError` / `NotTrusted` | 证书没导入当前用户的 `Root`；按第 4/5 节导入 `.cer` |
| 签名脚本提示找不到 exe | 先运行 `tools\build_exe.py` |
| 构建报没装 PyInstaller | `.venv\Scripts\python.exe -m pip install pyinstaller` |
| `--dry-run` 里出现 `%TEMP%\_MEI...` 路径 | 说明 exe 没在仓库根，或 `launch.py` 的 frozen 根目录逻辑被改坏；把 exe 放回仓库根 |
| 双击仍弹「发布者: 无法验证」 | 证书未进 `TrustedPublisher`/`Root`，或拦截来自组策略/安全软件；按第 8 节加白名单 |

---

## 10. 相关文件

| 文件 | 作用 |
|---|---|
| `launch.py` | 启动/停止逻辑；frozen 模式下用 exe 所在目录当仓库根 |
| `tools/build_exe.py` | 构建 `pgassistant.exe`（PyInstaller onefile） |
| `tools/sign_selfsigned.ps1` | 创建/复用本机代码签名证书、导入信任存储、签名并复核 |
| `data/dev-codesign.cer` | 导出的公钥证书（本机生成，`data/` 不入库） |
| `.gitignore` | 忽略 `pgassistant.exe`、`build/`、`*.spec` |
