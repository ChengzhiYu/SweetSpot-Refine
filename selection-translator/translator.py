"""
SweetSpot —— 全局划词翻译 / 润色工具
------------------------
选中任意软件里的文本，然后：
    按「翻译」快捷键（默认 Ctrl+Alt+W），或点浮动按钮上的「翻译」
        -> 翻译（外文译成中文，中文译成英文），在选区旁的卡片中展示，不改动原文
    按「Refine」快捷键（默认 Ctrl+Alt+T），或点浮动按钮上的「Refine」
        -> 用结果直接替换选中文本
           文本以中文为主 -> 翻译成地道专业的英文
           文本以英文为主 -> 润色为 native 级别的职场表达
"""

import os
import re
import sys
import time
import json
import ctypes
import logging
import threading
from pathlib import Path

import pyperclip
import keyboard
import pystray
from pystray import MenuItem as Item

# ---------------------------------------------------------------------------
# 路径处理：兼容直接运行 .py 和用 PyInstaller 打包成 .exe 两种情况
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parent

CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "translator.log"

DEFAULT_CONFIG = {
    "gemini_api_key": "在这里填写你的-gemini-key",
    "gemini_model": "gemini-2.5-flash",
    # 主模型繁忙 (429/5xx) 重试仍失败时，依次改用这些模型
    "gemini_fallback_models": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
    # 每个模型遇到临时错误时的重试次数
    "retry_count": 2,
    # Refine：润色 / 中译英，并替换选中文本
    "refine_hotkey": "ctrl+alt+t",
    # 翻译：翻译选中文本，在卡片中展示
    "translate_hotkey": "ctrl+alt+w",
    # 用鼠标划选文字后，在光标旁弹出「翻译 | Refine」浮动按钮
    "selection_popup": True,
    # 浮动按钮无操作多少秒后自动消失
    "popup_timeout": 4,
    # 只在 I 型文本光标上划选时弹出（过滤拖窗口、拖文件等）；某些软件不弹时可改为 false
    "popup_require_ibeam": True,
    # 「翻译」时，外文翻译成的目标语言（中文原文则译成英文）
    "read_target_language": "简体中文",
    "copy_wait": 0.15,
    "paste_wait": 0.10,
    "request_timeout": 20,
    "cjk_ratio_threshold": 0.15
}


def show_startup_message(message: str, *, error: bool = False) -> None:
    icon = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, message, "SweetSpot", icon)


def load_config() -> dict:
    # 第一次运行：生成默认配置，启动后会弹窗让用户填写 API Key
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    cfg = DEFAULT_CONFIG.copy()
    try:
        user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        show_startup_message(f"config.json 格式错误：{e}", error=True)
        sys.exit(1)

    # 兼容旧版配置：原来的单个 hotkey 即现在的 Refine 快捷键；智能识别相关配置已废弃
    migrated = False
    if "hotkey" in user_cfg:
        user_cfg.setdefault("refine_hotkey", user_cfg["hotkey"])
    for key in ("hotkey", "smart_mode", "read_mode_apps", "edit_mode_apps"):
        if key in user_cfg:
            del user_cfg[key]
            migrated = True
    cfg.update(user_cfg)
    if migrated:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


CFG = load_config()

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)

CJK_PATTERN = re.compile("[一-鿿㐀-䶿]")


def get_foreground_window_title() -> str:
    """返回当前前台窗口标题，用于诊断触发热键时焦点到底在哪个窗口"""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or "(无标题)"
    except Exception:
        return "(获取失败)"


def is_chinese(text: str) -> bool:
    """粗略判断文本是否以中文为主"""
    cjk_count = len(CJK_PATTERN.findall(text))
    ratio = cjk_count / max(len(text), 1)
    return ratio > CFG["cjk_ratio_threshold"]


def build_read_prompt(text: str):
    """翻译：外文 -> 目标语言；中文 -> 英文"""
    target = CFG.get("read_target_language", "简体中文")
    if is_chinese(text):
        target, label = "English", "中文 → English"
    else:
        label = f"→ {target}"
    system = (
        f"你是一名专业译者。请把用户提供的文本翻译成{target}，帮助读者快速准确地理解原文。"
        "要求：忠实准确、通顺自然；保留原文的段落、换行和列表结构；"
        "人名、产品名、代码、链接等专有内容保留原文；"
        "不要添加任何解释或备注，不要用引号包裹结果，只输出译文本身。"
    )
    return system, text, label


def build_prompt(text: str):
    if is_chinese(text):
        system = (
            "你是一名资深的双语商务/技术翻译专家。"
            "请将用户提供的中文文本翻译成地道、专业、符合英语母语者表达习惯的英文。"
            "要求：语义准确、用词自然、语气得体，不要逐字直译，不要添加任何解释或备注，"
            "不要用引号包裹结果，只输出翻译后的文本本身。"
        )
    else:
        system = (
            "你是一名资深的英语母语职场写作顾问。"
            "请将用户提供的英文文本润色为地道、自然、专业的 native speaker 职场表达。"
            "要求：保持原意不变，修正生硬或中式英语的表达方式，提升用词和语气的专业度，"
            "不要添加任何解释或备注，不要用引号包裹结果，只输出润色后的文本本身。"
        )
    return system, text


GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


class TransientError(RuntimeError):
    """服务器繁忙 / 网络抖动等可重试的错误"""


def api_key_missing() -> bool:
    key = str(CFG.get("gemini_api_key", "")).strip()
    return not key or not key.isascii() or key == DEFAULT_CONFIG["gemini_api_key"]


def check_api_key(key: str):
    """
    用一次轻量请求（列出模型）验证 key。
    返回 (True, "") 有效；(False, 原因) 无效；(None, 原因) 网络等原因暂时无法验证
    """
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
        headers={"x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=CFG["request_timeout"]):
            return True, ""
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except Exception:
            pass
        if e.code in (400, 401, 403):
            return False, f"Google 拒绝了这个 key：{detail}"
        return None, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return None, str(e)


def prompt_api_key(first_run: bool = False) -> bool:
    """弹窗让用户输入 API Key，验证通过后保存。返回是否已保存新 key（阻塞调用线程）"""
    from icon_art import make_icon
    from key_dialog import ask_api_key

    initial = "" if api_key_missing() else CFG["gemini_api_key"]
    error = ""
    while True:
        key = _popup.run_sync(
            lambda root: ask_api_key(root, make_icon, initial, error, first_run)
        )
        if not key:
            return False
        ok, reason = check_api_key(key)
        if ok is False:
            logging.warning("API Key 验证未通过：%s", reason)
            initial, error = key, reason
            continue
        CFG["gemini_api_key"] = key
        save_config()
        if ok:
            logging.info("API Key 已验证并保存")
            notify("SweetSpot", "API Key 已保存，可以开始使用了")
        else:
            logging.warning("API Key 暂时无法验证（%s），已先保存", reason)
            notify("SweetSpot", "API Key 已保存（暂时无法联网验证，请稍后试用）")
        return True


def call_gemini(system: str, user: str) -> str:
    if api_key_missing():
        raise RuntimeError("还没有设置 API Key：请右键托盘图标 →「设置 API Key…」")
    api_key = str(CFG["gemini_api_key"]).strip()

    models = [CFG["gemini_model"]]
    for m in CFG.get("gemini_fallback_models") or []:
        if m and m not in models:
            models.append(m)
    retries = max(int(CFG.get("retry_count", 2)), 0)

    last_err = None
    for model in models:
        for attempt in range(retries + 1):
            try:
                result = _gemini_request(model, api_key, system, user)
                if model != CFG["gemini_model"]:
                    logging.info("主模型不可用，已改用 %s", model)
                return result
            except TransientError as e:
                last_err = e
                logging.warning("%s 第 %d 次请求失败：%s", model, attempt + 1, e)
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
    raise last_err


def _gemini_request(model: str, api_key: str, system: str, user: str) -> str:
    # 直接调用 Gemini REST API（仅用标准库），避免 google-generativeai 引入
    # grpc / cryptography 等原生依赖导致 PyInstaller 打包后 DLL 加载失败
    import socket
    import urllib.request
    import urllib.error
    from urllib.parse import quote

    url = GEMINI_ENDPOINT.format(model=quote(model, safe=""))
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=CFG["request_timeout"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except Exception:
            pass
        cls = TransientError if e.code in RETRYABLE_HTTP_CODES else RuntimeError
        raise cls(f"Gemini HTTP {e.code} ({model}): {detail}") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise TransientError(f"网络错误 ({model}): {e}") from None

    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "无候选结果")
        raise RuntimeError(f"Gemini 未返回内容：{reason}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


def call_llm(text: str) -> str:
    system, user = build_prompt(text)
    return call_gemini(system, user)


def call_llm_read(text: str):
    system, user, label = build_read_prompt(text)
    return call_gemini(system, user), label


# ---------------------------------------------------------------------------
# 核心流程：复制选中文本 -> 调用大模型 -> 粘贴替换
# ---------------------------------------------------------------------------
_busy_lock = threading.Lock()
_icon = None  # pystray.Icon 实例，在 run_tray() 中赋值
_popup = None  # SelectionPopup 实例，在 main() 中赋值


def save_config():
    try:
        CONFIG_PATH.write_text(
            json.dumps(CFG, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        logging.exception("写入 config.json 失败")


def notify(title: str, message: str):
    logging.info("%s: %s", title, message)
    try:
        if _icon is not None:
            _icon.notify(message, title)
    except Exception:
        pass


ACTION_NAMES = {"translate": "翻译", "refine": "Refine"}


def hotkey_of(action: str) -> str:
    return CFG[f"{action}_hotkey"]


def copy_selection(release_keys: str = ""):
    """
    模拟 Ctrl+C 取得选中文本。返回 (选中文本, 原剪贴板内容)。
    release_keys : 触发用的快捷键。用户手指往往还按着 ctrl/alt 等修饰键没松开，
                   先强制“松开”，避免 Ctrl+C 被系统当成 Ctrl+Alt+C 之类的组合键导致复制失败
    """
    original_clipboard = None
    try:
        original_clipboard = pyperclip.paste()
    except Exception:
        pass

    # 先清空剪贴板，便于判断“复制”是否真的拿到了新内容
    try:
        pyperclip.copy("")
    except Exception:
        pass

    if release_keys:
        for key in release_keys.split("+"):
            try:
                keyboard.release(key.strip())
            except Exception:
                pass
        time.sleep(0.05)

    keyboard.send("ctrl+c")
    time.sleep(CFG["copy_wait"])

    try:
        selected = pyperclip.paste()
    except Exception:
        selected = ""
    return selected, original_clipboard


def restore_clipboard(content, delay: float = 0.0):
    if content is None:
        return

    def restore():
        time.sleep(delay)
        try:
            pyperclip.copy(content)
        except Exception:
            pass

    if delay:
        threading.Thread(target=restore, daemon=True).start()
    else:
        restore()


def run_action(action: str, from_hotkey: bool = True, anchor=None):
    """
    action      : "translate"（翻译并在卡片中展示）或 "refine"（润色 / 中译英并替换选中文本）
    from_hotkey : 由快捷键触发时为 True，需先松开残留的修饰键
    anchor      : 选区附近的屏幕矩形，用于摆放翻译卡片；None 表示用鼠标位置
    """
    if not _busy_lock.acquire(blocking=False):
        notify("SweetSpot", "上一个请求还在处理，请稍候")
        return
    try:
        logging.info("%s：前台窗口 %s", ACTION_NAMES[action], get_foreground_window_title())
        selected, original_clipboard = copy_selection(hotkey_of(action) if from_hotkey else "")

        if not selected or not selected.strip():
            restore_clipboard(original_clipboard)
            notify("SweetSpot", "未检测到选中文本")
            return

        if action == "translate":
            restore_clipboard(original_clipboard)  # 翻译不需要粘贴，立即恢复剪贴板
            show_translation(selected, anchor)
        else:
            refine_and_replace(selected, original_clipboard)
    finally:
        _busy_lock.release()


def refine_and_replace(selected: str, original_clipboard):
    notify("SweetSpot", "处理中…")
    try:
        result = call_llm(selected)
    except Exception as e:
        logging.exception("调用大模型失败")
        notify("SweetSpot", f"请求失败：{e}")
        restore_clipboard(original_clipboard)
        return

    if not result:
        notify("SweetSpot", "模型未返回内容")
        restore_clipboard(original_clipboard)
        return

    pyperclip.copy(result)
    time.sleep(CFG["paste_wait"])
    keyboard.send("ctrl+v")
    notify("SweetSpot", "已替换完成")
    # 延迟恢复用户原来剪贴板里的内容，避免影响后续正常粘贴操作
    restore_clipboard(original_clipboard, delay=1.5)


def show_translation(text: str, anchor):
    """翻译选中文字，并在选区旁的卡片里展示结果"""
    _popup.show_card(anchor, "翻译中", "翻译中…", kind="loading")
    try:
        result, label = call_llm_read(text)
    except Exception as e:
        logging.exception("调用大模型失败")
        _popup.show_card(anchor, "翻译失败", str(e), kind="error")
        return
    if not result:
        _popup.show_card(anchor, "翻译失败", "模型未返回内容", kind="error")
        return
    _popup.show_card(anchor, f"翻译  {label}", result)
    logging.info("翻译：已展示结果（%d 字）", len(result))


def trigger(action: str, from_hotkey: bool = True):
    threading.Thread(target=run_action, args=(action, from_hotkey), daemon=True).start()


# ---------------------------------------------------------------------------
# 系统托盘图标（右键菜单）
# ---------------------------------------------------------------------------
def make_icon_image():
    from icon_art import make_icon
    return make_icon(64)


def open_log(icon=None, item=None):
    try:
        os.startfile(LOG_PATH)
    except Exception:
        pass


def open_config(icon=None, item=None):
    try:
        os.startfile(CONFIG_PATH)
    except Exception:
        pass


def on_quit(icon=None, item=None):
    logging.info("用户退出程序")
    if _icon is not None:
        _icon.stop()
    os._exit(0)


def change_hotkey(action: str):
    """弹出输入框，让用户输入新的快捷键组合，成功后热更新并写回 config.json"""
    name = ACTION_NAMES[action]

    def worker():
        new_hotkey = _popup.ask_string(
            "SweetSpot",
            f"请输入「{name}」的新快捷键组合，例如：ctrl+alt+t\n"
            "支持 ctrl / alt / shift / windows 加字母、数字或功能键，用 + 连接。",
            initialvalue=hotkey_of(action),
        )

        if not new_hotkey:
            return
        new_hotkey = new_hotkey.strip().lower()
        old_hotkey = hotkey_of(action)

        if new_hotkey == old_hotkey:
            notify("SweetSpot", "快捷键未变化")
            return
        other = "refine" if action == "translate" else "translate"
        if new_hotkey == hotkey_of(other):
            notify("SweetSpot", f"该快捷键已被「{ACTION_NAMES[other]}」使用")
            return

        try:
            keyboard.add_hotkey(new_hotkey, lambda: trigger(action))
        except Exception as e:
            notify("SweetSpot", f"快捷键格式不正确或注册失败：{e}")
            return

        try:
            keyboard.remove_hotkey(old_hotkey)
        except (KeyError, ValueError):
            pass

        CFG[f"{action}_hotkey"] = new_hotkey
        save_config()

        logging.info("「%s」快捷键已从 %s 更新为 %s", name, old_hotkey, new_hotkey)
        notify("SweetSpot", f"「{name}」快捷键已更新为：{new_hotkey}")

    threading.Thread(target=worker, daemon=True).start()


def set_api_key(icon=None, item=None):
    threading.Thread(target=prompt_api_key, daemon=True).start()


def toggle_selection_popup(icon=None, item=None):
    CFG["selection_popup"] = not CFG.get("selection_popup", True)
    save_config()
    if not CFG["selection_popup"] and _popup is not None:
        _popup.dismiss()
    logging.info("划词浮动按钮：%s", "开启" if CFG["selection_popup"] else "关闭")


def run_tray():
    global _icon
    menu = pystray.Menu(
        Item(
            lambda item: f"翻译选中文本\t{hotkey_of('translate')}",
            lambda icon, item: trigger("translate", from_hotkey=False),
        ),
        Item(
            lambda item: f"Refine 选中文本\t{hotkey_of('refine')}",
            lambda icon, item: trigger("refine", from_hotkey=False),
            default=True,
        ),
        pystray.Menu.SEPARATOR,
        Item("更改「翻译」快捷键…", lambda icon, item: change_hotkey("translate")),
        Item("更改「Refine」快捷键…", lambda icon, item: change_hotkey("refine")),
        Item(
            "划词后显示浮动按钮",
            toggle_selection_popup,
            checked=lambda item: CFG.get("selection_popup", True),
        ),
        pystray.Menu.SEPARATOR,
        Item("设置 API Key…", set_api_key),
        Item("打开配置文件 config.json", open_config),
        Item("查看日志", open_log),
        pystray.Menu.SEPARATOR,
        Item("退出", on_quit),
    )
    _icon = pystray.Icon("sweetspot", make_icon_image(), "SweetSpot", menu)
    _icon.run()  # 阻塞，进入托盘消息循环


def main():
    global _popup
    from icon_art import make_icon
    from selection_popup import SelectionPopup, enable_dpi_awareness

    enable_dpi_awareness()  # 必须在创建任何窗口之前
    _popup = SelectionPopup(
        CFG,
        lambda action, anchor: run_action(action, from_hotkey=False, anchor=anchor),
        make_icon,
    )
    _popup.start()
    # 选中文字后开始打字，说明用户不需要按钮了
    # Esc 关闭翻译卡片
    keyboard.on_press(lambda e: _popup.on_key(e.name))

    # 第一次运行（或 key 无效）：弹窗引导填写 API Key；用户选择“稍后再说”也照常启动
    if api_key_missing() and not prompt_api_key(first_run=True):
        show_startup_message(
            "还没有设置 API Key，翻译和 Refine 暂时无法使用。\n"
            "SweetSpot 会继续在托盘中运行，随时可以右键托盘图标 →「设置 API Key…」完成设置。"
        )

    for action in ("translate", "refine"):
        try:
            keyboard.add_hotkey(hotkey_of(action), lambda a=action: trigger(a))
        except Exception as e:
            logging.error("注册「%s」快捷键 %s 失败：%s", ACTION_NAMES[action], hotkey_of(action), e)
            show_startup_message(
                f"「{ACTION_NAMES[action]}」快捷键 {hotkey_of(action)} 注册失败：{e}\n"
                "可在托盘菜单中重新设置。",
                error=True,
            )
    logging.info(
        "服务已启动，翻译：%s，Refine：%s", hotkey_of("translate"), hotkey_of("refine")
    )
    run_tray()


if __name__ == "__main__":
    main()
