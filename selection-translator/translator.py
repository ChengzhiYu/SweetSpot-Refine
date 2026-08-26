"""
SweetSpot Refine —— 全局划词翻译 / 润色工具
------------------------
选中任意软件里的文本 -> 按快捷键 (默认 Ctrl+Alt+T) 或右键点击托盘图标选择菜单项
-> 自动复制选中内容 -> 调用大模型 (Gemini) -> 用返回结果替换选中文本

判断逻辑：
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
from PIL import Image, ImageDraw

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
    "hotkey": "ctrl+alt+t",
    "copy_wait": 0.15,
    "paste_wait": 0.10,
    "request_timeout": 20,
    "cjk_ratio_threshold": 0.15
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"未找到配置文件，已在以下位置生成模板：\n{CONFIG_PATH}\n"
              f"请填写 API Key 等信息后重新运行本程序。")
        input("按回车键退出...")
        sys.exit(0)

    cfg = DEFAULT_CONFIG.copy()
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except json.JSONDecodeError as e:
        print(f"config.json 格式错误：{e}")
        input("按回车键退出...")
        sys.exit(1)
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


def call_gemini(system: str, user: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=CFG["gemini_api_key"])
    model = genai.GenerativeModel(CFG["gemini_model"], system_instruction=system)
    resp = model.generate_content(
        user, request_options={"timeout": CFG["request_timeout"]}
    )
    return resp.text.strip()


def call_llm(text: str) -> str:
    system, user = build_prompt(text)
    return call_gemini(system, user)


# ---------------------------------------------------------------------------
# 核心流程：复制选中文本 -> 调用大模型 -> 粘贴替换
# ---------------------------------------------------------------------------
_busy_lock = threading.Lock()
_icon = None  # pystray.Icon 实例，在 run_tray() 中赋值


def notify(title: str, message: str):
    logging.info("%s: %s", title, message)
    try:
        if _icon is not None:
            _icon.notify(message, title)
    except Exception:
        pass


def do_translate_or_polish():
    if not _busy_lock.acquire(blocking=False):
        notify("SweetSpot Refine", "上一个请求还在处理，请稍候")
        return
    try:
        logging.info("触发时前台窗口：%s", get_foreground_window_title())
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

        # 触发热键时，用户手指往往还按着 ctrl/alt 等修饰键没松开；
        # 这里强制发送“松开”事件清掉残留状态，避免发送 ctrl+c 时
        # 被系统当成 ctrl+alt+c 之类的组合键，导致复制不生效
        for key in CFG["hotkey"].split("+"):
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

        if not selected or not selected.strip():
            notify("SweetSpot Refine", "未检测到选中文本")
            return

        notify("SweetSpot Refine", "处理中…")
        try:
            result = call_llm(selected)
        except Exception as e:
            logging.exception("调用大模型失败")
            notify("SweetSpot Refine", f"请求失败：{e}")
            return

        if not result:
            notify("SweetSpot Refine", "模型未返回内容")
            return

        pyperclip.copy(result)
        time.sleep(CFG["paste_wait"])
        keyboard.send("ctrl+v")
        notify("SweetSpot Refine", "已替换完成")

        # 延迟恢复用户原来剪贴板里的内容，避免影响后续正常粘贴操作
        def restore():
            time.sleep(1.5)
            if original_clipboard is not None:
                try:
                    pyperclip.copy(original_clipboard)
                except Exception:
                    pass

        threading.Thread(target=restore, daemon=True).start()

    finally:
        _busy_lock.release()


def trigger():
    threading.Thread(target=do_translate_or_polish, daemon=True).start()


# ---------------------------------------------------------------------------
# 系统托盘图标（右键菜单）
# ---------------------------------------------------------------------------
def make_icon_image():
    img = Image.new("RGB", (64, 64), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((4, 4, 60, 60), fill=(30, 144, 255))
    d.text((22, 20), "T", fill="white")
    return img


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


def change_hotkey(icon=None, item=None):
    """弹出输入框，让用户输入新的快捷键组合，成功后热更新并写回 config.json"""

    def worker():
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        new_hotkey = simpledialog.askstring(
            "SweetSpot Refine",
            "请输入新的快捷键组合，例如：ctrl+alt+t\n"
            "支持 ctrl / alt / shift / windows 加字母、数字或功能键，用 + 连接。",
            initialvalue=CFG["hotkey"],
            parent=root,
        )
        root.destroy()

        if not new_hotkey:
            return
        new_hotkey = new_hotkey.strip().lower()

        if new_hotkey == CFG["hotkey"]:
            notify("SweetSpot Refine", "快捷键未变化")
            return

        try:
            keyboard.add_hotkey(new_hotkey, trigger)
        except Exception as e:
            notify("SweetSpot Refine", f"快捷键格式不正确或注册失败：{e}")
            return

        try:
            keyboard.remove_hotkey(CFG["hotkey"])
        except (KeyError, ValueError):
            pass

        old_hotkey = CFG["hotkey"]
        CFG["hotkey"] = new_hotkey
        try:
            CONFIG_PATH.write_text(
                json.dumps(CFG, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logging.exception("写入 config.json 失败")

        logging.info("快捷键已从 %s 更新为 %s", old_hotkey, new_hotkey)
        notify("SweetSpot Refine", f"快捷键已更新为：{new_hotkey}")

    threading.Thread(target=worker, daemon=True).start()


def run_tray():
    global _icon
    menu = pystray.Menu(
        Item("翻译 / 润色选中文本", lambda icon, item: trigger(), default=True),
        Item(lambda item: f"当前快捷键：{CFG['hotkey']}", None, enabled=False),
        Item("更改快捷键…", change_hotkey),
        pystray.Menu.SEPARATOR,
        Item("打开配置文件 config.json", open_config),
        Item("查看日志", open_log),
        pystray.Menu.SEPARATOR,
        Item("退出", on_quit),
    )
    _icon = pystray.Icon("sweetspot_refine", make_icon_image(), "SweetSpot Refine", menu)
    _icon.run()  # 阻塞，进入托盘消息循环


def main():
    keyboard.add_hotkey(CFG["hotkey"], trigger)
    logging.info("服务已启动，快捷键：%s", CFG["hotkey"])
    run_tray()


if __name__ == "__main__":
    main()
