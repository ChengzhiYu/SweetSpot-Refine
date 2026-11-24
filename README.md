# SweetSpot

Windows 上的全局划词翻译 / 润色小工具。在任意软件里选中一段文字，一键翻译或改写成地道的职场英文，由 Google Gemini 驱动。

| 动作 | 默认快捷键 | 效果 |
|---|---|---|
| **翻译** | `Ctrl+Alt+W` | 外文译成中文、中文译成英文，结果显示在选区旁的卡片里，**不改动原文**。适合看网页、邮件、Teams 消息、文档 |
| **Refine** | `Ctrl+Alt+T` | 中文 → 地道专业的英文；英文 → 润色成 native 级别的职场表达。结果**直接替换**选中的文字。适合写邮件、回消息 |

除了快捷键，用鼠标划选文字后，光标旁会出现一个小的「翻译 | Refine」浮动按钮，点一下即可。

> 仅支持 Windows 10 / 11。

---

## 快速开始（下载 exe）

1. 到本仓库的 [Releases](../../releases) 页面下载最新的 `SweetSpot.exe`，**放进一个单独的文件夹**（配置文件和日志会生成在 exe 旁边）。
2. 双击运行。第一次运行会弹出「欢迎使用 SweetSpot」窗口：
   1. 点窗口里的「打开 Google AI Studio」，用 Google 账号登录，点 **Create API key**（有免费额度）；
   2. 复制生成的 key，粘贴到窗口的输入框，点「保存」。
3. 程序会自动验证 key：无效会提示原因并让你重新输入；有效就保存好，右下角托盘出现图标，可以开始用了。

暂时没有 key 也可以点「稍后再说」，程序照常在托盘运行，之后随时右键托盘图标 →「设置 API Key…」完成设置。更换 key 也用这个菜单。key 保存在 exe 旁边自动生成的 `config.json` 里。

想开机自动启动：按 `Win+R`，输入 `shell:startup` 回车，把 `SweetSpot.exe` 的**快捷方式**放进打开的文件夹。

> **Windows 弹出“已保护你的电脑”？** 这是因为 exe 没有数字签名。点“更多信息” →“仍要运行”即可。如果不放心，可以按下面的方式从源码运行或自己打包。

## 使用方法

- **翻译**：选中文字 → 按 `Ctrl+Alt+W`，或点浮动按钮的「翻译」。结果卡片里可以点「复制」，或选中部分译文后按 `Ctrl+C`。点卡片外任意位置、点 ✕ 或按 `Esc` 关闭。
- **Refine**：在输入框里选中文字 → 按 `Ctrl+Alt+T`，或点浮动按钮的「Refine」。几秒后选中的文字会被替换。替换不满意可以直接 `Ctrl+Z` 撤销。
- **托盘菜单**（右键托盘图标）：手动触发两个动作、更改快捷键、开关浮动按钮、设置 API Key、打开配置文件、查看日志、退出。

## 配置项（config.json）

修改后需要退出并重新运行 SweetSpot 才生效（快捷键和浮动按钮开关可以直接在托盘菜单里改，立即生效）。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `gemini_api_key` | — | **必填**，你的 Gemini API Key。推荐用托盘菜单「设置 API Key…」填写，会自动验证 |
| `gemini_model` | `gemini-2.5-flash` | 使用的模型 |
| `gemini_fallback_models` | `["gemini-2.5-flash", "gemini-2.5-flash-lite"]` | 主模型繁忙（HTTP 429 / 5xx）时依次改用的备用模型 |
| `retry_count` | `2` | 每个模型遇到临时错误时的重试次数。设为 `0` 则立即改用备用模型 |
| `translate_hotkey` | `ctrl+alt+w` | 「翻译」快捷键 |
| `refine_hotkey` | `ctrl+alt+t` | 「Refine」快捷键 |
| `read_target_language` | `简体中文` | 「翻译」时外文译成的语言（中文原文始终译成英文） |
| `selection_popup` | `true` | 划选文字后是否显示浮动按钮 |
| `popup_timeout` | `4` | 浮动按钮多少秒无操作后自动消失 |
| `popup_require_ibeam` | `true` | 只在文本光标（I 形）上划选时才弹出按钮，避免拖窗口、拖文件时误弹。某个软件里划词不弹按钮时，可改为 `false` |
| `request_timeout` | `20` | 单次请求超时（秒） |
| `copy_wait` / `paste_wait` | `0.15` / `0.10` | 模拟复制 / 粘贴后的等待时间（秒）。偶尔取不到选中文字时可以调大 |
| `cjk_ratio_threshold` | `0.15` | 中文字符占比超过该值即视为中文文本 |

快捷键写法：`ctrl` / `alt` / `shift` / `windows` 加字母、数字或功能键，用 `+` 连接，例如 `ctrl+shift+e`。

## 常见问题

**按快捷键没反应**
- 确认托盘里有 SweetSpot 图标，并且只运行了一个。
- 如果当前软件是以管理员身份运行的，SweetSpot 也需要以管理员身份运行，否则 Windows 会阻止它在该软件里接收快捷键和模拟复制粘贴。
- 快捷键可能和其他软件冲突，在托盘菜单里换一个试试。

**提示“未检测到选中文本”**：有些软件复制较慢，把 `copy_wait` 调大一点（例如 `0.3`）。

**提示 `Gemini HTTP 503 ... high demand`**：Google 服务器繁忙，程序会自动重试并切换到备用模型。持续出现可以稍后再试，或把 `gemini_model` 换成其他模型。

**提示 `API key not valid`**：key 没有完整复制，或已在 Google AI Studio 中被删除。右键托盘图标 →「设置 API Key…」重新粘贴。

**其他问题**：托盘菜单 →「查看日志」，`translator.log` 里有每次请求的详细记录。

## 隐私说明

- 选中的文字会发送给 Google Gemini API 处理，请勿用于不允许外发的敏感内容。
- API Key 只保存在你本机的 `config.json` 里。分享程序时只发 `SweetSpot.exe`，**不要把 `config.json` 一起发出去**。

---

## 从源码运行

需要 Windows 10 / 11 和 Python 3.9 或更高版本。

```powershell
git clone https://github.com/ChengzhiYu/SweetSpot-Refine.git
cd SweetSpot-Refine\selection-translator
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python translator.py
```

第一次运行会弹窗引导填写 API Key，配置保存在 `selection-translator\config.json`。

## 自己打包 exe

```powershell
cd SweetSpot-Refine\selection-translator
.\.venv\Scripts\pip install -r requirements-build.txt
.\.venv\Scripts\pyinstaller --noconfirm SweetSpot.spec
```

生成的程序在 `dist\SweetSpot.exe`。打包前请先退出正在运行的 SweetSpot，否则旧文件被占用会导致打包失败。

修改图标：编辑 `icon_art.py` 后运行 `.\.venv\Scripts\python icon_art.py` 重新生成 `app.ico`，再重新打包。

## 项目结构

```
selection-translator/
├── translator.py        主程序：配置、调用 Gemini、快捷键、托盘菜单
├── selection_popup.py   划词浮动按钮和翻译结果卡片
├── key_dialog.py        「设置 API Key」对话框
├── icon_art.py          用代码绘制的应用图标（生成 app.ico）
├── app.ico              exe 图标
├── SweetSpot.spec       PyInstaller 打包配置
├── requirements.txt     运行依赖
└── requirements-build.txt  打包依赖
```
