# pgassistant

一个跑在你自己电脑上的学习资料库，也是一个会拆学习计划的 AI 助手。

它把上百篇中文技术资料整理成可检索的资料库，再让助手基于这些资料找内容、解释概念，或把「两个月入门后端」这样的目标拆成有阶段、能打勾的学习计划。
数据都留在本机；不配模型也能浏览资料、套用计划模板。

## 主要功能

**资料库。** 关键词检索，按技术栈、难度、类型筛选，详情页保留标题层级、图片和代码块；内容库不随仓库分发，首次启动自动准备，也可随时重建。

**学习计划。** 内置常见路线模板，也能自建阶段和条目；时间轴排开，完成一项勾掉一项，进度自动保存。

**AI 助手。** 对话找资料、解释概念，或直接说目标让助手生成计划；回答参考本地资料库，计划拆成阶段和可执行条目，流式输出。

**站内代码运行。** 资料里的 Python 和 JavaScript 代码块可直接在页面里运行，不用另装环境，代码不会发到外部服务；其它语言只展示。

**私有资料。** 把 Markdown 放进 `data/private`（或设置里指定目录），重新扫描后与内置资料一起检索；只在本机，不会混进公开内容。

**中英双语。** 界面可在简体中文和 English 之间切换，选择会记住。

## 运行环境

- Windows 10 / 11，或 macOS / Linux。
- Python 3.11 或更高版本、Node.js 20 或更高版本；首次准备内容库还需要 git。
- 首次准备内容库需要联网；资料准备好之后，浏览和检索可以离线使用（AI 对话仍需要联网）。
- 不需要数据库服务器，不需要 Docker，也不需要单独安装 SQLite。

## 安装与启动

### Windows

1. 安装 Python 3.11+ 和 Node.js 20+，确认 `python --version`、`node --version` 能正常输出；项目放在
   固定目录，不要只把 `start.cmd` 单独拷走。
2. 双击 `start.cmd`，或在项目目录的命令行里运行：

```bat
start.cmd
```

启动入口是 `start.cmd`（不再有 `start.ps1`），不经过 PowerShell，不需要修改执行策略。启动完成后浏览器会自动打开
`http://127.0.0.1:8000`（端口被占用时可能是 8001–8010 中的另一个）。

### macOS / Linux

```bash
chmod +x start.sh stop.sh   # 第一次运行前执行一次
./start.sh
```

### 首次启动会做什么

1. 在项目里创建 `.venv`，安装 Python 依赖。
2. 安装前端依赖并构建页面。
3. 准备内容库：从上游项目抓取教程正文和图片，需要联网，通常要几分钟。
4. 建立数据库和检索索引。
5. 在 8000–8010 里挑一个空闲端口启动服务，并打开浏览器。

### 停止

```text
stop.cmd        # Windows
./stop.sh       # macOS / Linux
```

也可以在启动窗口按 `Ctrl+C`。停止脚本可以重复执行，没在运行时也不会报错。

### 不想用一键脚本

如果习惯自己管理环境，可以在项目根目录按下面的顺序手动执行：

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r backend/requirements.txt
python tools/prepare_content.py
python backend/scripts/bootstrap_index.py
npm ci --prefix frontend
npm run build --prefix frontend
python -m uvicorn app.main:app --app-dir backend --port 8000
```

## 第一次使用：配置模型

要用 AI 助手或让助手自动生成计划，需要配置一个兼容 OpenAI 接口的模型：

1. 打开左侧「设置 → 模型」，填写 Base URL、模型和 API Key，点「保存模型设置」。
2. 点「测试连接」；成功显示「连接正常」，失败显示上游返回的原文，方便判断是地址、密钥还是额度问题。

DeepSeek 示例：

```text
Base URL: https://api.deepseek.com
模型:     deepseek-flash
API Key:  sk-...（在 DeepSeek 控制台创建）
```

不配模型也能正常使用资料库、检索、计划模板和手动计划，只是助手对话与自动生成计划不可用。

语义检索是可选项。在「设置 → 语义检索」里配置 embedding 服务后，检索会加入向量排序；不配置时自动使用关键词检索，助手功能不受影响。

## 数据与隐私

运行时数据都在项目根目录的 `data/` 下：

- `app.db`：SQLite 数据库，存学习计划、对话记录和设置。
- `private/`：你的私有 Markdown 资料；`logs/` 和 `backups/` 分别是日志和数据库迁移前的自动备份。
- `runtime.json`：本次运行的地址和进程信息。
- `llm_key`：只在系统凭据库不可用时出现，用来保存模型密钥。

内容库在 `content/` 下，是首次运行时从上游生成的，可以随时重建，不算你的个人数据。

API Key 只保存在本机：优先系统凭据库（Windows 凭据管理器 / macOS 钥匙串），不可用时才写入
`data/llm_key`；不进数据库、不进日志。服务只监听 `127.0.0.1`，局域网内其它设备访问不到，应用没有遥测。

会把数据发到本机之外的情况只有三类：助手对话或测试连接会发给模型服务商；配置语义检索后，建立向量时资料片段会发给 embedding 服务商；首次准备内容库会从上游站点和 GitHub 下载资料。

### 备份与迁移

先停止服务，然后把整个 `data/` 目录复制到安全位置。恢复时把备份放回原来的位置，再启动即可。
换电脑时建议连 `content/` 一起复制，省去重新准备内容库的等待；只复制 `data/` 也可以，内容库会重新生成。

如果密钥存在系统凭据库里，它不会随 `data/` 一起迁移；到新机器后需要在设置页重新输入一次 API Key。

### 彻底清除

停止服务后删除整个 `data/`，数据库、私有资料、日志和本地密钥文件都会一起消失。想连生成的内容库一起
清掉，再删除 `content/`。如果使用系统凭据库，还要在系统里删除名为 `pgassistant` 的凭据。

## 常见问题

### 双击 start.cmd 提示「无法验证发布者」

`start.cmd` 是脚本，Windows 无法给它有效的发布者签名；提示通常来自安全软件或系统策略，不是文件损坏。
可以改用下面任意一种方式：

- 打开命令行，进入项目目录后执行 `start.cmd`，绕开资源管理器的安全警告。
- 使用项目提供的 `pgassistant.exe`（自签名版本），适合想双击启动又不想看到提示的用户；它仍然是启动器，必须放在项目根目录。也可以把项目目录加入安全软件排除项。

### 端口被占用

启动脚本会自动在 8000–8010 里挑第一个空闲端口，实际地址会打印在窗口里并自动打开浏览器。
想固定端口可以运行 `start.cmd --port 8020`；指定的端口也被占用时会直接报错，不会自动换端口。

### 首次运行停在「准备内容库」

这一步需要访问 GitHub 和上游教程站点，网络慢时可能要几分钟。先确认机器能打开这些站点；使用代理时，
先在同一个窗口里设好 `HTTP_PROXY` / `HTTPS_PROXY` 再运行。失败的详细原因写在
`data/.sources/prepare-content.log`，也可以稍后手动重跑：

```bash
python tools/prepare_content.py
```

### 资料库是空的

通常是内容库没有准备成功。先运行上面的 `python tools/prepare_content.py`，成功后到
「设置 → 索引」点一次「全量重建」，再刷新页面。如果之前手动删过 `content/`，也需要重新准备。

### 模型连接失败

设置页会显示上游返回的原文。常见原因：API Key 填错或已失效、Base URL 路径不对、模型在当前账号下
不可用、账户余额或配额不足。先用「测试连接」确认配置本身能通，再回到助手页面。

### 怎么换语言

打开「设置 → 通用」，在「语言」里选「简体中文」或「English」，切换会立即生效并记住选择。

## 许可与致谢

代码使用 MIT 许可，完整条款见 [LICENSE](LICENSE)。

学习资料不随仓库分发，由使用者在本机从上游项目抓取。主要来源和许可如下：hello-algo 与
coding-interview-university 为 MIT；MDN Web Docs 为 CC BY-SA 4.0；react.dev 为 CC BY 4.0；
TypeScript 文档为 Apache-2.0；runoob 的部分教程标注为 proprietary，仅供本地个人学习，请勿二次分发。

每篇资料的文首记录了 author、license 和 source_url，二次分发前请按对应许可核对；本项目的 MIT 许可只覆盖代码和文档。

想参与贡献或自行修改，见 [CONTRIBUTING.md](CONTRIBUTING.md)。
