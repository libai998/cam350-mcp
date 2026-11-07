# CAM350 MCP Server

通过 **MCP（Model Context Protocol）** 用 AI 控制 DownStream **CAM350 12.x**。

CAM350 没有开放 COM / DDE / 插件 API，本服务器走它**原生的宏语言**：
宏命令以 `@` 结尾（如 `numlayers!` 是查询、`panelization@` 是命令），
进程级控制用 `camnt95.exe -M<宏文件>` 与常驻 watcher 宏实现。

- 发行形式：**标准 Python wheel 安装包**（非插件）。CAM350 本身不支持插件，
  MCP server 是一个独立的 stdio 进程，任何支持 MCP 的客户端都能接入。
- 运行时：**Windows 原生 Python 3.10+**（CAM350 是 Windows 桌面程序，
  WSL 里的 Linux Python 无法直接操作它的窗口）。

> **English**: An unofficial MCP server that drives DownStream CAM350 12.x
> through its native macro language (there is no COM/DDE/plugin API). It runs
> on native Windows Python 3.10+ and exposes 23 tools for job/layer management,
> macro execution, Gerber/Excellon/ODB++ import-export, DRC and panelization.

> **免责声明**：本项目是非官方社区项目，与 DownStream Technologies 无隶属
> 或背书关系。CAM350 是 DownStream Technologies 的商标；使用本项目前请确保
> 你拥有合法的 CAM350 授权。CAM350 帮助文档派生的参考索引
> （`reference.json`）**不随本仓库分发**，见 [2.4 配置](#24-配置)。

---

## 1. 功能一览（23 个工具）

| 分类 | 工具 | 说明 |
|---|---|---|
| 生命周期 | `cam350_status` | 进程、窗口、作业、配置诊断 |
| | `cam350_start` / `cam350_stop` / `cam350_restart` | 启停常驻实例（可同时打开作业/导入目录） |
| | `cam350_abort` | 中断卡住的宏（自动点掉宏对话框），必要时杀进程 |
| 数据库 | `cam350_open_job` / `cam350_save_job` | 打开 / 另存 `.cam` 作业 |
| | `cam350_import` | 导入 Gerber / Excellon / ODB（`auto` / `nc` / `new` 三种模式） |
| 层与查询 | `cam350_layers` | 列出层 id、名称、类型 |
| | `cam350_edit_layer` | 改层类型、名称、颜色、开关状态 |
| | `cam350_query` | 执行 CAM350 查询（`numlayers!`、`Units!`…） |
| 宏 | `cam350_run_macro` | 在常驻实例里跑任意宏（实例自动保持存活） |
| | `cam350_batch_macro` | 在全新进程里跑一次性宏（`-M` 批处理） |
| 导出 | `cam350_export_gerber` | 导出 Gerber（274X / 274D） |
| | `cam350_export_drill` | 导出 Excellon 钻孔 |
| | `cam350_export_odb` | 导出 ODB++（`.tgz`） |
| 分析 | `cam350_drc` | DRC 检查，返回错误数与报告 |
| | `cam350_panelize` | 拼板（经典 Step & Repeat 流程） |
| GUI | `cam350_screenshot` | 截取 CAM350 窗口 |
| | `cam350_dialogs` / `cam350_click_dialog` | 枚举 / 点击对话框按钮（无需焦点） |
| 辅助 | `cam350_reference` | 检索宏命令/查询（可选，需自备 `reference.json`） |
| | `cam350_read_file` | 读取 CAM350 生成的报告/日志 |

> **单实例限制**：CAM350 同一时刻只允许一个自动化会话。批处理调用
> （`cam350_batch_macro`）在常驻实例运行时会被拒绝；先 `cam350_stop`，
> 或传 `force=true` 让服务器自动停掉常驻实例。

---

## 2. 安装教程

### 2.1 系统要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11（x64） |
| CAM350 | 12.x（默认自动探测 12.1） |
| Python | 3.10 及以上（**Windows 原生**，python.org 安装包） |
| MCP 客户端 | Claude Code / Claude Desktop / 任何 MCP stdio 客户端 |
| 权限 | 普通用户即可，无需管理员 |

CAM350 的安装位置会自动探测（注册表 + `Program Files` 通配）；装在
非标准路径时用环境变量 `CAM350_EXE` 指定（见 2.4）。

### 2.2 方式 A：一键安装（推荐）

```powershell
git clone https://github.com/libai998/cam350-mcp.git
cd cam350-mcp
py -3 -m pip wheel . -w dist --no-deps
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Register
```

`install.ps1` 会依次完成：

1. 在 `dist\` 里找到最新的 `cam350_mcp-*.whl`；
2. 探测 Python（`py -3` 或 `python`）并检查 ≥ 3.10；
3. 在 `%LOCALAPPDATA%\Programs\cam350-mcp\venv` 建立独立虚拟环境；
4. `pip install` 安装 wheel（同版本重装会 `--force-reinstall`）；
5. 运行 `cam350-mcp.exe --check` 做健康检查；
6. `-Register` 时自动注册到 Claude Code（用户级 scope），
   会先删除旧的 `cam350` 条目再添加。

常用参数：

```powershell
# 不自动注册，只安装
.\scripts\install.ps1

# 指定安装目录 / Python / wheel
.\scripts\install.ps1 -InstallDir D:\tools\cam350-mcp -Python C:\Python312\python.exe -Wheel .\dist\cam350_mcp-0.1.0-py3-none-any.whl

# 注册到 free-code（部分环境里 Claude Code 的命令名）
.\scripts\install.ps1 -Register -Client free-code
```

装完后服务器命令是：

```
%LOCALAPPDATA%\Programs\cam350-mcp\venv\Scripts\cam350-mcp.exe
```

### 2.3 方式 B：手动 pip 安装

适合已有 Python 环境、或想装进现有 venv 的机器：

```powershell
# 1) 建独立环境（可选但推荐）
py -3 -m venv C:\tools\cam350-mcp
C:\tools\cam350-mcp\Scripts\activate

# 2) 从 GitHub 或本地克隆安装
pip install "git+https://github.com/libai998/cam350-mcp.git"
# 或：pip install C:\path\to\cam350-mcp

# 3) 自检
cam350-mcp --check
```

`--check` 输出 JSON，关键字段：

```json
{
  "version": "0.1.0",
  "python": "3.12.10",
  "cam350_exe": "C:\\Program Files (x86)\\DownStream Technologies\\2015\\CAM350 12.1\\camnt95.exe",
  "cam350_found": true,
  "data_dir": "C:\\Users\\<you>\\AppData\\Local\\cam350-mcp",
  "reference": "C:\\...\\cam350_mcp\\reference.json",
  "reference_exists": false,
  "reference_hint": "reference.json is an index of CAM350 help text ...",
  "ok": true
}
```

`"ok"` 只取决于是否找到 `camnt95.exe`；`reference_exists: false` 不影响
其余工具，只是 `cam350_reference` 不可用、宏命令校验会跳过。

### 2.4 配置

全部通过环境变量，无需改代码：

| 变量 | 作用 | 默认 |
|---|---|---|
| `CAM350_EXE` | `camnt95.exe` 的完整路径 | 自动探测 |
| `CAM350_MCP_HOME` | 运行时数据目录（宏、日志、截图、导出） | `%LOCALAPPDATA%\cam350-mcp` |
| `CAM350_MCP_REFERENCE` | 可选的宏/查询参考索引 `reference.json` | 包内同目录 |

```powershell
setx CAM350_EXE "C:\Program Files (x86)\DownStream Technologies\2015\CAM350 12.1\camnt95.exe"
setx CAM350_MCP_HOME "D:\cam350-data"
setx CAM350_MCP_REFERENCE "D:\cam350-data\reference.json"
```

> **关于 `reference.json`**：它由 CAM350 帮助文档整理而来，包含 DownStream
> 的文档内容，因此**不随本仓库分发**。没有它时所有宏、导入导出、DRC、拼板
> 等功能照常工作；仅 `cam350_reference` 与「未知宏命令」预校验被禁用。
> 如果你自建了该索引，把路径设到 `CAM350_MCP_REFERENCE` 即可。

`setx` 只影响之后新开的进程；临时生效可直接在当次 shell 里 `set`。
也可以不改环境变量，直接用命令行参数覆盖：

```powershell
cam350-mcp --exe "D:\CAM350\camnt95.exe" --home D:\cam350-data --check
```

### 2.5 注册到 MCP 客户端

```powershell
# Windows 上的 Claude Code
claude mcp add --scope user cam350 -- "<venv>\Scripts\cam350-mcp.exe"

# 本机的 Claude Code 命令名为 free-code
free-code mcp add --scope user cam350 -- "<venv>\Scripts\cam350-mcp.exe"
```

WSL 里运行 Claude Code 时，MCP 进程可以在 Windows 侧启动：

```bash
free-code mcp add --scope user cam350 -- "/mnt/c/Users/<you>/AppData/Local/Programs/cam350-mcp/venv/Scripts/cam350-mcp.exe"
```

> 更新或换路径时先删再加（同名条目不会自动覆盖）：
> `free-code mcp remove cam350 --scope user`
>
> 也可以让被安装的包直接打印注册命令：`cam350-mcp --print-claude-command`

客户端里用 `mcp list`（或 Claude Code 的 `/mcp`）确认 `cam350` 显示 **Connected**。

### 2.6 从源码构建（开发者）

```powershell
py -3 -m pip install build
py -3 -m build              # 或: py -3 -m pip wheel . -w dist --no-deps
py -3 -m pytest             # 冒烟测试（Windows）
```

产物在 `dist\`。源码布局为 src-layout：`src/cam350_mcp/{__init__,cli,core,server}.py`。
`reference.json` 是可选项：本地放了就会被 package-data 打进 wheel，
仓库里没有它也能正常构建运行。

---

## 3. 使用教程

### 3.1 最小流程

在 Claude Code 里直接用自然语言，或引用工具名。典型会话：

```
1. cam350_status                       # 看看 CAM350 装没装、跑没跑
2. cam350_start(job="D:\\work\\board.cam")   # 启动并打开作业
3. cam350_layers()                     # 列层
4. cam350_query(["numlayers!", "Units!"])
5. cam350_drc(clearance=0.15)          # DRC
6. cam350_export_gerber(out_dir="D:\\out\\gerber")
7. cam350_stop()
```

参数里的路径既可以是 Windows 风格（`C:\...`），也可以是被自动转换的
WSL 风格（`/mnt/c/...`）。

### 3.2 打开 / 导入 / 保存

```
cam350_start(job="D:\\work\\board.cam")
```

`setopenfile@` 只能在顶层宏里执行，所以**打开作业会重启常驻实例**。
`job` 不是 `.cam` 时按“导入源”处理（取其所在文件夹）。

```
cam350_import(source="D:\\work\\gerber_dir", mode="auto")
```

| mode | 行为 |
|---|---|
| `auto` | `auto_import@` 导入文件夹，保留当前数据库已有层 |
| `nc` | `Auto_Import_NCData@`，用于钻孔/铣边文件 |
| `new` | 先 `setnewfile@` 清空，再 `auto_import@`——从零开始一个新库 |

```
cam350_save_job(path="D:\\work\\board_v2.cam")
```

### 3.3 层操作

```
cam350_layers()
→ {"layers": [
     {"id": 0, "number": 1, "name": "Layer_1", "type": 0, "type_name": "Top"},
     {"id": 3, "number": 4, "name": "Layer_4", "type": 21, "type_name": "NC Data"},
     ...
   ]}
```

`id` 是 0-based（宏里用），`number` 是 CAM350 界面上的 1-based 编号。

```
cam350_edit_layer(layer_id=2, type=7, name="SilkTop")
```

类型码：`0` Top、`1` Internal、`2` NegPlane、`3` Bottom、`4` Graphic、
`6` Border、`7` SilkTop、`8` SilkBottom、`11/12` MaskTop/Bottom、
`13` Temporary、`14` PosPlane、`16/17` RefDes、`18` Insulator、
`19/20` Paste、`21` NC Data。

颜色参数默认 `-2` = **保持原值**；不要传 `-1`，那会新建层。
`status=1` 打开层、`0` 关闭。

### 3.4 查询与参考

```
cam350_query(["numlayers!", "Units!", "dbminx!", "dbmaxx!", "dbminy!", "dbmaxy!"])
```

查询必须以 `!` 结尾。不确定命令名时用参考检索：

```
cam350_reference("panel")       # 搜命令/查询/帮助页
cam350_reference("Layer_Queries")
```

返回结果里带命令分类和帮助页链接名，可据此拼宏。

### 3.5 任意宏

```
cam350_run_macro("""
setlayer@ 0
delete_layer@
update_layerbar@
""")
```

- 一行一个命令，命令以 `@` 结尾；
- 末尾的 `setquit@` / `END` / `EXIT MACRO` 会被自动去掉，**常驻实例保持存活**；
- 未知命令会以 warning 返回——它们可能让宏播放卡住，务必检查；
- 每次调用都会在 `data_dir\runtime\calls\<时间戳>` 下保存宏与输出，便于排查。

一次性批处理（独立进程，跑完自动退出）：

```
cam350_batch_macro("""
setnewfile@
auto_import@ D:\\work\\gerber_dir
setexportfile@ D:\\out\\panel.cam
setquit@
""", force=true)
```

`force=true` 会先停掉常驻实例，因为 CAM350 只允许一个自动化会话。

### 3.6 导出

**Gerber（274X）**

```
cam350_export_gerber(
    out_dir="D:\\out\\gerber",
    layer_ids=[0, 1, 3],      # 省略 = 所有非 NC 层
    fmt="274x",
    units="metric",
    digits_x=3, digits_y=3,   # 3.3；英制常用 2.4
    zero="leading",
)
```

**钻孔（Excellon）**

```
cam350_export_drill(out_dir="D:\\out\\drill")   # 默认导出所有 type=21 层
```

**ODB++**

```
cam350_export_odb(out_path="D:\\out\\board.tgz")
```

导出结果都带文件大小与预览（图片直接返回给客户端）。

### 3.7 DRC

```
cam350_drc(clearance=0.15)                  # 0.15 mm ≈ 6 mil
cam350_drc(clearance=6, checks=["trk2trk", "trk2pad", "pad2pad", "outline"])
cam350_drc(pass_file="D:\\work\\strict.pass")   # 用已保存的 DRC pass
```

`clearance` 使用**数据库单位**（`Units!` 可查）。可用检查项：
`trk2trk, trk2pad, pad2pad, outline, mintrk, minpad, redundpad,
pad2masktop, pad2maskbot, drill2mask, drill2pad, drillwopad,
padwodrill, drill2trk`。

返回 `Number_drc_errors` 与报告前几行；用 `cam350_read_file` 读完整报告。

### 3.8 拼板

```
cam350_panelize(
    panel_x=300, panel_y=250,           # 拼板尺寸（数据库单位）
    border_left=5, border_top=5, border_right=5, border_bottom=5,
    image_spacing_x=3, image_spacing_y=3,
    rotation=0,
    outline=True, outline_dcode=10, outline_layer=0,
    save_as="D:\\work\\panel.cam",
)
```

内部按经典 Autofilm 顺序执行：
`panel_spread@` → `panel_spacing@` → `panel_dimension@` → `panel_outline@`
→ `panel_centerimg@` → `setfilmbox@` → `panelization@` → `update_layerbar@`
（`rotation` 非 0 时追加 `panel_rotate@`）。结果生成在新层上，
返回值里含生成的宏和更新后的层列表。

### 3.9 GUI 辅助

```
cam350_screenshot()                      # 截 CAM350 窗口（会切到前台）
cam350_dialogs()                         # 有哪些对话框和按钮
cam350_click_dialog("OK", title="Warning")   # 按标题点按钮，不需要焦点
cam350_abort()                           # 宏卡住时中断
```

---

## 4. 排错

| 现象 | 原因 / 处理 |
|---|---|
| `--check` 里 `cam350_found: false` | 设置 `CAM350_EXE` 指向 `camnt95.exe` |
| `cam350_start` 返回 `started: false` | 看 `error`；多为没找到 exe 或上一实例未退出 |
| 宏播放卡在未知对话框 | `cam350_abort()`；再用 `cam350_dialogs()` 看按钮 |
| `batch_macro` 报 “已有实例” | 先 `cam350_stop`，或传 `force=true` |
| 截图截到别的窗口 | 截图会自动切前台；确认 CAM350 没被最小化 |
| 路径报 `FileNotFoundError` | Windows 侧请用 `C:\...`，不要传 `/mnt/c/...` 给原生 Python |
| 中文界面输出乱码 | 宏文件按 ANSI/CRLF 写入；报告用 `cam350_read_file` 读取 |
| MCP 显示未连接 | 用 `free-code mcp remove cam350 --scope user` 后重新 `add` |
| 注册后的 command 变成 `C:Users...`（反斜杠丢失） | Windows 上的 `free-code.cmd` 是 WSL 垫片，参数会被 WSL 解析吞掉反斜杠；改用 `/mnt/c/...` 路径注册（`install.ps1` 对 `.cmd/.bat` 客户端已自动处理） |
| `cam350_reference` 返回 `available: false` | `reference.json` 不随仓库分发；设置 `CAM350_MCP_REFERENCE` 指向你自己的副本 |
| Macros run but no "unknown command" warnings | 同上；没有参考索引时命令校验会被跳过 |

运行日志与中间产物：

```
%LOCALAPPDATA%\cam350-mcp\
├─ runtime\calls\     每次宏调用的宏文件 + 输出
├─ runtime\shots\     截图
├─ runtime\staging\   导入暂存
└─ state.json         常驻实例状态
```

---

## 5. 升级 / 卸载

**升级**：`git pull` 后重跑 `install.ps1 -Register`（会覆盖安装并重注册），
或手动 `pip install --upgrade --force-reinstall "git+https://github.com/libai998/cam350-mcp.git"`。

**卸载**：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
# 同时删除运行时数据
.\scripts\uninstall.ps1 -RemoveData
# 从 free-code 注销
.\scripts\uninstall.ps1 -Client free-code
```

卸载不会动你导出到别处的文件。

---

## 6. 目录结构

```
cam350-mcp/
├─ pyproject.toml
├─ README.md
├─ LICENSE              MIT
├─ .gitignore
├─ src/cam350_mcp/
│  ├─ __init__.py       版本号
│  ├─ cli.py            cam350-mcp 命令（--check / --version / stdio）
│  ├─ core.py           宏执行、进程/窗口管理、导入导出、DRC、拼板
│  └─ server.py         23 个 MCP 工具
├─ scripts/
│  ├─ install.ps1       一键安装 + 注册
│  └─ uninstall.ps1     卸载
├─ tests/
│  └─ test_smoke.py     CLI/核心冒烟测试
└─ .github/workflows/
   └─ ci.yml            Windows CI（3.10 / 3.12）
```

`reference.json`（可选）放在 `src/cam350_mcp/` 下时会被打进本地构建的
wheel；它已被 `.gitignore` 排除，不会进入仓库。

---

## 7. 许可证

[MIT](LICENSE)。本项目与 DownStream Technologies 无关；CAM350 及相关名称
归其各自所有者所有。
