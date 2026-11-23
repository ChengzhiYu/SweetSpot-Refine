"""
判断用户当前选中的文字处在什么环境里：
    - "edit"：可编辑的输入框（聊天输入框、正在写的邮件、表单、记事本……）-> 润色/翻译后直接替换
    - "read"：只读内容（网页正文、别人发来的 Teams 消息、收到的邮件、PDF……）-> 翻译并弹窗展示

判断依据（按优先级）：
    1. 配置里的应用名单（read_mode_apps / edit_mode_apps），例如 Word 默认按“阅读”处理
    2. Windows UI Automation（读屏软件用的无障碍接口）：当前焦点元素是否可编辑
    3. 兜底：当前窗口是否有闪烁的文本插入符
"""

import ctypes
import logging
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GUI_CARETBLINKING = 0x01

# UI Automation 常量
UIA_ValuePatternId = 10002
UIA_TextPatternId = 10014
UIA_AriaPropertiesPropertyId = 30102
UIA_EditControlTypeId = 50004
UIA_DocumentControlTypeId = 50030


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def foreground_process_name() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _has_blinking_caret() -> bool:
    hwnd = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return False
    return bool(info.flags & GUI_CARETBLINKING) or bool(info.hwndCaret)


def _uia_module():
    try:
        from comtypes.gen import UIAutomationClient
    except ImportError:
        import comtypes.client
        UIAutomationClient = comtypes.client.GetModule("UIAutomationCore.dll")
    return UIAutomationClient


def _focused_element_editable():
    """返回 (是否可编辑 / None 表示无法判断, 诊断描述)"""
    import comtypes
    import comtypes.client

    uia_mod = _uia_module()
    comtypes.CoInitialize()
    try:
        uia = comtypes.client.CreateObject(
            uia_mod.CUIAutomation, interface=uia_mod.IUIAutomation
        )
        el = uia.GetFocusedElement()
        if not el:
            return None, "无焦点元素"
        ct = el.CurrentControlType
        desc = f"控件类型={ct} 类名={el.CurrentClassName!r}"

        vp = None
        try:
            vp = el.GetCurrentPattern(UIA_ValuePatternId)
            if vp:
                vp = vp.QueryInterface(uia_mod.IUIAutomationValuePattern)
        except Exception:
            vp = None
        if vp:
            read_only = bool(vp.CurrentIsReadOnly)
            return not read_only, f"{desc} Value.IsReadOnly={read_only}"

        # 浏览器内核（Edge / Chrome / 新版 Teams / 新版 Outlook）会在 AriaProperties 里
        # 标注 readonly=true；可编辑的富文本框（contenteditable，如 Teams / 邮件编辑区）
        # 没有 ValuePattern，但支持 TextPattern 且不带 readonly
        aria = ""
        try:
            aria = str(el.GetCurrentPropertyValue(UIA_AriaPropertiesPropertyId) or "")
        except Exception:
            pass
        if "readonly=true" in aria.lower():
            return False, f"{desc} (aria readonly)"
        if ct == UIA_EditControlTypeId:
            return True, f"{desc} (Edit 控件)"
        if aria and el.GetCurrentPattern(UIA_TextPatternId):
            return True, f"{desc} (可编辑富文本)"
        if ct == UIA_DocumentControlTypeId:
            caret = _has_blinking_caret()
            return caret, f"{desc} (Document，插入符={caret})"
        return False, f"{desc} (非文本输入控件)"
    finally:
        comtypes.CoUninitialize()


def detect_mode(cfg: dict) -> str:
    """返回 "edit"（替换）或 "read"（翻译并弹窗展示）"""
    if not cfg.get("smart_mode", True):
        return "edit"

    proc = foreground_process_name()
    proc_l = proc.lower()
    if proc_l in {a.lower() for a in cfg.get("edit_mode_apps", [])}:
        logging.info("模式判断：%s 在 edit_mode_apps 中 -> edit", proc)
        return "edit"
    if proc_l in {a.lower() for a in cfg.get("read_mode_apps", [])}:
        logging.info("模式判断：%s 在 read_mode_apps 中 -> read", proc)
        return "read"

    try:
        editable, desc = _focused_element_editable()
    except Exception as e:
        logging.warning("UI Automation 判断失败（%s），改用插入符判断", e)
        editable, desc = None, "UIA 失败"
    if editable is None:
        editable = _has_blinking_caret()
        desc += f"，插入符={editable}"

    mode = "edit" if editable else "read"
    logging.info("模式判断：%s %s -> %s", proc, desc, mode)
    return mode
