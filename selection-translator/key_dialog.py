"""
「设置 API Key」对话框：第一次运行或从托盘菜单打开，引导用户申请并粘贴 Gemini API Key。
必须在 UI 线程中调用（通过 SelectionPopup.run_sync）。
"""

import webbrowser

AI_STUDIO_URL = "https://aistudio.google.com/apikey"

BG = "#FFFFFF"
FG = "#18181B"
MUTED = "#52525B"
ACCENT = "#7C3AED"
ACCENT_HOVER = "#6D28D9"
ERROR = "#DC2626"
BORDER = "#D4D4D8"


def _force_foreground(win):
    """
    把对话框带到最前面并获得键盘焦点。
    Windows 会阻止后台进程抢前台，直接 SetForegroundWindow 常常无效，
    这里临时把本线程的输入状态挂到当前前台窗口的线程上再切换，这是系统允许的做法。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]

    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_tid = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != my_tid and user32.AttachThreadInput(my_tid, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)


def ask_api_key(root, icon_factory, initial: str = "", error: str = "", first_run: bool = False):
    """弹出对话框，返回用户输入的 key（已去掉首尾空白和引号）；取消则返回 None"""
    import tkinter as tk
    from PIL import ImageTk

    s = root.winfo_fpixels("1i") / 96.0
    px = lambda v: round(v * s)  # noqa: E731
    font = ("Microsoft YaHei UI", 10)

    win = tk.Toplevel(root)
    win.title("SweetSpot · 设置 API Key")
    win.configure(bg=BG)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    icon = ImageTk.PhotoImage(icon_factory(32), master=root)
    win.iconphoto(False, icon)
    win._icon_ref = icon  # 防止图片被回收

    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=px(24), pady=px(20))

    header = tk.Frame(body, bg=BG)
    header.pack(fill="x")
    logo = ImageTk.PhotoImage(icon_factory(px(28)), master=root)
    win._logo_ref = logo
    tk.Label(header, image=logo, bg=BG).pack(side="left")
    tk.Label(
        header, text="欢迎使用 SweetSpot" if first_run else "设置 Gemini API Key",
        font=("Microsoft YaHei UI", 13, "bold"), fg=FG, bg=BG,
    ).pack(side="left", padx=(px(10), 0))

    intro = (
        "SweetSpot 使用 Google Gemini 翻译和润色文字，需要一个 Gemini API Key（有免费额度）。\n\n"
        "1. 点击下方按钮打开 Google AI Studio，用 Google 账号登录\n"
        "2. 点击「Create API key」，复制生成的 key\n"
        "3. 粘贴到下面的输入框，点「保存」"
    )
    tk.Label(
        body, text=intro, font=font, fg=MUTED, bg=BG, justify="left", wraplength=px(440)
    ).pack(anchor="w", pady=(px(14), px(10)))

    link = tk.Label(
        body, text="打开 Google AI Studio ↗", font=("Microsoft YaHei UI", 10, "underline"),
        fg=ACCENT, bg=BG, cursor="hand2",
    )
    link.pack(anchor="w")
    link.bind("<Button-1>", lambda e: webbrowser.open(AI_STUDIO_URL))

    tk.Label(body, text="API Key", font=("Microsoft YaHei UI", 9, "bold"), fg=FG, bg=BG).pack(
        anchor="w", pady=(px(16), px(4))
    )
    entry_wrap = tk.Frame(body, bg=BG, highlightthickness=1, highlightbackground=BORDER,
                          highlightcolor=ACCENT)
    entry_wrap.pack(fill="x")
    var = tk.StringVar(value=initial)
    entry = tk.Entry(entry_wrap, textvariable=var, font=("Consolas", 11), relief="flat",
                     bg=BG, fg=FG, insertbackground=FG)
    entry.pack(fill="x", padx=px(8), pady=px(6))

    error_label = tk.Label(body, text=error, font=("Microsoft YaHei UI", 9), fg=ERROR, bg=BG,
                           justify="left", wraplength=px(440))
    if error:
        error_label.pack(anchor="w", pady=(px(6), 0))

    tk.Label(
        body, text="Key 只保存在本机 exe 旁边的 config.json 里，不要分享给别人。",
        font=("Microsoft YaHei UI", 9), fg=MUTED, bg=BG,
    ).pack(anchor="w", pady=(px(8), 0))

    result = {"value": None}

    def save(_event=None):
        key = var.get().strip().strip("'\"").strip()
        if not key:
            error_label.configure(text="请先粘贴 API Key")
            error_label.pack(anchor="w", pady=(px(6), 0))
            return
        if not key.isascii() or " " in key:
            error_label.configure(text="Key 格式不对：只应包含英文字母、数字和符号，不能有空格或中文")
            error_label.pack(anchor="w", pady=(px(6), 0))
            return
        result["value"] = key
        win.destroy()

    def cancel(_event=None):
        win.destroy()

    buttons = tk.Frame(body, bg=BG)
    buttons.pack(fill="x", pady=(px(18), 0))
    save_btn = tk.Label(buttons, text="保存", font=("Microsoft YaHei UI", 10, "bold"), fg="white",
                        bg=ACCENT, padx=px(22), pady=px(6), cursor="hand2")
    save_btn.pack(side="right")
    save_btn.bind("<Button-1>", save)
    save_btn.bind("<Enter>", lambda e: save_btn.configure(bg=ACCENT_HOVER))
    save_btn.bind("<Leave>", lambda e: save_btn.configure(bg=ACCENT))
    cancel_btn = tk.Label(buttons, text="稍后再说" if first_run else "取消", font=font, fg=MUTED,
                          bg=BG, padx=px(14), pady=px(6), cursor="hand2")
    cancel_btn.pack(side="right", padx=(0, px(8)))
    cancel_btn.bind("<Button-1>", cancel)

    win.bind("<Return>", save)
    win.bind("<Escape>", cancel)
    win.protocol("WM_DELETE_WINDOW", cancel)

    # 居中显示并抢到焦点，方便直接 Ctrl+V
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{x}+{y}")
    win.deiconify()
    win.lift()
    win.update()
    _force_foreground(win)
    win.focus_force()
    entry.focus_set()
    entry.select_range(0, "end")

    win.grab_set()
    win.wait_window()
    return result["value"]
