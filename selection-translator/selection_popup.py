"""
划词浮动按钮：用鼠标拖选或双击选中文本后，在光标旁弹出「翻译 | Refine」小按钮。

实现要点
- 用 WH_MOUSE_LL 低级鼠标钩子（ctypes，无额外依赖）识别“拖选”和“双击”手势。
  弹出前只看鼠标手势，不试探性地发送 Ctrl+C，避免在终端等程序里产生副作用。
- 默认要求手势发生在 I 型文本光标上，以过滤拖动窗口、拖文件、拖滚动条等操作。
- 浮动窗口带 WS_EX_NOACTIVATE：点击它不会抢走原窗口焦点，原来的选区得以保留。
- 所有 Tk 操作都在同一个 UI 线程里执行，其他线程通过 call() 投递任务。
"""

import ctypes
import logging
import math
import os
import queue
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
LONG_PTR = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = LONG_PTR
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
user32.SetWindowLongPtrW.restype = LONG_PTR
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WH_MOUSE_LL = 14
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x0204, 0x0207
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
LLMHF_INJECTED = 0x01
IDC_IBEAM = 32513
VK_LBUTTON = 0x01
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x08, 0x80, 0x08000000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x01, 0x10, 0x20, 0x40
MONITOR_DEFAULTTONEAREST = 2
OFFSCREEN = -32000

COLOR_BG = "#FFFFFF"
COLOR_HOVER = "#F3F0FF"
COLOR_FG = "#3F3F46"
COLOR_BUSY_FG = "#8B5CF6"
COLOR_BORDER = "#E4E4E7"

# 浮动按钮上的两个动作：(动作名, 文字, 处理中文字)
ACTIONS = (
    ("translate", "翻译", "翻译中…"),
    ("refine", "Refine", "处理中…"),
)
COLOR_DIVIDER = "#E4E4E7"

CARD_TEXT_FG = "#18181B"
CARD_MUTED_FG = "#71717A"
CARD_ERROR_FG = "#DC2626"
CARD_BTN_HOVER = "#F4F4F5"
CARD_MAX_LINES = 14
BODY_FONT = ("Microsoft YaHei UI", 10)


def enable_dpi_awareness():
    """让坐标、窗口尺寸都按物理像素计算，避免高 DPI 屏幕上位置错位、界面模糊。需在创建任何窗口前调用"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def _cursor_is_ibeam(ibeam_handle) -> bool:
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        return False
    return bool(ci.hCursor) and ci.hCursor == ibeam_handle


def _foreground_is_own_process() -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def _work_area(x: int, y: int):
    mon = user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        r = mi.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


class SelectionPopup:
    def __init__(self, cfg: dict, on_activate, icon_factory):
        """
        cfg          : 全局配置 dict（读取 selection_popup / popup_timeout / popup_require_ibeam）
        on_activate  : 点击按钮后在后台线程中调用的函数 f(action, anchor_rect)，
                       action 为 "translate"（翻译并展示）或 "refine"（润色并替换）
        icon_factory : size -> PIL.Image，按钮上的小图标
        """
        self.cfg = cfg
        self.on_activate = on_activate
        self.icon_factory = icon_factory

        self._q = queue.Queue()
        self._ready = threading.Event()
        self._state = "hidden"  # hidden / shown / busy
        self._rect = None       # 显示中按钮的屏幕矩形 (l, t, r, b)
        self._hide_job = None
        self._show_token = 0
        self._down = None       # 左键按下时的 (x, y, 是否 I 型光标)
        self._last_up = (0, 0, 0)
        self._card_rect = None  # 翻译结果卡片的屏幕矩形
        self._card_anchor = None
        self._card_text = ""

        self._ibeam = user32.LoadCursorW(None, ctypes.c_void_p(IDC_IBEAM))
        self._dbl_time = user32.GetDoubleClickTime()
        self._dbl_cx = user32.GetSystemMetrics(36)  # SM_CXDOUBLECLK
        self._dbl_cy = user32.GetSystemMetrics(37)  # SM_CYDOUBLECLK

    # ------------------------------------------------------------------ 公共接口
    def start(self):
        threading.Thread(target=self._ui_thread, name="ui", daemon=True).start()
        if not self._ready.wait(10):
            logging.error("浮动按钮 UI 线程启动超时")
            return
        threading.Thread(target=self._hook_thread, name="mouse-hook", daemon=True).start()

    def call(self, fn):
        """在 UI 线程中执行 fn（线程安全）"""
        self._q.put(fn)

    def dismiss(self):
        """用户敲键盘等情况下收起按钮（处理中时不收起）"""
        if self._state == "shown":
            self.call(self._hide)

    def on_key(self, key_name: str):
        """全局按键：收起浮动按钮；Esc 同时关闭翻译卡片"""
        self.dismiss()
        if key_name == "esc" and self._card_rect is not None:
            self.call(self._hide_card)

    def show_card(self, anchor, title: str, text: str, kind: str = "result"):
        """
        显示翻译结果卡片（线程安全）。
        anchor : 选区附近的屏幕矩形 (l, t, r, b)，卡片显示在它下方；None 表示用当前鼠标位置
        kind   : "loading" / "result" / "error"
        """
        self.call(lambda: self._show_card(anchor, title, text, kind))

    @property
    def anchor_rect(self):
        """浮动按钮当前所在位置，作为结果卡片的定位参考"""
        return self._rect

    def ask_string(self, title: str, prompt: str, initialvalue: str = ""):
        """在 UI 线程弹出输入框，阻塞调用线程直到用户关闭对话框"""
        result = {}
        done = threading.Event()

        def run():
            from tkinter import simpledialog
            top = self._tk.Toplevel(self.root)
            top.withdraw()
            top.attributes("-topmost", True)
            try:
                result["value"] = simpledialog.askstring(
                    title, prompt, initialvalue=initialvalue, parent=top
                )
            finally:
                top.destroy()
                done.set()

        self.call(run)
        done.wait()
        return result.get("value")

    # ------------------------------------------------------------------ UI 线程
    def _ui_thread(self):
        import tkinter as tk
        from PIL import ImageTk

        self._tk = tk
        root = self.root = tk.Tk()
        root.withdraw()  # 根窗口只作为宿主和对话框的父窗口
        s = self.scale = root.winfo_fpixels("1i") / 96.0

        win = self.win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"+{OFFSCREEN}+{OFFSCREEN}")
        win.configure(bg=COLOR_BG, cursor="hand2")

        box = self._box = tk.Frame(win, bg=COLOR_BG, highlightthickness=0, bd=0)
        box.pack(fill="both", expand=True)
        font = ("Microsoft YaHei UI", 9, "bold")
        pad_y = round(6 * s)

        self._img = ImageTk.PhotoImage(self.icon_factory(round(18 * s)), master=root)
        self._icon_label = tk.Label(box, image=self._img, bg=COLOR_BG, bd=0)
        self._icon_label.pack(side="left", padx=(round(8 * s), round(2 * s)), pady=pad_y)

        # 每个动作一段；段宽取“正常文字”和“处理中文字”的较宽者，切换文字时尺寸不变
        self._segments = {}
        self._hover_action = None
        probe = tk.Label(box, font=font)
        for i, (action, text, busy_text) in enumerate(ACTIONS):
            if i:
                tk.Frame(box, bg=COLOR_DIVIDER, width=max(1, round(s)), height=round(16 * s)).pack(side="left")
            widths = []
            for t in (text, busy_text):
                probe.configure(text=t)
                probe.update_idletasks()
                widths.append(probe.winfo_reqwidth())
            seg = tk.Frame(box, bg=COLOR_BG, width=max(widths) + round(16 * s),
                           height=probe.winfo_reqheight() + 2 * pad_y)
            seg.pack(side="left")
            seg.pack_propagate(False)
            label = tk.Label(seg, text=text, font=font, fg=COLOR_FG, bg=COLOR_BG, bd=0)
            label.place(relx=0.5, rely=0.5, anchor="center")
            for w in (seg, label):
                w.bind("<ButtonRelease-1>", lambda e, a=action: self._on_click(a))
            self._segments[action] = (seg, label, text, busy_text)
        probe.destroy()
        tk.Frame(box, bg=COLOR_BG, width=round(4 * s)).pack(side="left")

        for seq in ("<Enter>", "<Leave>", "<Motion>"):
            win.bind(seq, lambda e: self._update_hover())

        win.update_idletasks()
        win.update()
        self.hwnd = user32.GetParent(win.winfo_id())
        self._apply_window_styles(self.hwnd, no_activate=True, fallback_border=self._box)
        self._size = (win.winfo_width(), win.winfo_height())

        self._build_card()

        self._ready.set()
        root.after(15, self._poll)
        root.mainloop()

    @staticmethod
    def _apply_window_styles(hwnd, no_activate: bool, fallback_border, corner: int = 3):
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
        if no_activate:
            ex |= WS_EX_NOACTIVATE
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED | 0x02,  # 0x02 = SWP_NOMOVE
        )
        # Windows 11：系统级圆角 + 细边框；旧系统上退回 Tk 自己画的边框
        rounded = False
        try:
            dwm = ctypes.WinDLL("dwmapi")
            pref = ctypes.c_int(corner)  # 2 = DWMWCP_ROUND, 3 = DWMWCP_ROUNDSMALL
            rounded = dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4) == 0
            border = ctypes.c_uint(0x00E7E4E4)  # COLORREF 0x00BBGGRR，对应 #E4E4E7
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)
        except Exception:
            pass
        if not rounded:
            fallback_border.configure(highlightthickness=1, highlightbackground=COLOR_BORDER)

    # ------------------------------------------------------------------ 翻译结果卡片
    def _build_card(self):
        tk = self._tk
        s = self.scale
        card = self.card = tk.Toplevel(self.root)
        card.overrideredirect(True)
        card.attributes("-topmost", True)
        card.geometry(f"+{OFFSCREEN}+{OFFSCREEN}")
        card.configure(bg=COLOR_BG)
        card.bind("<Escape>", lambda e: self._hide_card())

        frame = self._card_frame = tk.Frame(card, bg=COLOR_BG, bd=0, highlightthickness=0)
        frame.pack(fill="both", expand=True)
        pad = round(12 * s)

        header = tk.Frame(frame, bg=COLOR_BG)
        header.pack(fill="x", padx=pad, pady=(round(10 * s), round(4 * s)))
        self._card_img = self._tk_image(16)
        tk.Label(header, image=self._card_img, bg=COLOR_BG, bd=0).pack(side="left")
        self._card_title = tk.Label(
            header, text="", font=("Segoe UI Semibold", 9), fg=CARD_MUTED_FG, bg=COLOR_BG
        )
        self._card_title.pack(side="left", padx=(round(6 * s), 0))

        def flat_button(text, command):
            b = tk.Label(
                header, text=text, font=("Microsoft YaHei UI", 9), fg=CARD_MUTED_FG,
                bg=COLOR_BG, cursor="hand2", padx=round(6 * s), pady=round(1 * s),
            )
            b.bind("<Button-1>", lambda e: command())
            b.bind("<Enter>", lambda e: b.configure(bg=CARD_BTN_HOVER, fg=CARD_TEXT_FG))
            b.bind("<Leave>", lambda e: b.configure(bg=COLOR_BG, fg=CARD_MUTED_FG))
            b.pack(side="right")
            return b

        flat_button("✕", self._hide_card)
        self._copy_btn = flat_button("复制", self._copy_card_text)

        body = self._card_body = tk.Text(
            frame, wrap="word", font=BODY_FONT, fg=CARD_TEXT_FG, bg=COLOR_BG,
            bd=0, highlightthickness=0, width=42, height=1, cursor="xterm",
            padx=0, pady=0, spacing1=round(1 * s), spacing3=round(1 * s),
            selectbackground="#DDD6FE", selectforeground=CARD_TEXT_FG,
            insertwidth=0, takefocus=0,
        )
        body.pack(fill="both", expand=True, padx=pad, pady=(0, round(12 * s)))
        # 只读：屏蔽键盘输入，但保留选择和 Ctrl+C
        body.bind("<Key>", lambda e: None if (e.state & 0x4 and e.keysym.lower() in ("c", "a")) else "break")
        body.bind("<MouseWheel>", lambda e: "break")  # 滚轮由鼠标钩子统一处理

        card.update_idletasks()
        card.update()
        self.card_hwnd = user32.GetParent(card.winfo_id())
        self._apply_window_styles(self.card_hwnd, no_activate=False, fallback_border=frame, corner=2)

    def _tk_image(self, size: int):
        from PIL import ImageTk
        return ImageTk.PhotoImage(self.icon_factory(round(size * self.scale)), master=self.root)

    def _show_card(self, anchor, title: str, text: str, kind: str):
        # 结果卡片出现后，浮动按钮就不需要了
        self._hide(force=True)
        if anchor is None:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            anchor = (pt.x, pt.y, pt.x, pt.y + round(20 * self.scale))
        self._card_anchor = anchor
        self._card_text = text if kind == "result" else ""

        fg = {"loading": CARD_MUTED_FG, "error": CARD_ERROR_FG}.get(kind, CARD_TEXT_FG)
        self._card_title.configure(text=title)
        self._copy_btn.configure(text="复制")
        if kind == "result":
            self._copy_btn.pack(side="right")
        else:
            self._copy_btn.pack_forget()

        body = self._card_body
        body.configure(state="normal", fg=fg, height=CARD_MAX_LINES)
        body.delete("1.0", "end")
        body.insert("1.0", text)
        body.yview_moveto(0)
        self.card.update_idletasks()
        lines = body.count("1.0", "end-1c", "displaylines")
        lines = (lines[0] if isinstance(lines, tuple) else lines) or 0
        body.configure(height=max(1, min(lines + 1, CARD_MAX_LINES)))
        self.card.update_idletasks()
        self._place_card()

    def _place_card(self):
        s = self.scale
        w, h = self.card.winfo_reqwidth(), self.card.winfo_reqheight()
        al, at, ar, ab = self._card_anchor
        gap = round(6 * s)
        x, y = al, ab + gap
        left, top, right, bottom = _work_area(al, ab)
        if y + h > bottom:  # 下方放不下就放到选区上方
            y = at - gap - h
        x = min(max(left, x), right - w)
        y = min(max(top, y), bottom - h)
        user32.SetWindowPos(
            self.card_hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        )
        self._card_rect = (x, y, x + w, y + h)

    def _hide_card(self):
        if self._card_rect is None:
            return
        user32.SetWindowPos(
            self.card_hwnd, HWND_TOPMOST, OFFSCREEN, OFFSCREEN, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE
        )
        self._card_rect = None

    def _copy_card_text(self):
        if not self._card_text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._card_text)
        self._copy_btn.configure(text="已复制 ✓")
        self.root.after(1500, lambda: self._copy_btn.configure(text="复制"))

    def _scroll_card(self, delta: int):
        self._card_body.yview_scroll(-1 if delta > 0 else 1, "units")
        self._card_body.yview_scroll(-1 if delta > 0 else 1, "units")

    def _point_in_card(self, x: int, y: int) -> bool:
        r = self._card_rect
        return r is not None and r[0] <= x < r[2] and r[1] <= y < r[3]

    def _poll(self):
        try:
            while True:
                fn = self._q.get_nowait()
                try:
                    fn()
                except Exception:
                    logging.exception("UI 任务执行失败")
        except queue.Empty:
            pass
        self.root.after(15, self._poll)

    def _segment_under_pointer(self):
        # 窗口是用 SetWindowPos 直接移动的，Tk 记录的屏幕坐标不可靠；
        # 这里用真实窗口位置 self._rect + 各段在窗口内的相对偏移来判断
        r = self._rect
        if r is None:
            return None
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        if not self._point_inside(pt.x, pt.y):
            return None
        rel_x = pt.x - r[0]
        for action, (seg, _, _, _) in self._segments.items():
            if seg.winfo_x() <= rel_x < seg.winfo_x() + seg.winfo_width():
                return action
        return None

    def _update_hover(self):
        action = self._segment_under_pointer() if self._state == "shown" else None
        if action == self._hover_action:
            return
        self._hover_action = action
        for a, (seg, label, _, _) in self._segments.items():
            bg = COLOR_HOVER if a == action else COLOR_BG
            seg.configure(bg=bg)
            label.configure(bg=bg)

    def _reset_segments(self):
        self._hover_action = None
        for seg, label, text, _ in self._segments.values():
            seg.configure(bg=COLOR_BG)
            label.configure(bg=COLOR_BG, text=text, fg=COLOR_FG)

    def _cursor_inside(self) -> bool:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return self._point_inside(pt.x, pt.y)

    def _point_inside(self, x: int, y: int) -> bool:
        r = self._rect
        return r is not None and r[0] <= x < r[2] and r[1] <= y < r[3]

    def _cancel_hide_job(self):
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None

    def _show_at(self, x: int, y: int):
        if self._state == "busy":
            return
        s = self.scale
        w, h = self._size
        x, y = x + round(8 * s), y + round(20 * s)  # 放在光标右下方，避开光标本身
        left, top, right, bottom = _work_area(x, y)
        if x + w > right:
            x = right - w
        if y + h > bottom:
            y = y - h - round(32 * s)  # 底部放不下就放到光标上方
        x, y = max(left, x), max(top, y)

        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        )
        self._rect = (x, y, x + w, y + h)
        self._state = "shown"
        self._reset_segments()
        self._cancel_hide_job()
        self._hide_job = self.root.after(int(float(self.cfg.get("popup_timeout", 4)) * 1000), self._auto_hide)

    def _auto_hide(self):
        self._hide_job = None
        if self._state != "shown":
            return
        if self._cursor_inside():  # 鼠标正停在按钮上，稍后再检查
            self._hide_job = self.root.after(800, self._auto_hide)
            return
        self._hide()

    def _hide(self, force: bool = False):
        if self._state == "busy" and not force:
            return
        self._cancel_hide_job()
        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, OFFSCREEN, OFFSCREEN, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE
        )
        self._rect = None
        self._state = "hidden"

    def _delayed_show(self, token: int, x: int, y: int):
        # 期间用户又按下了鼠标，或左键仍按着，则放弃
        if token != self._show_token or user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
            return
        self._show_at(x, y)

    def _on_click(self, action: str):
        if self._state != "shown" or self._segment_under_pointer() != action:
            return
        self._state = "busy"
        self._cancel_hide_job()
        self._reset_segments()
        _, label, _, busy_text = self._segments[action]
        label.configure(text=busy_text, fg=COLOR_BUSY_FG)

        anchor = self._rect

        def worker():
            try:
                self.on_activate(action, anchor)
            except Exception:
                logging.exception("浮动按钮触发失败")
            finally:
                self.call(lambda: self._hide(force=True))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ 鼠标钩子线程
    def _hook_thread(self):
        self._proc = HOOKPROC(self._hook_proc)  # 必须保存引用，防止被垃圾回收
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            logging.error("安装鼠标钩子失败：%s", ctypes.get_last_error())
            return
        logging.info("划词浮动按钮已就绪")
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _hook_proc(self, n_code, w_param, l_param):
        # 钩子回调必须尽快返回，这里只做轻量判断，实际 UI 操作投递给 UI 线程
        if n_code == 0:
            try:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not info.flags & LLMHF_INJECTED:  # 忽略程序模拟的鼠标事件
                    self._on_mouse(w_param, info.pt.x, info.pt.y, info.time, info.mouseData)
            except Exception:
                logging.exception("鼠标钩子处理异常")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_mouse(self, msg: int, x: int, y: int, t: int, data: int = 0):
        if msg == WM_LBUTTONDOWN:
            self._show_token += 1  # 取消尚未显示的按钮
            if self._point_inside(x, y) or self._point_in_card(x, y):
                self._down = None
                return
            if self._state == "shown":
                self.call(self._hide)
            if self._card_rect is not None:  # 点击卡片以外的地方即关闭卡片
                self.call(self._hide_card)
            self._down = (x, y, _cursor_is_ibeam(self._ibeam))

        elif msg == WM_LBUTTONUP:
            down, self._down = self._down, None
            lx, ly, lt = self._last_up
            self._last_up = (x, y, t)
            if down is None or not self.cfg.get("selection_popup", True):
                return

            s = getattr(self, "scale", 1.0)
            dragged = math.hypot(x - down[0], y - down[1]) >= 6 * s
            double = (
                ((t - lt) & 0xFFFFFFFF) <= self._dbl_time
                and abs(x - lx) <= self._dbl_cx
                and abs(y - ly) <= self._dbl_cy
                and math.hypot(x - down[0], y - down[1]) < 6 * s
            )
            if not (dragged or double):
                return
            if self.cfg.get("popup_require_ibeam", True) and not (down[2] or _cursor_is_ibeam(self._ibeam)):
                return
            if _foreground_is_own_process():
                return

            token = self._show_token
            # 稍等片刻再显示，让目标程序先完成选区更新
            self.call(lambda: self.root.after(120, lambda: self._delayed_show(token, x, y)))

        elif msg == WM_MOUSEWHEEL and self._point_in_card(x, y):
            delta = ctypes.c_short((data >> 16) & 0xFFFF).value
            self.call(lambda: self._scroll_card(delta))

        elif msg in (WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            if self._state == "shown" and not self._point_inside(x, y):
                self.call(self._hide)
            if msg in (WM_RBUTTONDOWN, WM_MBUTTONDOWN) and self._card_rect is not None                     and not self._point_in_card(x, y):
                self.call(self._hide_card)
