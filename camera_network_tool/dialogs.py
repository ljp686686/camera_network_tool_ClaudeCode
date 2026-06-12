"""
对话框模块
IP 设置、批量 IP 设置、重命名的模态对话框
"""

import tkinter as tk
from tkinter import ttk, messagebox
import json
import re

from .net_mgr import NetMgr, validate_ip, validate_prefix, ps_escape_sq, center_dialog

class SetIPDialog:
    """设置单个适配器 IPv4 地址的模态对话框"""
    def __init__(self, parent, adapter_name, current_ip="", current_prefix=""):
        self.result = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("设置 IPv4 地址")
        self.dialog.configure(bg="#0d1117")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.dialog.bind("<Return>", lambda e: self._on_ok())
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())

        f = ttk.Frame(self.dialog, padding=12)
        f.pack(fill=tk.BOTH, expand=True)

        # 当前状态
        cur_text = f"当前: {current_ip}/{current_prefix}" if current_ip else "当前: 未配置"
        ttk.Label(f, text=cur_text, foreground="#3fb950" if current_ip else "#8b949e").pack(anchor=tk.W)
        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # IP 地址
        ipf = ttk.Frame(f)
        ipf.pack(fill=tk.X, pady=3)
        ttk.Label(ipf, text="IP 地址:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        self.entry_ip = tk.Entry(ipf, bg="#161b22", fg="#c9d1d9",
                                 insertbackground="#c9d1d9", relief="flat", bd=2,
                                 highlightthickness=1, highlightbackground="#30363d")
        self.entry_ip.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        if current_ip:
            self.entry_ip.insert(0, current_ip)

        # 子网掩码
        pref = ttk.Frame(f)
        pref.pack(fill=tk.X, pady=3)
        ttk.Label(pref, text="子网掩码:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        self.combo_prefix = ttk.Combobox(pref, values=["24", "16", "8", "32"], width=8)
        self.combo_prefix.current(0)
        if current_prefix:
            self.combo_prefix.set(str(current_prefix))
        self.combo_prefix.pack(side=tk.LEFT, padx=(4, 4))
        ttk.Label(pref, text="(前缀长度 /24 = 255.255.255.0)", foreground="#8b949e").pack(side=tk.LEFT)

        # 默认网关
        gwf = ttk.Frame(f)
        gwf.pack(fill=tk.X, pady=3)
        ttk.Label(gwf, text="默认网关:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        self.entry_gw = tk.Entry(gwf, bg="#161b22", fg="#c9d1d9",
                                 insertbackground="#c9d1d9", relief="flat", bd=2,
                                 highlightthickness=1, highlightbackground="#30363d")
        self.entry_gw.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        ttk.Label(gwf, text=" (可选)", foreground="#8b949e").pack(side=tk.LEFT)

        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Label(f, text="注意：设置 IP 将清除此接口的 DHCP 配置。",
                  foreground="#d29922", wraplength=380).pack(anchor=tk.W)

        # 按钮
        bf = ttk.Frame(f)
        bf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bf, text="确定", command=self._on_ok).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(bf, text="取消", command=self._on_cancel).pack(side=tk.RIGHT)

        center_dialog(self.dialog, parent)

    def _on_ok(self):
        ip = self.entry_ip.get().strip()
        prefix = self.combo_prefix.get().strip()
        gw = self.entry_gw.get().strip()

        ok, msg = validate_ip(ip)
        if not ok:
            messagebox.showwarning("输入错误", msg, parent=self.dialog)
            self.entry_ip.focus_set()
            return
        ok, msg = validate_prefix(prefix)
        if not ok:
            messagebox.showwarning("输入错误", msg, parent=self.dialog)
            self.combo_prefix.focus_set()
            return
        if gw:
            ok, msg = validate_ip(gw)
            if not ok:
                messagebox.showwarning("输入错误", f"网关 {msg}", parent=self.dialog)
                self.entry_gw.focus_set()
                return
        self.result = (ip, int(prefix), gw if gw else None)
        self.dialog.destroy()

    def _on_cancel(self):
        self.result = None
        self.dialog.destroy()


class MultiSetIPDialog:
    """批量设置多个适配器 IPv4 地址的模态对话框，目标 IP 默认按 10.0.0.x 递增"""
    def __init__(self, parent, adapters_info):
        """
        adapters_info: list of (name, current_ip, current_prefix)
        目标 IP 默认按 10.0.0.1, 10.0.0.2, ... 递增
        """
        self.result = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("批量设置 IPv4 地址")
        self.dialog.configure(bg="#0d1117")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())

        self.adapters_info = adapters_info

        f = ttk.Frame(self.dialog, padding=12)
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(f, text="批量设置 IPv4 地址", font=("", 12, "bold")).pack(anchor=tk.W)
        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # ── 适配器列表（滚动区域） ──
        outer = ttk.Frame(f)
        outer.pack(fill=tk.X)
        canvas = tk.Canvas(outer, bg="#0d1117", highlightthickness=0, height=min(200, 40 * len(adapters_info)))
        vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # 表头
        hdr = ttk.Frame(inner)
        hdr.pack(fill=tk.X)
        ttk.Label(hdr, text="适配器名称", font=("", 9, "bold"), width=14, anchor=tk.W).grid(row=0, column=0, padx=3, pady=2)
        ttk.Label(hdr, text="当前 IP", font=("", 9, "bold"), width=16, anchor=tk.W).grid(row=0, column=1, padx=3, pady=2)
        ttk.Label(hdr, text="目标 IP", font=("", 9, "bold"), anchor=tk.W).grid(row=0, column=2, padx=3, pady=2, sticky=tk.W)

        self.entries = []
        for i, (name, cur_ip, cur_prefix) in enumerate(adapters_info):
            row = i + 1
            cur_text = f"{cur_ip}/{cur_prefix}" if cur_ip else "未配置"
            ttk.Label(hdr, text=name, width=14, anchor=tk.W).grid(row=row, column=0, padx=3, pady=2)
            ttk.Label(hdr, text=cur_text, foreground="#3fb950" if cur_ip else "#8b949e",
                      width=16, anchor=tk.W).grid(row=row, column=1, padx=3, pady=2)
            entry = tk.Entry(hdr, bg="#161b22", fg="#c9d1d9",
                             insertbackground="#c9d1d9", relief="flat", bd=2,
                             highlightthickness=1, highlightbackground="#30363d")
            entry.insert(0, f"10.0.0.{i + 1}")
            entry.grid(row=row, column=2, padx=3, pady=2, sticky=tk.EW)
            self.entries.append(entry)

        hdr.grid_columnconfigure(2, weight=1)

        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # ── 公共设置 ──
        sf = ttk.Frame(f)
        sf.pack(fill=tk.X, pady=2)
        ttk.Label(sf, text="子网掩码:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        self.combo_prefix = ttk.Combobox(sf, values=["24", "16", "8", "32"], width=8)
        self.combo_prefix.current(0)
        self.combo_prefix.pack(side=tk.LEFT, padx=(4, 4))
        ttk.Label(sf, text="(前缀长度 /24 = 255.255.255.0)", foreground="#8b949e").pack(side=tk.LEFT)

        gf = ttk.Frame(f)
        gf.pack(fill=tk.X, pady=2)
        ttk.Label(gf, text="默认网关:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        self.entry_gw = tk.Entry(gf, bg="#161b22", fg="#c9d1d9",
                                 insertbackground="#c9d1d9", relief="flat", bd=2,
                                 highlightthickness=1, highlightbackground="#30363d")
        self.entry_gw.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        ttk.Label(gf, text=" (可选, 所有适配器共用)", foreground="#8b949e").pack(side=tk.LEFT)

        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Label(f, text="注意：设置 IP 将清除这些接口的 DHCP 配置。",
                  foreground="#d29922", wraplength=450).pack(anchor=tk.W)

        # ── 按钮 ──
        bf = ttk.Frame(f)
        bf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bf, text="确定", command=self._on_ok).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(bf, text="取消", command=self._on_cancel).pack(side=tk.RIGHT)

        center_dialog(self.dialog, parent)

    def _on_ok(self):
        prefix = self.combo_prefix.get().strip()
        ok, msg = validate_prefix(prefix)
        if not ok:
            messagebox.showwarning("输入错误", msg, parent=self.dialog)
            self.combo_prefix.focus_set()
            return

        gw = self.entry_gw.get().strip()
        if gw:
            ok, msg = validate_ip(gw)
            if not ok:
                messagebox.showwarning("输入错误", f"网关 {msg}", parent=self.dialog)
                self.entry_gw.focus_set()
                return

        results = []
        for i, entry in enumerate(self.entries):
            ip = entry.get().strip()
            ok, msg = validate_ip(ip)
            if not ok:
                messagebox.showwarning("输入错误",
                    f"第 {i+1} 行 ({self.adapters_info[i][0]}): {msg}",
                    parent=self.dialog)
                entry.focus_set()
                return
            results.append((self.adapters_info[i][0], ip, int(prefix), gw if gw else None))

        self.result = results
        self.dialog.destroy()

    def _on_cancel(self):
        self.result = None
        self.dialog.destroy()


class RenameDialog:
    """批量重命名网络适配器的模态对话框"""
    def __init__(self, parent, adapters_info):
        self.result = None
        self.adapters_info = adapters_info
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("批量重命名适配器")
        self.dialog.configure(bg="#0d1117")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())

        f = ttk.Frame(self.dialog, padding=12)
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(f, text="批量重命名适配器", font=("", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(f, text="修改名称后点击确定应用（需要管理员权限）",
                  foreground="#8b949e").pack(anchor=tk.W, pady=(2, 0))
        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # 适配器列表
        outer = ttk.Frame(f)
        outer.pack(fill=tk.X)
        canvas = tk.Canvas(outer, bg="#0d1117", highlightthickness=0, height=min(200, 40 * len(adapters_info)))
        vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # 表头
        hdr = ttk.Frame(inner)
        hdr.pack(fill=tk.X)
        ttk.Label(hdr, text="当前名称", font=("", 9, "bold"), width=18, anchor=tk.W).grid(row=0, column=0, padx=3, pady=2)
        ttk.Label(hdr, text="新名称", font=("", 9, "bold"), anchor=tk.W).grid(row=0, column=1, padx=3, pady=2, sticky=tk.W)

        self.entries = []
        for i, (cur_name, _) in enumerate(adapters_info):
            row = i + 1
            ttk.Label(hdr, text=cur_name, width=18, anchor=tk.W).grid(row=row, column=0, padx=3, pady=2)
            entry = tk.Entry(hdr, bg="#161b22", fg="#c9d1d9",
                             insertbackground="#c9d1d9", relief="flat", bd=2,
                             highlightthickness=1, highlightbackground="#30363d")
            entry.insert(0, cur_name)
            entry.grid(row=row, column=1, padx=3, pady=2, sticky=tk.EW)
            self.entries.append(entry)

        hdr.grid_columnconfigure(1, weight=1)
        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # 按钮
        bf = ttk.Frame(f)
        bf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bf, text="确定", command=self._on_ok).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(bf, text="取消", command=self._on_cancel).pack(side=tk.RIGHT)
        center_dialog(self.dialog, parent)

    def _on_ok(self):
        results = []
        seen_names = set()
        for i, entry in enumerate(self.entries):
            new_name = entry.get().strip()
            if not new_name:
                messagebox.showwarning("输入错误",
                    f"第 {i+1} 行（{self.adapters_info[i][0]}）：名称不能为空",
                    parent=self.dialog)
                entry.focus_set()
                return
            if new_name in seen_names:
                messagebox.showwarning("输入错误",
                    f"名称「{new_name}」重复，请确保每个适配器名称唯一",
                    parent=self.dialog)
                entry.focus_set()
                return
            seen_names.add(new_name)
            results.append((self.adapters_info[i][0], new_name))
        self.result = results
        self.dialog.destroy()

    def _on_cancel(self):
        self.result = None
        self.dialog.destroy()


# ──────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# 界面层：Tkinter GUI 主窗口
# ──────────────────────────────────────────────
