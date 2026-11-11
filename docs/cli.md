# RepoPilot 终端客户端

客户端采用可滚动的终端对话布局。启动页头左侧是俯视的蓝色像素客机（21 列 × 6 行，半块字符绘制，带后掠主翼、发动机和尾翼），右侧显示版本、仓库、分支、工作目录、模型和权限模式；终端窄于 72 列时隐藏客机，路径以 `~` 缩写并保留结尾，详细状态通过 `/status` 查看。圆角输入框提供命令补全、历史搜索、占位提示和快捷键说明，发送后收起为一行消息气泡；执行区展示模型回复、工具结果、代码差异和用量，各块之间统一空一行。

浅色和深色主题都使用蓝色主色，覆盖标题、分支、输入框边框、提示符与快捷键；输入区使用带蓝色的底色。白底终端可运行 `/theme light`，黑底终端可运行 `/theme dark`，选择会保存为后续启动的默认值。

`--theme auto` 默认保留终端原生前景色，避免在白色底上使用浅色字。终端提供 `COLORFGBG` 时可自动选择深浅配色；也可显式使用 `--theme light` 或 `--theme dark`。主题内主文字、辅助文字、强调色与成功/错误文字的对比度至少为 4.5:1，边框至少为 3:1。未知底色时使用原生前景色，不根据 `TERM` 猜测背景。

交互参考 [Claude Code 的官方交互文档](https://code.claude.com/docs/en/interactive-mode)，结合 RepoPilot 现有的目标、任务和权限机制实现。

## 安装与启动

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
repopilot /absolute/path/to/repository

# manual：按现有权限规则审批；auto：无需审批
repopilot --mode manual
repopilot --mode auto
```

Windows PowerShell：

```powershell
py -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/repopilot.exe D:/path/to/repository
```

也可从源码根目录运行 `python main.py` 或 `python -m repopilot`。目标支持普通 Git 仓库与 worktree 的 `.git` 文件。未设置 API 配置时仍可进入界面、查看帮助和仓库状态；发送任务时会提示缺少的配置。

```bash
repopilot /path/to/repository \
  --env-file /path/to/.env \
  --model deepseek/deepseek/deepseek-v4.1-flash \
  --base-url https://api.360.cn/v1
```

`.env` 中配置 `OPENAI_API_KEY`、`MODEL_ID` 和可选的 `OPENAI_BASE_URL`。进程环境优先于 dotenv，命令行的 `--model` 与 `--base-url` 优先于两者，修改仅作用于当前进程。

希望在任何项目中直接输入 `repopilot` 启动时，可以在用户配置 `~/.config/repopilot/config.json` 保存默认值（设置 `XDG_CONFIG_HOME` 时使用该目录）：

```json
{
  "model": "deepseek/deepseek/deepseek-v4.1-flash",
  "base_url": "https://api.360.cn/v1",
  "env_file": "/absolute/path/to/existing/.env",
  "mode": "manual"
}
```

密钥继续从引用的 `.env` 读取。默认值的优先级为：命令行 > 进程环境 > dotenv > 用户 JSON 配置。`--env-file` 优先于用户配置中引用的文件；未指定时，源码运行仍支持从项目根目录向上寻找 `.env`。完成安装与用户配置后，进入任意目标 Git 仓库输入 `repopilot` 即可，不需要激活虚拟环境（若采用用户级安装）或重复指定模型。用户级安装使用 `python3 -m pip install --user -e .`。

权限默认使用 `manual`：Shell、MCP、创建工作树和启动队友等受保护操作按现有规则审批。`auto` 会自动批准这些操作，适用于交互输入、管道和 `--task`，并传递给子任务、工作流和队友。现有的禁用命令与仓库路径边界仍然生效。启动时通过 `--mode manual|auto` 选择；会话中使用 `/mode manual` 或 `/mode auto` 即时切换，保留当前对话。`/mode` 不修改后续启动的默认值；如需保存默认值，在用户 JSON 中设置 `"mode": "auto"`。模式优先级为 `--mode` > `REPOPILOT_MODE` 环境变量（也可在 dotenv 中设置）> 用户 JSON > `manual`。`/status` 或 `/mode` 可查看当前权限模式。

## 命令

| 命令 | 行为 |
| --- | --- |
| `/help` | 查看命令与快捷键 |
| `/status` | 仓库、分支、模型、脱敏后的服务地址、权限与累计用量 |
| `/model [id]` | 查看或切换模型，不丢弃对话；后台工作未结束时拒绝切换 |
| `/mode [manual\|auto]` | 查看或切换当前会话权限模式：需要审批 / 无需审批 |
| `/theme [auto\|light\|dark]` | 查看或切换主题；选择会保存在用户配置中 |
| `/diff [file]` | 查看 HEAD 到工作区的差异，包含暂存与未暂存改动；不展开未跟踪文件 |
| `/tasks` | 查看当前计划和持久任务 |
| `/goal [criteria]` | 查看或设置验收目标；`/goal clear` 清除目标 |
| `/compact` | 摘要压缩对话并归档原文，会调用模型 |
| `/clear` | 清空模型对话，保留文件、记忆、任务和目标；后台工作进行中时拒绝清空 |
| `/export [path.json]` | 导出模型对话和用量；默认位于 `.repopilot/exports/`，拒绝覆盖已有文件 |
| `/exit` | 关闭会话及受管理的进程；也接受 `q`、`exit`、`/quit` |

模型响应使用 Markdown 和代码高亮：标题左对齐，代码块与表格使用主题底色（表格为圆角细边框和隔行底色）。每次工具调用结束后显示为一行摘要，如 `● Read  module.py · 120 lines`；编辑工具展示带文件行号的 diff，新增行绿底、删除行红底，改写的单词再加深标出；Shell 命令显示为带标题栏、退出码与耗时的浅灰终端窗口，只保留输出末尾 8 行；计划以 `✓ ▸ ○` 清单显示。`--verbose` 显示更多输出。模型等待期间显示带流光效果与计时的活动指示器，响应完成后显示文本；当前未实现逐 Token 文本流。用量覆盖当前 Lead 执行上下文，持久队友独立线程的请求尚未合并到该统计。

## 输入与权限

- `Enter` 发送；`Alt+Enter`、`Ctrl+J` 或行尾 `\` 再按 `Enter` 换行。
- `Tab` 补全斜杠命令；方向键访问历史；`Ctrl+R` 搜索历史。
- 空闲时 `Ctrl+C` 丢弃草稿；`Ctrl+D` 退出。
- 等待模型时显示 `⠹ Thinking…  12s  ·  Ctrl+C to stop`，亮带扫过文字表示仍在工作；超过 30 秒的任务结束时响铃并发送桌面通知（OSC 9，iTerm2、WezTerm、kitty 等支持），设置 `REPOPILOT_NOTIFY=0` 关闭。此时按 `Ctrl+C` 会取消正在等待的 HTTP 请求，包括重试等待，并返回输入框；会话保留，可以继续发送下一条任务。已执行的文件修改会保留。工具正在执行时，在工具返回后停止后续操作；已启动的后台命令和持久队友独立运行，退出客户端时由运行时清理。
- Shell 与其他受保护操作显示紧凑授权卡，右下角说明需要审批的原因：`y` 只允许这一次；`a` 在本会话内放行同类操作——普通 Shell 命令一次授权后不再询问，`rm`、`chmod 777` 等破坏性命令仍逐条询问，其余工具只记住完全相同的参数；`Enter` / `n` 拒绝。回答后授权卡下只留一行结果。授权输入不写入任务历史；输入下一条任务不会被视为授权。
- 以 `!` 开头输入进入终端模式：输入框变为绿色 `Terminal`，提示符变为 `$`。回车后命令在仓库目录直接执行（120 秒超时，不经过模型），结果显示在终端窗口中，并在下一条消息中一并交给模型。退格删除 `!` 即回到普通消息。
- 粘贴超过 10 行或 1500 个字符时，输入框与消息气泡中显示为 `[Pasted #1 · 120 lines]`，发送时自动展开为全文。
- `manual` 模式下，单次任务与管道输入不弹出确认，默认拒绝需要审批的操作。可通过 `--allow-command` 预先授权完全一致的命令，不提供模糊前缀授权。`auto` 模式下自动批准，无需逐条预授权。

```bash
repopilot /path/to/repository --task '运行测试并报告结果' \
  --allow-command 'python -m unittest discover -s tests -v'
```

`--plain` 关闭颜色和动画，适合日志及浅色终端，也遵循 `NO_COLOR` 和 `TERM=dumb`。`--task` 在异常或目标未完成时返回非零退出码；取消返回 130。普通交互模式允许修正错误后继续下一轮。

## 离线预览与品牌资源

```bash
repopilot --demo
repopilot --demo --export-svg assets/brand/terminal-preview.svg
python scripts/render_screenshots.py   # 重新生成 README 的四张截图
```

演示使用固定的展示事件，不执行 Shell、不构造 Agent、不调用模型，页面明确标注为离线演示。导出的 SVG 来自同一套实际终端渲染组件。

- `assets/brand/logo-mark.svg`：透明底、纯蓝色几何 `RP` 字母标志。
- `assets/brand/logo.svg` / `logo-dark.svg`：浅色与深色主题的 `RP` 标志，仅含两个字母。
- `assets/brand/terminal-preview.svg` / `terminal-preview-dark.svg`：离线演示的一轮完整对话（浅色 / 深色）。
- `assets/brand/client-light.svg` / `client-dark.svg`：客户端首屏（浅色 / 深色）。

品牌资源保留透明底的蓝色几何 `RP` 字母，SVG 母版位于 `repopilot/cli/assets/logo-mark.svg`。字母用封闭矢量路径绘制，显示不依赖字体。终端页头使用 21 列 × 6 行的蓝色像素客机，每个字符格用 `▀ ▄` 半块字符绘制上下两个像素，像素接近正方形；同色像素同时填充单元格背景，避免字体行距造成横向细缝，无需终端图片协议；右侧展示仓库与模型等信息，`--plain` 关闭颜色。SVG 和 PNG 用于正式品牌资源及图片预览。

README 的 `logo.svg`、`logo-dark.svg` 使用同一 `RP` 标志；四张截图由 `scripts/render_screenshots.py` 通过离线演示生成，使用中性的示例路径 `~/projects/RepoPilot`，不含本机路径。

## 实现与会话数据

`repopilot/cli/app.py` 管理输入、命令与授权；`ui.py` 负责渲染；`theme.py` 统一深浅配色；`composer.py` 将真实 PromptSession 缓冲区包入圆角边框（标题左对齐，终端模式下切换为绿色 `Terminal`），并保留补全菜单和搜索控件。输入框不依赖终端回复 CPR，因此 IDE 内置终端也能显示边框和提示。窗口变窄时，按终端折行后的输入区高度清除旧边框，再重绘输入区，保留草稿。`demo.py` 提供离线演示。单一执行线程串行处理 Lead 的提交、摘要和事件轮次，终端事件循环负责输入。工具结果通过 Agent 的可选事件回调传递，原有程序化接口保持可用。

输入历史保存于目标仓库 `.repopilot/cli/history`。导出的对话可能包含代码和命令输出，应按仓库的数据要求保管；状态卡不会显示 API 密钥。该客户端沿用现有运行时权限，未增加 Shell 或 MCP 的沙箱隔离。
