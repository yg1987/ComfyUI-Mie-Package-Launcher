# AGENTS.md — 给 AI agent 的操作指南

> 本文件是 agent 操作本启动器的入口。完整 CLI 契约见 [`cli.md`](cli.md)。

## 强制会话预检（不可跳过）

**任何代码搜索、读取、测试、构建或修改之前，先完整阅读本文件。** 当前工作目录可能位于本仓库上级；即便外层存在工作区规则，也必须继续读取到本项目的这份 `AGENTS.md`。

本机已配置可用工具链，禁止只检查项目 `.venv` 后就声称依赖不存在：

- GUI / PyQt5 测试：`D:\CodexTools\pyqt5-test\Scripts\python.exe`
- 正式构建：`D:\CodexTools\launcher-build\Scripts\python.exe`
- Enigma：`D:\CodexTools\EnigmaVirtualBox\enigmavbconsole.exe`

运行 Qt 测试时，必须先使用 GUI 测试 Python，例如：

```powershell
& 'D:\CodexTools\pyqt5-test\Scripts\python.exe' -m unittest tests.unit.test_plugin_workers -v
```

只有上述可执行文件或命令实际失败，才能报告测试环境不可用，并附失败证据；不得以项目 `.venv` 缺少 PyQt5 作为结论。

## 这是什么

ComfyUI 启动器（PyQt5，Windows）。无参数启动 = GUI 图形界面；带子命令 = headless CLI，
复用 GUI 同一套启动/停止路径，适合自动化、监控、开机自启。

**支持多环境**：一个 ComfyUI 根目录 + python 路径的组合 = 一个「环境」，config 里可存多组。
同时只能运行一个环境，切换前必须先停止当前服务（CLI 会拒绝重复 start，GUI 会提示）。

## 怎么调用

**agent / 自动化推荐 `ComfyUI启动器-CLI.cmd`**：和下面 `ComfyUI启动器.exe` 行为完全等价（参数 + 退出码透传），但名字带 -CLI，对监控脚本 / NSSM / systemd / GitHub Actions 更友好。必须和 `ComfyUI启动器.exe` 同目录。

```bash
# 打包版（部署/运维场景，agent 通常用这个）—— exe 会自动切到自身所在目录找配置
ComfyUI启动器.exe <command> [--json] [-v]

# 开发版（从仓库根目录跑）
python __main__.py <command> [--json] [-v]
```

- **无子命令** → `ComfyUI启动器.exe`（裸跑）会启动 GUI；`ComfyUI启动器-CLI.cmd`（裸跑）会转发到 `help`，不弹窗口。
- **未知子命令或仅传 flags**（如 `frobnicate`、`--json` 单独）→ wrapper exit 1 + stderr 一行 `[ComfyUI启动器-CLI] ERROR: ...`，不弹 GUI。
- 所有子命令都支持 `--json`（输出**单行** JSON，便于解析）和 `-v`/`--verbose`（可叠加 `-vv`）。

## 子命令速查（agent 最常用）

| 目的 | 命令 | 判断方式 |
|---|---|---|
| 健康检查 | `status --json` | 退出码 `0`=在跑 / `3`=未跑 / `1`=异常；或解析 `.running` |
| 启动 | `start` | 阻塞到 `/system_stats` 就绪；加 `--no-wait` 立即返回 |
| 停止 | `stop` | 幂等，未跑也退 `0`；加 `--force` 直接 `taskkill /F` |
| 重启 | `restart` | stop 旧 + start 新 |
| 看配置 | `info --json` | `.comfyui_path` `.python_path` `.port` `.launcher_version` `.environments` `.active_env_id` |
| 看日志 | `logs comfyui -n 100 --no-follow` | `comfyui` / `launcher` 二选一；**务必带 `--no-follow`** |
| 更新内核 | `update comfyui --dry-run` 然后 `update comfyui` | 先 dry-run 看会做什么 |
| 查帮助 | `help` / `help <command>` / `<command> --help` | — |

> **agent 默认不传 `--env`**。`start` / `restart` / `info` / `update` / `logs` 接受可选的 `--env ENV_ID` 覆盖本次调用的环境，仅供跨环境自动化脚本用；`status` / `stop` 不接受 `--env`（作用于「当前在跑的那个」）。切环境是 GUI 的事，agent 不要主动切。

典型自动化节奏：`status --json` 判断在不在跑 → 不在就 `start` → 失败就 `logs comfyui --no-follow` 排查。

## 机器契约（agent 解析依据）

- **`--json` 输出**：每个命令都是单行 JSON，字段 schema 见 `cli.md` 每个子命令的 *Output schema* 段。
- **退出码**（定义在 `core/cli/exitcodes.py`，跨命令稳定）：

  | 码 | 含义 | 出现在 |
  |---|---|---|
  | 0 | 成功 | 所有 |
  | 1 | 通用错误（路径缺失/env/IO/超时） | 所有 |
  | 2 | start 拒绝重复（已在跑） | start |
  | 3 | 未在跑 | status |
  | 4 | 已是最新 | update |

- 推荐用法：**按退出码分支 + 解析 `--json` 字段**，不要正则匹配人类文案。

## 关键路径

| 文件 | 含义 |
|---|---|
| `launcher/config.json` | 配置（端口、路径、环境列表、启动选项）—— **机器本地，含绝对路径，勿提交运行时改动** |
| `launcher/launcher.log` | 启动器自身日志 |
| `<comfyui_root>/user/comfyui.log` | ComfyUI 输出日志（`logs comfyui` 读这个） |
| `launcher/comfyui.pid` | 跨进程 PID 协调（JSON：pid/port/started_at/log_path/env_id），stale 当不存在 |

端口默认 `8188`，来自 `config.json` 的 `launch_options.default_port`。

### `config.json` 的多环境 schema

```json
{
  "environments": [
    {"id": "env_default", "name": "默认环境", "comfyui_root": "...", "python_path": "..."}
  ],
  "active_env_id": "env_default",
  "paths": {"comfyui_root": "...", "python_path": "..."}
}
```

- `environments[]`：环境数组，每项含 `id`（稳定标识，CLI `--env` 用）/ `name` / `comfyui_root`（ComfyUI 安装的**父目录**，launcher 拼 `root/ComfyUI/main.py`）/ `python_path`。
- `active_env_id`：当前激活环境 id。
- `paths`：**老 schema 的兼容回退**。`get_active_paths()` 优先读 `environments[active_env_id]`，为空才回退 `paths`。首次加载时老 `paths` 会自动迁移成 `environments[0]`（`config/migrations.py`）。

### 多环境代码入口（agent 改路径相关逻辑时看这些）

| 文件 | 含义 |
|---|---|
| `config/migrations.py` | 迁移 + 解析纯函数：`migrate_environments` / `resolve_active_paths` / `resolve_paths_for_env` / `find_env` / `update_active_env` |
| `core/launcher_cmd.py` | `build_launch_params(app, env_id=None)` —— 启动命令构建，用激活环境（或 `--env` 指定）的路径 |
| `utils/paths.py` | `comfy_root_from_config(config)` —— 内部已走 `resolve_active_paths`，传完整 config 即自动环境感知 |
| `headless_app.py` / `ui_qt/qt_app.py` | 两个 app 类各实现 `get_active_paths()`（无共同基类，鸭子类型） |

## 多环境机制（背景，agent 默认不切）

- **agent 默认不切换环境**。CLI 跑的就是 GUI 当前激活的环境（`config.json` 的 `active_env_id`）；切环境是 GUI 的事，agent 不要主动切，也不要为单次启动改 config / 加 `--env` 绕过 GUI 当前配置。
- **同时只能跑一个环境**。`start` 时若已有环境在跑（pidfile 有效），拒绝启动并返回当前在跑的 `running_env_id`；要先 `stop` 再启动。
- **`--env` 是一次性的，不持久化**：仅供跨环境自动化 / 一次性脚本用；不传则用 `active_env_id`，不写回 config。要永久切换激活环境，改 config（GUI 的环境下拉 / 设置页管理，或直接写 `active_env_id`）。
- **`--env <不存在的 id>` 报错**：返回 `error: "环境不存在: ..."`，退出码 1。先 `info --json` 拿 `.environments[].id` 确认可用 id。
- **`stop` / `status` 不接受 `--env`**：它们作用于当前在跑的那个环境，跟环境选择无关。
- **pidfile 记录 `env_id`**：`start` 写入时带上当前环境 id，`status` / `start` 的"已在跑"返回里含 `running_env_id`，便于 agent 判断冲突。
- **`launch_options` 是全局的**，不 per-env（端口/GPU/监听所有环境共享，因为同时只跑一个）。
- **有后台任务时禁止切换**：切换前会检查 `app.has_active_background_tasks()`（覆盖 BackgroundTaskRegistry 的活跃任务 + `_update_running` 核心更新标志）。有进行中任务时弹框阻止切换——因为 git/cm-cli 子进程不能安全强杀，中途换环境会操作错误仓库/目录甚至写坏文件。ComfyUI 服务进程则可以停（用户确认后自动 stop 再切）。
- **version worker 竞态防护**：`refresh_after_env_switch` 会自增 `_env_token`，正在跑的旧 worker 回调时发现 token 变了就丢弃结果，避免旧环境的版本号迟到覆盖新环境。

## 坑（agent 易踩）

- **`logs -f` 会永久阻塞**，自动化/脚本里禁用，要 `--no-follow`。
- **无子命令 = GUI**：如果 agent 想跑 CLI 却只执行了 `ComfyUI启动器.exe`（不带子命令），会弹 GUI 而非执行命令。
- `--start` / `--stop` / `--status` 这类**老 flag 已废弃**（旧文档可能还写），现在是子命令：`start` / `stop` / `status`。
- 配置改动会落到 `launcher/config.json`，里面是本机绝对路径——**不要把运行时生成的 config 改动提交进 git**。
- 调试模式：在 `launcher/` 目录下建一个 `is_debug` 文件（内容随意），日志变详细。
- **多环境：不要直接读写 `config["paths"]`**。老 `paths` 段是兼容回退，生产代码应走 `app.get_active_paths()`（GUI/CLI app 对象）或 `resolve_active_paths(config)`（纯 config）。详见上方「多环境代码入口」表。
- **`comfyui_path` 是死字段**：老 config 里的 `paths.comfyui_path` 几乎无人读（实际驱动逻辑的是 `comfyui_root`），多环境迁移时已丢弃，别搬进 environment 对象。
- **agent 不要为单次启动改 `config.json` / 加 `--env` 绕过 GUI**。CLI 就是 GUI 当前配置的 headless 别名——端口、env、paths 全以 GUI 为准。看到端口冲突 / env 不对，应该让用户去 GUI 调整，而不是 agent 自己改配置 / 加 override。
- **`start` / `stop` 只动 pidfile 里那个 PID**：看不到的另一份 launcher 实例（多环境 GUI 各自）可能随时被它自己的 GUI 关掉。如果看到 8188 突然空了，多半是用户手动操作，**别当成 launcher 的副作用去调查**。
- **发布到 GitHub 后 zip 名中 `启动器` 会变成 `_`**：本地 release/里的 zip 叫 `ComfyUI启动器_v<ver>_<ts>.zip`，上传后在 GitHub release 资产里变成 `ComfyUI._v<ver>_<ts>.zip`。这是 `gh release upload` 的 bug（中文字符被替换为 `_`），v1.0.13 也是这样。**zip 内容正确**（解压后子目录名是 `ComfyUI启动器_v<ver>_<ts>/`，里面文件全对），不影响用户下载体验。看到资产名字对不上别质疑 gh 配置问题，是已知 bug，详见 `release.py` 里的注释。如果一天 gh 修了，不要忘了同步 release note 里的下载名描述。

## Windows 正式构建与发布产物

### GitHub Actions 正式发布：强制预检与收尾

用户要求提交并发布时，**不得把 GitHub Actions 当作调试环境连续试错**。在首次推送前必须按顺序完成以下步骤；只有全部通过，才可推送并让 `my-changes` 的自动工作流发布：

0. 先更新 `build_parameters.json` 的 `version`，并将它和本次产品代码一起提交。正式 Release 使用纯产品版本标签，**绝不附加** GitHub Actions 运行号。版本递增约定如下：
   - 当前正式版本是 `v1.0.15`；下一次正式发布必须是 `v1.0.16`。
   - 补丁号依次递增：`1.0.16`、`1.0.17`……`1.0.20`。
   - 当补丁号已为 `20`，下一次为 `1.1.0`；之后按同样规则递增（例如 `1.1.20` 后为 `1.2.0`）。
   - 本地测试包继续用 `ComfyUI启动器_v<版本>_<时间戳>_test.exe`，时间戳仅用于区分本地测试构建，不进入 GitHub Release 版本。
1. 查阅最近的工作流结果和失败日志（如有）：
   ```powershell
   gh run list --repo yg1987/ComfyUI-Mie-Package-Launcher --workflow 'Build Windows release package' --limit 5
   ```
2. 本仓库为私有仓库，GitHub Hosted Runner 不能直接访问 Enigma 厂商 URL。工作流必须使用**私有预发布缓存** `enigma-v11.30-build20250428` 中的 `enigmavb-probe.bin`，通过 `gh release download`（携带 `GH_TOKEN: ${{ github.token }}`）下载，并在运行前以 `Get-AuthenticodeSignature` 验签。不得改回匿名 URL 或 `curl` 下载。
3. 推送前在本机用同一条认证下载路径验证缓存资产；必须得到 7,946,496 字节、`Valid` 签名和 SHA-256 `AB743F5E3DD927A288E126BBB053D367F270592E89378C9A06B7F3B15FA1EE35`：
   ```powershell
   gh release download enigma-v11.30-build20250428 `
     --repo yg1987/ComfyUI-Mie-Package-Launcher --pattern 'enigmavb-probe.bin' `
     --dir D:\CodexTools\temp --clobber
   Get-AuthenticodeSignature D:\CodexTools\temp\enigmavb-probe.bin
   Get-FileHash D:\CodexTools\temp\enigmavb-probe.bin -Algorithm SHA256
   ```
4. 用构建环境校验 workflow YAML，并运行 `git diff --check`：
   ```powershell
   & 'D:\CodexTools\launcher-build\Scripts\python.exe' -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/build-release.yml').read_text(encoding='utf-8'))"
   ```
5. 将所有已验证的代码与 workflow 改动合并为**一次**提交后再推送 `my-changes`；不要推送未经预检的中间 CI 修复。该分支上的 push 会自动触发正式发布。
6. 推送后用 `gh run watch <run-id> --exit-status` 等到结束。只有运行成功后，才通过 `gh release view v<version>` 核对标签、目标提交、EXE 资产、大小与 SHA-256，再向用户宣布发布完成。

若发布失败，先执行 `gh run view <run-id> --log-failed` 获取证据；在本机完成上述同等预检后才允许提交下一次修复，不能直接再推送猜测性改动。

构建正式版 exe 时，**不要使用项目内的 `.venv`**；它可能缺少完整的 Nuitka / PyQt5 构建依赖。使用已验证的工具链：

- 构建 Python：`D:\CodexTools\launcher-build\Scripts\python.exe`
- Enigma Virtual Box：`D:\CodexTools\EnigmaVirtualBox\enigmavbconsole.exe`

在仓库根目录执行完整的 Nuitka + Enigma 构建：

```powershell
$env:CL = '/utf-8'
& 'D:\CodexTools\launcher-build\Scripts\python.exe' build.py `
  --test `
  --python-path 'D:\CodexTools\launcher-build\Scripts\python.exe' `
  --enigma-path 'D:\CodexTools\EnigmaVirtualBox\enigmavbconsole.exe'
```

- 自动化运行上述完整 Nuitka + Enigma 构建时，必须显式设置**至少 15 分钟（900,000 ms）**的命令超时；两分钟仅适用于快速检查，不能用来判定完整构建失败。超时后先检查构建进程和 `release/` 产物，再决定是否重试或报告失败。

- 必须保留 `$env:CL = '/utf-8'`：项目的中文产品元数据会进入 Nuitka 自动生成的 C 头文件；缺少该选项时，MSVC 936 代码页可能报 `C4819` / `C2001: 常量中有换行符`。
- **本机构建一律使用 `--test`**（也是脚本默认值），Release 文件名必须带 `_test.exe`；不要把本机验证包当正式发布物。
- GitHub Actions 使用 `build.py --release` 生成正式包：exe 文件名不得含 `test`，并上传到 GitHub Release。正式 Release 标签为单个 `v` 前缀加产品版本（例如 `v1.0.15`），不含构建号或时间戳。
- 构建会重建 `dist/ComfyUI启动器.dist`，并在 `release/` 生成单文件产物 `ComfyUI启动器_v<版本>_<时间戳>.exe`。这是正常且已授权的构建副作用。
- 构建后至少确认产物存在、大小和 SHA-256，例如：

```powershell
Get-FileHash -LiteralPath '.\release\<生成的 exe 文件名>' -Algorithm SHA256
```

- `build_parameters.json` 会更新构建时间；保留用户已有版本号，不要为普通构建擅自传 `--version`。

## 深入

- 完整 CLI 参考（每命令 flag / Exit codes / Output schema / systemd / NSSM / cron 示例）：[`cli.md`](cli.md)
- 服务接口契约：[`docs/ServiceInterfaces.md`](docs/ServiceInterfaces.md)
