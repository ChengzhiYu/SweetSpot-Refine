"""
开机自动启动：写入当前用户的启动项（HKCU\\...\\CurrentVersion\\Run），不需要管理员权限，
也会出现在任务管理器的「启动应用」列表里。

用户还可能在任务管理器 / 设置里把启动项“禁用”，那会记录在 StartupApproved\\Run 下，
这里一并读取和清除，保证菜单里的勾选状态和实际行为一致。
"""

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
DEFAULT_NAME = "SweetSpot"


def launch_command() -> str:
    """开机时要执行的命令：打包后的 exe；从源码运行时用无控制台的 pythonw 启动脚本"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    script = os.path.abspath(sys.modules["__main__"].__file__)
    return f'"{pythonw}" "{script}"'


def _read(key_path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _disabled_in_settings(name: str) -> bool:
    data = _read(APPROVED_KEY, name)
    # 首字节 0x02 / 0x06 表示启用，0x03 / 0x07 表示被用户禁用
    return isinstance(data, bytes) and len(data) > 0 and data[0] & 0x01 == 1


def is_enabled(name: str = DEFAULT_NAME) -> bool:
    return _read(RUN_KEY, name) is not None and not _disabled_in_settings(name)


def enable(name: str = DEFAULT_NAME):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, launch_command())
    # 清除之前在任务管理器里“禁用”的记录，否则写了启动项也不会运行
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


def disable(name: str = DEFAULT_NAME):
    for path in (RUN_KEY, APPROVED_KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            pass


def refresh_path(name: str = DEFAULT_NAME) -> bool:
    """
    已开启自启动、但 exe 被移动过位置时，把启动项更新为当前路径。
    返回是否做了更新。
    """
    current = _read(RUN_KEY, name)
    if current is None or current == launch_command():
        return False
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, launch_command())
    return True
