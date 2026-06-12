"""
主界面模块
相机网络配置工具的 Tkinter GUI 主窗口
"""

import sys
import os
import tkinter as tk
from tkinter import ttk, messagebox
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import re
import ctypes
import socket
import struct
import ipaddress

from .net_mgr import APP_NAME, APP_VERSION, NetMgr, is_admin, elevate
from .net_mgr import run_ps, validate_ip, validate_prefix, center_dialog
from .net_mgr import SETTING_DEFS, INTERFACE_TYPE_ETHERNET
from .camera_scanner import GigEVisionScanner
from .dialogs import SetIPDialog, MultiSetIPDialog, RenameDialog

class App:
    def __init__(self, root):
        self.root = root
        self._lock = Lock()
        self.style = ttk.Style()
        # ── 配色常量（深色科技风） ──
        self.COL = {
            "bg":       "#0d1117",  # 根背景
            "surface":  "#161b22",  # 卡片/输入区
            "border":   "#30363d",  # 分割线
            "accent":   "#58a6ff",  # 主强调色
            "accent2":  "#1f6feb",  # hover 态
            "text":     "#c9d1d9",  # 主文字
            "dim":      "#8b949e",  # 次要文字
            "success":  "#3fb950",  # 成功
            "error":    "#f85149",  # 失败
            "warn":     "#d29922",  # 警告
            "header":   "#f0f6fc",  # 标题文字
        }
        self._configure_styles()
        self.tree = None
        self.check_vars = {}
        self.adapters = []
        self.show_wifi = tk.BooleanVar(value=False)
        self._build_ui()
        self._check_adm()
        self.refresh()

    def _configure_styles(self):
        """统一配置 ttk 样式 — 深色科技风"""
        c = self.COL
        style = self.style
        style.theme_use("clam")

        style.configure(".", background=c["bg"], foreground=c["text"],
                        font=("Microsoft YaHei UI", 9))
        style.configure("TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["surface"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"),
                        foreground=c["header"], background=c["bg"])
        style.configure("Version.TLabel", font=("Consolas", 8), foreground=c["accent"],
                        background=c["surface"])
        style.configure("Dim.TLabel", foreground=c["dim"], background=c["bg"])
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 8, "bold"))
        style.configure("TLabelframe", background=c["bg"])
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["dim"],
                        font=("Microsoft YaHei UI", 8, "bold"))
        style.configure("TButton", background=c["surface"], foreground=c["text"],
                        borderwidth=0, relief="flat", padding=(14, 7))
        style.map("TButton", background=[("active", c["accent2"]),
                    ("!disabled", c["surface"]), ("disabled", c["surface"])],
                  foreground=[("active", "#fff"), ("disabled", c["dim"])])
        style.configure("Accent.TButton", background=c["accent"], foreground="#fff",
                        font=("Microsoft YaHei UI", 9, "bold"), borderwidth=0, padding=(16, 8))
        style.map("Accent.TButton", background=[("active", c["accent2"]),
                  ("!disabled", c["accent"]), ("disabled", c["surface"])])
        style.configure("Small.TButton", padding=(8, 4), font=("Microsoft YaHei UI", 8))
        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"])
        style.map("TCheckbutton", background=[("active", c["bg"])])
        style.configure("Treeview", background=c["surface"], foreground=c["text"],
                        fieldbackground=c["surface"], borderwidth=0)
        style.configure("Treeview.Heading", background=c["bg"], foreground=c["dim"],
                        font=("Microsoft YaHei UI", 8, "bold"), borderwidth=0, relief="flat")
        style.map("Treeview", background=[("selected", c["accent2"])],
                  foreground=[("selected", "#fff")])
        style.configure("TProgressbar", background=c["accent"], troughcolor=c["surface"],
                        borderwidth=0, thickness=4)
        style.configure("TScrollbar", background=c["surface"], troughcolor=c["bg"],
                        borderwidth=0, arrowsize=14)
        style.map("TScrollbar", background=[("active", c["accent2"])])

    def _check_adm(self):
        if not is_admin():
            self.root.after(500, lambda: (
                messagebox.askyesno("管理员权限",
                    "部分设置需要管理员权限才能读取和修改。\n是否以管理员身份重新启动？")
                and elevate()
            ))

    # ── UI 构建 ──

    def _build_ui(self):
        c = self.COL
        P = {"padx": 12, "pady": 6}

        # ═══════════ Header ═══════════
        hf = ttk.Frame(self.root, padding=(16, 10, 16, 4))
        hf.pack(fill=tk.X)
        ttk.Label(hf, text=APP_NAME, style="Title.TLabel").pack(side=tk.LEFT)

        # 版本徽章
        badge = tk.Frame(hf, bg=c["surface"], highlightthickness=0)
        badge.pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(badge, text=APP_VERSION, fg=c["accent"], bg=c["surface"],
                 font=("Consolas", 8)).pack(padx=6, pady=2)

        self.adm_lbl = ttk.Label(hf, style="Status.TLabel")
        self.adm_lbl.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Checkbutton(hf, text="无线", variable=self.show_wifi,
                        command=self.refresh).pack(side=tk.RIGHT, padx=4)
        self._upd_adm_lbl()

        # 分割线
        sep = ttk.Frame(self.root, height=1)
        sep.pack(fill=tk.X, padx=16)
        tk.Frame(sep, bg=c["border"], height=1).pack(fill=tk.X)

        # ═══════════ PanedWindow（可拖拽分割）═══════════
        pw = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 2))

        # ── Top: Adapter List + Button Bar ──
        top_frame = ttk.Frame(pw)

        # 选择按钮（列表上方靠左）
        sf = ttk.Frame(top_frame)
        sf.pack(fill=tk.X, padx=(0, 0))
        ttk.Button(sf, text="↻ 刷新", style="Small.TButton",
                   command=self.refresh).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(sf, text="全选", style="Small.TButton",
                   command=self.sel_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(sf, text="全不选", style="Small.TButton",
                   command=self.sel_none).pack(side=tk.LEFT, padx=2)

        lf = ttk.LabelFrame(top_frame, text=" 适配器列表 ", padding=2)
        lf.pack(fill=tk.BOTH, expand=True)

        cols = ("sel", "name", "desc", "speed", "status", "mac", "type")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=6)
        heads = {"sel": "选", "name": "名称", "desc": "描述",
                 "speed": "速率", "status": "状态", "mac": "MAC 地址", "type": "类型"}
        widths = {"sel": 36, "name": 135, "desc": 200, "speed": 70,
                  "status": 62, "mac": 125, "type": 48}
        for col in cols:
            data_anchor = "center" if col in ("sel", "speed", "status", "type") else "w"
            self.tree.heading(col, text=heads[col], anchor=data_anchor)
            self.tree.column(col, width=widths[col], minwidth=widths[col], anchor=data_anchor)
        self.tree.column("desc", stretch=True)  # 描述列自动填充剩余宽度
        vsb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        lf.grid_columnconfigure(0, weight=1)
        lf.grid_rowconfigure(0, weight=1)
        self.tree.bind("<ButtonRelease-1>", self._on_click)

        # Button Bar（在适配器列表下方）
        bf = ttk.Frame(top_frame, padding=(12, 6))
        bf.pack(fill=tk.X)

        self.btn_chk = ttk.Button(bf, text="🔍 检查", command=self.check)
        self.btn_chk.pack(side=tk.LEFT, padx=2)
        self.btn_apply = ttk.Button(bf, text="⚡ 一键设置", command=self.apply,
                                    style="Accent.TButton")
        self.btn_apply.pack(side=tk.LEFT, padx=2)
        self.btn_setip = ttk.Button(bf, text="🌐 设置 IP", command=self.set_ip)
        self.btn_setip.pack(side=tk.LEFT, padx=2)
        self.btn_rename = ttk.Button(bf, text="✏ 重命名", command=self.rename)
        self.btn_rename.pack(side=tk.LEFT, padx=2)
        self.btn_pm = ttk.Button(bf, text="🔌 电源管理", command=self._on_toggle_pm)
        self.btn_pm.pack(side=tk.LEFT, padx=2)
        self.btn_scan = ttk.Button(bf, text="📡 扫描相机", command=self.scan_cameras)
        self.btn_scan.pack(side=tk.LEFT, padx=2)
        
        pw.add(top_frame, weight=0)

        # ── Bottom: Result + Log side by side ──
        bottom_pw = ttk.PanedWindow(pw, orient=tk.HORIZONTAL)

        # ── Result Area (left, weight=2) ──
        mid_frame = ttk.Frame(bottom_pw)
        self.prog = ttk.Progressbar(mid_frame, mode="indeterminate")
        rf = ttk.LabelFrame(mid_frame, text=" 结果 ", padding=2)
        rf.pack(fill=tk.BOTH, expand=True)

        self.tres = tk.Text(rf, wrap=tk.WORD, font=("Consolas", 10),
                            bg=c["surface"], fg=c["text"], insertbackground=c["text"],
                            relief="flat", borderwidth=0, highlightthickness=0,
                            state=tk.DISABLED, padx=8, pady=4)
        rv = ttk.Scrollbar(rf, orient=tk.VERTICAL, command=self.tres.yview)
        self.tres.configure(yscrollcommand=rv.set)
        self.tres.grid(row=0, column=0, sticky="nsew")
        rv.grid(row=0, column=1, sticky="ns")
        rf.grid_columnconfigure(0, weight=1)
        rf.grid_rowconfigure(0, weight=1)

        tag_specs = {
            "h":        (c["accent"], ("Consolas", 11, "bold")),
            "sec":      ("#c586c0",   ("Consolas", 10, "bold")),
            "ok":       (c["success"], None),
            "fail":     (c["error"],   None),
            "info":     (c["text"],    None),
            "warn":     (c["warn"],    None),
            "sub_ok":   (c["success"], None),
            "sub_fail": (c["error"],   None),
        }
        for tag, (fg, font) in tag_specs.items():
            kwargs = {"foreground": fg}
            if font:
                kwargs["font"] = font
            self.tres.tag_configure(tag, **kwargs)

        # ── Log Area (right, weight=1) ──
        bot_frame = ttk.Frame(bottom_pw)
        lgf = ttk.LabelFrame(bot_frame, text=" 日志 ", padding=2)
        lgf.pack(fill=tk.BOTH, expand=True)

        self.tlog = tk.Text(lgf, wrap=tk.WORD, font=("Consolas", 9),
                            bg=c["surface"], fg=c["dim"], insertbackground=c["text"],
                            relief="flat", borderwidth=0, highlightthickness=0,
                            state=tk.DISABLED, padx=8, pady=2)
        self.tlog.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lv = ttk.Scrollbar(lgf, orient=tk.VERTICAL, command=self.tlog.yview)
        self.tlog.configure(yscrollcommand=lv.set)
        lv.pack(side=tk.RIGHT, fill=tk.Y)
        self.tlog.tag_configure("ts", foreground=c["dim"])
        self.tlog.tag_configure("info", foreground=c["dim"])
        self.tlog.tag_configure("err", foreground=c["error"])
        self.tlog.tag_configure("ok", foreground=c["success"])

        bottom_pw.add(mid_frame, weight=1)
        bottom_pw.add(bot_frame, weight=1)
        pw.add(bottom_pw, weight=1)

    def _upd_adm_lbl(self):
        adm = is_admin()
        self.adm_lbl.config(
            text="● 管理员" if adm else "○ 普通模式",
            foreground=self.COL["success"] if adm else self.COL["error"],
        )

    # ── 适配器列表交互 ──

    def _on_click(self, ev):
        region = self.tree.identify_region(ev.x, ev.y)
        if region == "heading":
            return
        item = self.tree.identify_row(ev.y)
        if not item:
            return
        vals = self.tree.item(item, "values")
        if not vals or len(vals) < 2:
            return
        name = vals[1]
        if name in self.check_vars:
            self.check_vars[name].set(not self.check_vars[name].get())
            self._sync_cb(item, name)

    def _sync_cb(self, item=None, name=None):
        if item and name and name in self.check_vars:
            self.tree.set(item, "sel", "☑" if self.check_vars[name].get() else "☐")

    def _sync_all_cb(self):
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            if vals and len(vals) > 1:
                n = vals[1]
                if n in self.check_vars:
                    self.tree.set(item, "sel", "☑" if self.check_vars[n].get() else "☐")

    def refresh(self):
        self._set_btns(False)
        self._log("正在扫描网络适配器...")
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)
        self.tres.config(state=tk.DISABLED)

        def task():
            all_a = NetMgr.enum_adapters()
            up_names = {a["Name"] for a in all_a
                        if a.get("Status") == "Up" or a.get("MediaConnectedState") is True}
            self.root.after(0, self._show_adapters, all_a, up_names)

        Thread(target=task, daemon=True).start()

    def _show_adapters(self, adapters, up_names):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.check_vars.clear()
        self.adapters = adapters

        if not adapters:
            self._log("未检测到网络适配器")
            self.tres.insert(tk.END, "未检测到网络适配器。\n", "info")
            self._set_btns(True)
            return

        for a in adapters:
            name = a.get("Name", "?")
            desc = a.get("InterfaceDescription", "")
            speed = a.get("LinkSpeed", "") or "未知"
            mac = a.get("MacAddress", "") or ""
            itype = a.get("InterfaceType", 0)
            connected = (name in up_names or a.get("Status") == "Up"
                         or a.get("MediaConnectedState") is True)
            status_txt = "已连接" if connected else "未连接"

            # 接口类型: 6=以太网, 71=无线
            is_wifi = (itype == 71)
            type_txt = "无线" if is_wifi else "以太网" if itype == INTERFACE_TYPE_ETHERNET else "其他"

            # 默认过滤掉无线网卡（相机通常使用有线以太网）
            if is_wifi and not self.show_wifi.get():
                continue

            self.tree.insert("", tk.END, values=(
                "☐", name, desc, speed, status_txt, mac, type_txt
            ), tags=("up",) if connected else ("down",))
            self.check_vars[name] = tk.BooleanVar(value=connected)

        self.tree.tag_configure("up", foreground=self.COL["text"])
        self.tree.tag_configure("down", foreground=self.COL["dim"])
        self._sync_all_cb()

        n_up = sum(1 for n in up_names if n in self.check_vars)
        total = len(self.check_vars)
        wifi_hidden = sum(1 for a in adapters if a.get("InterfaceType") == 71)
        wifi_msg = f" (已过滤 {wifi_hidden} 个无线)" if wifi_hidden and not self.show_wifi.get() else ""
        self._log(f"扫描完成：显示 {total} 个适配器，{n_up} 个已连接{wifi_msg}")
        self._set_btns(True)

    def _selected(self):
        sel = []
        for item in self.tree.get_children():
            v = self.tree.item(item, "values")
            if v and len(v) > 1:
                n = v[1]
                if n in self.check_vars and self.check_vars[n].get():
                    sel.append(n)
        return sel

    def sel_all(self):
        for v in self.check_vars.values():
            v.set(True)
        self._sync_all_cb()

    def sel_none(self):
        for v in self.check_vars.values():
            v.set(False)
        self._sync_all_cb()

    def _set_btns(self, en):
        s = tk.NORMAL if en else tk.DISABLED
        self.btn_chk.config(state=s)
        self.btn_apply.config(state=s)
        self.btn_setip.config(state=s)
        self.btn_rename.config(state=s)
        self.btn_scan.config(state=s)

    def _busy(self, text):
        """进入繁忙状态：禁用按钮，显示进度条，清空结果区并写入提示"""
        self._set_btns(False)
        self.prog.pack(fill=tk.X, padx=10, pady=2)
        self.prog.start()
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)
        self.tres.insert(tk.END, text + "\n\n", "info")
        self.tres.config(state=tk.DISABLED)

    def _idle(self):
        """退出繁忙状态：停止进度条，启用按钮，锁定结果区"""
        self.prog.stop()
        self.prog.pack_forget()
        self.tres.config(state=tk.DISABLED)
        self._set_btns(True)

    def _wrt(self, text, tag="info"):
        """快捷写入结果区（带安全状态切换）"""
        self.tres.config(state=tk.NORMAL)
        self.tres.insert(tk.END, text, tag)
        self.tres.config(state=tk.DISABLED)

    def _log(self, msg):
        """在主线程中写入日志（通过 after 调度 _log_do）"""
        ts = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, self._log_do, ts, msg)

    # ── 检查 ──

    def check(self):
        sel = self._selected()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个适配器")
            return
        self._busy("正在检查...")

        def task():
            results = {}
            with ThreadPoolExecutor(max_workers=len(sel)) as pool:
                future_map = {pool.submit(NetMgr.check_adapter, n): n for n in sel}
                for future in as_completed(future_map):
                    name = future_map[future]
                    try:
                        items = future.result()
                    except Exception as e:
                        items = [("错误", "exception", name, str(e), "N/A", False)]
                    self._log(f"{name} 检查完成")
                    with self._lock:
                        results[name] = items
            self.root.after(0, self._show_check, results, sel)

        Thread(target=task, daemon=True).start()

    def _show_check(self, results, selected):
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)

        all_ok = True
        for name in selected:
            items = results.get(name, [])
            if not items:
                continue
            self.tres.insert(tk.END, f"📌 {name}\n", "h")
            self.tres.insert(tk.END, f"{'═' * 80}\n", "info")

            # 按分类分组显示
            last_cat = None
            for cat, sid, disp, cur, tgt, ok in items:
                if cat != last_cat and not sid.startswith("diag_"):
                    last_cat = cat
                    self.tres.insert(tk.END, f"\n  [{cat}]\n", "sec")

                if sid.startswith("diag_"):
                    self.tres.insert(tk.END, f"     {cur}\n", "info")
                elif ok:
                    self.tres.insert(tk.END, f"  ✅ {disp}\n", "sub_ok")
                    self.tres.insert(tk.END, f"     当前值: {cur}  |  目标值: {tgt}  →  正确\n", "info")
                else:
                    self.tres.insert(tk.END, f"  ❌ {disp}\n", "sub_fail")
                    self.tres.insert(tk.END, f"     当前值: {cur}  |  目标值: {tgt}  →  不正确\n", "warn")
                    all_ok = False

            total = len(items)
            ok_count = sum(1 for _, _, _, _, _, ok in items if ok)
            self.tres.insert(tk.END, f"\n  📊 进度: {ok_count}/{total} 项达标\n", "info")
            self.tres.insert(tk.END, f"{'═' * 80}\n\n", "info")

        if all_ok:
            self.tres.insert(tk.END, "\n✅ 所有设置均符合相机网络要求！\n", "ok")
        else:
            self.tres.insert(tk.END, "\n⚠ 部分设置未达标，请点击「一键设置」修复。\n", "warn")

        self._idle()

    # ── 应用 ──

    def apply(self):
        sel = self._selected()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个适配器")
            return

        if not is_admin():
            if messagebox.askyesno("管理员权限", "修改设置需要管理员权限。\n是否以管理员身份重新启动？"):
                elevate()
            else:
                messagebox.showerror("权限不足", "请以管理员身份运行")
                return

        if not messagebox.askyesno(
            "确认设置",
            "将配置以下适配器：\n" + "\n".join(f"  • {n}" for n in sel)
            + "\n\n设置将在应用后生效，是否继续？"
        ):
            return

        self._busy("正在应用设置...")

        def task():
            results = {}  # {name: [settings_log, restart_result]}
            # 阶段 1：并行写入所有适配器设置（不重启，各自独立无竞态）
            with ThreadPoolExecutor(max_workers=len(sel)) as pool:
                future_map = {
                    pool.submit(NetMgr.apply_settings, n): n
                    for n in sel
                }
                for future in as_completed(future_map):
                    name = future_map[future]
                    try:
                        settings_log = future.result()
                    except Exception as e:
                        settings_log = [(name, False, str(e))]
                    self._log(f"{name} 设置写入完成")
                    with self._lock:
                        results[name] = [settings_log, None]
            # 阶段 2：串行重启适配器（避免并行重启导致硬件冲突）
            for n in sel:
                self._log(f"正在重启 {n} 使设置生效...")
                rst_ok, rst_msg = NetMgr.restart_adapter(n)
                results[n][1] = ("适配器重启", rst_ok, rst_msg)
            self.root.after(0, self._show_apply, results, sel)

        Thread(target=task, daemon=True).start()

    def _show_apply(self, results, selected):
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)

        all_ok = True
        for name in selected:
            settings_log, restart_entry = results.get(name, ([], None))
            if not settings_log and not restart_entry:
                continue

            self.tres.insert(tk.END, f"📌 {name}\n", "h")
            self.tres.insert(tk.END, f"{'═' * 80}\n", "info")

            # 显示设置阶段
            self.tres.insert(tk.END, "  [设置写入]\n", "sec")
            total = len(settings_log)
            ok_count = sum(1 for _, ok, _ in settings_log if ok)
            for disp, ok, msg in settings_log:
                tag = "sub_ok" if ok else "sub_fail"
                status = "设置成功" if ok else "设置失败"
                self.tres.insert(tk.END, f"  {'✅' if ok else '❌'} {disp}  →  {status}\n", tag)
                if msg and len(msg) > 2:
                    self.tres.insert(tk.END, f"     {msg[:200]}\n", "info")
                if not ok:
                    all_ok = False

            # 显示重启阶段
            if restart_entry:
                self.tres.insert(tk.END, "  [适配器重启]\n", "sec")
                rst_disp, rst_ok, rst_msg = restart_entry
                tag = "sub_ok" if rst_ok else "sub_fail"
                status = "重启成功" if rst_ok else "重启失败"
                self.tres.insert(tk.END, f"  {'✅' if rst_ok else '❌'} {rst_disp}  →  {status}\n", tag)
                if rst_msg:
                    self.tres.insert(tk.END, f"     {rst_msg[:200]}\n", "info")
                if not rst_ok:
                    all_ok = False

            self.tres.insert(tk.END, f"\n  📊 进度: {ok_count}/{total} 项设置成功\n", "info")
            self.tres.insert(tk.END, f"{'═' * 80}\n\n", "info")

        if all_ok:
            self.tres.insert(tk.END, "\n✅ 所有设置已成功应用并重启！\n", "ok")
            self._log("所有设置已成功应用并重启！")
        else:
            self.tres.insert(tk.END, "\n⚠ 部分设置应用失败，请查看详情。\n", "warn")
            self._log("部分设置应用失败")

        self._idle()

    # ── 设置 IP ──

    @staticmethod
    def _verify_ip_setting(name, expected_ip, expected_prefix):
        """设置 IP 后回读验证，返回 (verify_ok, verify_msg)"""
        s = NetMgr.get_settings(name)
        if not s:
            return False, "设置后无法读取适配器状态"
        nip = s.get("IPv4Address", "")
        np = s.get("PrefixLength", "")
        if nip == expected_ip and str(np) == str(expected_prefix):
            return True, ""
        return False, f"验证不匹配: 当前 {nip}/{np}, 预期 {expected_ip}/{expected_prefix}"

    def set_ip(self):
        """打开 IP 设置对话框（单适配器 / 批量多适配器）"""
        sel = self._selected()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个适配器")
            return

        if not is_admin():
            if messagebox.askyesno("管理员权限", "修改 IP 地址需要管理员权限。\n是否以管理员身份重新启动？"):
                elevate()
            else:
                messagebox.showerror("权限不足", "请以管理员身份运行")
            return

        if len(sel) == 1:
            # ── 单适配器 ──
            name = sel[0]
            s = NetMgr.get_settings(name)
            cur_ip = s.get("IPv4Address", "") if s else ""
            cur_pre = s.get("PrefixLength", "") if s else ""

            dlg = SetIPDialog(self.root, name, cur_ip, cur_pre)
            self.root.wait_window(dlg.dialog)
            if dlg.result is None:
                return
            ip_address, prefix, gateway = dlg.result

            self._busy(f"正在为 {name} 设置 IP...")
            self._log(f"正在设置 {name} IP → {ip_address}/{prefix}")

            def task():
                ok, msg = NetMgr.set_ipv4(name, ip_address, prefix, gateway)
                verify_ok, verify_msg = App._verify_ip_setting(name, ip_address, prefix) if ok else (False, "")
                self.root.after(0, self._show_setip_result, name, ip_address,
                                prefix, gateway, ok, msg, verify_ok, verify_msg)

            Thread(target=task, daemon=True).start()

        else:
            # ── 多适配器批量设置（并行获取当前 IP 信息）──
            adapters_info = []
            with ThreadPoolExecutor(max_workers=len(sel)) as pool:
                future_map = {pool.submit(NetMgr.get_settings, n): n for n in sel}
                for future in as_completed(future_map):
                    name = future_map[future]
                    try:
                        s = future.result()
                    except Exception:
                        s = None
                    adapters_info.append((name,
                        s.get("IPv4Address", "") if s else "",
                        s.get("PrefixLength", "") if s else ""))
            # 恢复原始顺序
            name_order = {n: i for i, n in enumerate(sel)}
            adapters_info.sort(key=lambda x: name_order.get(x[0], 99))

            dlg = MultiSetIPDialog(self.root, adapters_info)
            self.root.wait_window(dlg.dialog)
            if dlg.result is None:
                return
            target_list = dlg.result  # [(name, ip, prefix, gateway), ...]

            self._busy(f"正在为 {len(target_list)} 个适配器设置 IP...")
            for n, ip, pre, gw in target_list:
                self._log(f"正在设置 {n} IP → {ip}/{pre}")

            def task():
                all_results = []
                with ThreadPoolExecutor(max_workers=len(target_list)) as pool:
                    def set_one(n, ip, pre, gw):
                        ok, msg = NetMgr.set_ipv4(n, ip, pre, gw)
                        verify_ok, verify_msg = App._verify_ip_setting(n, ip, pre) if ok else (False, "")
                        return (n, ip, pre, gw, ok, msg, verify_ok, verify_msg)
                    future_map = {
                        pool.submit(set_one, n, ip, pre, gw): n
                        for n, ip, pre, gw in target_list
                    }
                    for future in as_completed(future_map):
                        try:
                            with self._lock:
                                all_results.append(future.result())
                        except Exception as e:
                            name = future_map[future]
                            with self._lock:
                                all_results.append((name, "", 0, None, False, str(e), False, ""))
                        self._log(f"{future_map[future]} IP 设置完成")
                # 按原始顺序排序以保持 UI 一致性
                name_order = {n: i for i, (n, *_) in enumerate(target_list)}
                all_results.sort(key=lambda r: name_order.get(r[0], 99))
                self.root.after(0, self._show_multi_setip_result, all_results)

            Thread(target=task, daemon=True).start()

    def _show_setip_result(self, name, ip_address, prefix, gateway,
                           ok, msg, verify_ok, verify_msg):
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)
        success = self._write_ip_result_row(name, ip_address, prefix, gateway,
                                            ok, msg, verify_ok, verify_msg)
        self.tres.insert(tk.END, f"{'═' * 80}\n", "info")
        if success:
            self._log(f"{name} IP 设置成功: {ip_address}/{prefix}")
        else:
            self._log(f"{name} IP 设置失败" if not ok else f"{name} IP 设置验证异常")
        self._idle()

    def _show_multi_setip_result(self, results):
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)

        all_ok = True
        for args in results:
            success = self._write_ip_result_row(*args)
            self.tres.insert(tk.END, f"{'═' * 80}\n\n", "info")
            if not success:
                all_ok = False

        if all_ok:
            self.tres.insert(tk.END, "\n✅ 所有 IP 配置已完成\n", "ok")
            self._log("所有 IP 设置成功")
        else:
            self.tres.insert(tk.END, "\n⚠ 部分 IP 配置未完成\n", "warn")
            self._log("部分 IP 设置失败")
        self._idle()

    def _write_ip_result_row(self, name, ip_address, prefix, gateway,
                             ok, msg, verify_ok, verify_msg):
        """写单个适配器的 IP 设置结果行，返回是否成功"""
        self.tres.insert(tk.END, f"📌 {name}  —  IP 地址设置\n", "h")
        self.tres.insert(tk.END, f"{'═' * 80}\n", "info")
        disp_gw = f", 网关 {gateway}" if gateway else ""

        if ok and verify_ok:
            self.tres.insert(tk.END, "  ✅ IP 地址设置成功\n", "sub_ok")
            self.tres.insert(tk.END, f"     新 IP: {ip_address}/{prefix}{disp_gw}\n", "info")
            return True
        else:
            if not ok:
                self.tres.insert(tk.END, "  ❌ IP 地址设置失败\n", "sub_fail")
                self.tres.insert(tk.END, f"     原因: {msg}\n", "warn")
            else:
                self.tres.insert(tk.END, "  ⚠ IP 地址设置报告成功，但验证发现异常\n", "warn")
                self.tres.insert(tk.END, f"     {verify_msg}\n", "warn")
            return False

    # ── 重命名 ──

    def rename(self):
        """打开批量重命名对话框"""
        sel = self._selected()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个适配器")
            return

        if not is_admin():
            if messagebox.askyesno("管理员权限", "重命名适配器需要管理员权限。\n是否以管理员身份重新启动？"):
                elevate()
            else:
                messagebox.showerror("权限不足", "请以管理员身份运行")
            return

        adapters_info = [(n, n) for n in sel]
        dlg = RenameDialog(self.root, adapters_info)
        self.root.wait_window(dlg.dialog)
        if dlg.result is None:
            return
        target_list = dlg.result

        self._busy(f"正在重命名 {len(target_list)} 个适配器...")
        for n, nn in target_list:
            self._log(f"正在重命名 {n} → {nn}")

        def task():
            results = []
            with ThreadPoolExecutor(max_workers=len(target_list)) as pool:
                future_map = {
                    pool.submit(NetMgr.rename_adapter, n, nn): n
                    for n, nn in target_list
                }
                for future in as_completed(future_map):
                    name = future_map[future]
                    try:
                        ok, msg = future.result()
                        with self._lock:
                            results.append((name, msg, ok))
                    except Exception as e:
                        with self._lock:
                            results.append((name, str(e), False))
                    self._log(f"{name} 重命名完成")
            self.root.after(0, self._show_rename_result, results)

        Thread(target=task, daemon=True).start()

    def _show_rename_result(self, results):
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)

        all_ok = True
        for i, (name, msg, ok) in enumerate(results):
            if i > 0:
                self.tres.insert(tk.END, f"{'═' * 80}\n\n", "info")
            self.tres.insert(tk.END, f"📌 {name}\n", "h")
            if ok:
                self.tres.insert(tk.END, "  ✅ 重命名成功\n", "sub_ok")
            else:
                self.tres.insert(tk.END, f"  ❌ 重命名失败\n", "sub_fail")
                self.tres.insert(tk.END, f"     原因: {msg}\n", "warn")
                all_ok = False

        if all_ok:
            self.tres.insert(tk.END, "\n✅ 所有适配器已重命名\n", "ok")
            self._log("所有适配器重命名成功")
        else:
            self.tres.insert(tk.END, "\n⚠ 部分适配器重命名失败\n", "warn")
            self._log("部分适配器重命名失败")
        self._idle()
        self.root.after(1200, self.refresh)

    def _on_toggle_pm(self):
        """对所选适配器批量关闭/恢复电源管理"""
        sel = self._selected()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个适配器")
            return

        pm = NetMgr.get_power_settings(sel[0])
        if not pm:
            self._log(f"{sel[0]}: 不支持电源管理")
            return

        def task(enable):
            results = []
            for n in sel:
                label = "恢复" if enable else "关闭"
                self._busy(f"正在{label} {n} 电源管理...")
                if enable:
                    ok, msg = NetMgr.enable_power_saving(n)
                else:
                    ok, msg = NetMgr.disable_power_saving(n)
                results.append((n, ok, msg))
            self.root.after(0, self._show_pm_result, results)

        def on_disable():
            dlg.destroy()
            Thread(target=lambda: task(False), daemon=True).start()

        def on_enable():
            dlg.destroy()
            Thread(target=lambda: task(True), daemon=True).start()

        allow_val = pm.get("AllowComputerToTurnOffDevice", 0)
        wol_magic = pm.get("WakeOnMagicPacket", 0)
        wol_pattern = pm.get("WakeOnPattern", 0)
        allow_txt = {0: "不支持", 1: "关闭✅", 2: "开启❌"}.get(allow_val, str(allow_val))
        magic_txt = {0: "不支持", 1: "关闭", 2: "开启"}.get(wol_magic, str(wol_magic))
        pattern_txt = {0: "不支持", 1: "关闭", 2: "开启"}.get(wol_pattern, str(wol_pattern))

        dlg = tk.Toplevel(self.root)
        dlg.title(f"电源管理 - {len(sel)} 个适配器")
        dlg.configure(bg="#0d1117")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        f = ttk.Frame(dlg, padding=12)
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(f, text=f"已选 {len(sel)} 个适配器", font=("", 10, "bold")).pack(anchor=tk.W)
        ttk.Label(f, text=f"参考状态 ({sel[0]}):", foreground="#8b949e").pack(anchor=tk.W)
        for line in [
            f"  允许关闭以节能: {allow_txt}",
            f"  允许此设备唤醒(魔术包): {magic_txt}",
            f"  允许此设备唤醒(模式匹配): {pattern_txt}",
        ]:
            ttk.Label(f, text=line, foreground="#8b949e").pack(anchor=tk.W, pady=1)
        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        bf = ttk.Frame(f)
        bf.pack(fill=tk.X)
        ttk.Button(bf, text="❌ 关闭节能", command=on_disable).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="✅ 恢复节能", command=on_enable).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="取消", command=dlg.destroy).pack(side=tk.RIGHT, padx=2)
        center_dialog(dlg, self.root)

    def _show_pm_result(self, results):
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)
        for name, ok, msg in results:
            self.tres.insert(tk.END, f"\U0001f4cc {name}\n", "h")
            self.tres.insert(tk.END, f"{'═' * 80}\n", "info")
            self.tres.insert(tk.END, f"  {'✅' if ok else '❌'} {msg}\n",
                            "sub_ok" if ok else "sub_fail")
        self._idle()
        self.root.after(1200, self.refresh)

    def scan_cameras(self):
        """通过 MVS SDK 扫描相机"""
        self._busy("正在扫描相机...\n(MVS SDK 枚举，约 2 秒)")

        def task():
            try:
                adapter_ips = NetMgr.get_adapter_ips()
                cameras, diag = GigEVisionScanner.discover()
                mapped = GigEVisionScanner.map_to_adapters(cameras, adapter_ips)
                self.root.after(0, self._show_camera_results, cameras, mapped, adapter_ips, diag)
            except Exception as e:
                self.root.after(0, self._show_camera_error, str(e))

        Thread(target=task, daemon=True).start()

    def _show_camera_results(self, cameras, mapped, adapter_ips, diag=None):
        """显示相机扫描结果"""
        self._idle()
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)

        self.tres.insert(tk.END, f"📡 相机扫描结果: ", "h")
        self.tres.insert(tk.END, f"发现 {len(cameras)} 台\n", "ok")
        if diag and diag.get('error'):
            self.tres.insert(tk.END, f"\n[错误] {diag['error']}\n", "warn")

        # 显示所有相机（简洁模式：IP + MAC）
        all_cams = list(cameras)
        if all_cams:
            self.tres.insert(tk.END, f"{'═' * 60}\n", "info")
            for cam in all_cams:
                aname = cam.get('adapter')
                tag = "dim" if aname else "sub_ok"
                self.tres.insert(tk.END, f"  → {cam['ip']}", tag)
                if cam.get('subnet'):
                    self.tres.insert(tk.END, f" (/{cam['subnet'].split('.')[-1]})", "dim")
                if aname:
                    self.tres.insert(tk.END, f"  ← {aname}", "dim")
                if cam.get('mac'):
                    self.tres.insert(tk.END, f"\n     MAC: {cam['mac']}", "dim")
                self.tres.insert(tk.END, "\n", "info")
            self.tres.insert(tk.END, f"{'═' * 60}\n", "info")

        self.tres.config(state=tk.DISABLED)

    def _show_camera_error(self, err_msg):
        """显示相机扫描错误"""
        self._idle()
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)
        self.tres.insert(tk.END, "📡 相机扫描失败\n", "h")
        self.tres.insert(tk.END, f"{'═' * 80}\n", "info")
        self.tres.insert(tk.END, f"  ❌ {err_msg}\n", "warn")
        self.tres.insert(tk.END, f"{'═' * 80}\n", "info")
        self.tres.config(state=tk.DISABLED)


    def _log_do(self, ts, msg):
        """在主线程中执行日志写入（由 _log 通过 after 调度）"""
        self.tlog.config(state=tk.NORMAL)
        self.tlog.insert(tk.END, f"[{ts}] ", "ts")
        self.tlog.insert(tk.END, f"{msg}\n", "info")
        self.tlog.see(tk.END)
        self.tlog.config(state=tk.DISABLED)


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────
def main():
    # 设置控制台编码（仅对 .py 模式有效）
    if sys.platform == "win32" and not getattr(sys, 'frozen', False):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    # 检查 NetAdapter 模块
    out, _, rc = run_ps(
        "if (Get-Module -ListAvailable -Name NetAdapter) { 'OK' } else { 'NO' }"
    )
    if "NO" in out or rc != 0:
        messagebox.showerror(
            "系统不兼容",
            "未检测到 PowerShell NetAdapter 模块。\n"
            "需要 Windows 8/Server 2012 或更高版本。",
        )
        sys.exit(1)

    try:
        root = tk.Tk()
        root.title(f"{APP_NAME} {APP_VERSION}")
        root.geometry("950x720")
        root.minsize(800, 600)
        root.configure(bg="#0d1117")

        # 设置图标（如果有）
        try:
            if getattr(sys, 'frozen', False):
                icon_path = os.path.join(sys._MEIPASS, "icon.ico")
                if os.path.exists(icon_path):
                    root.iconbitmap(icon_path)
        except Exception as e:
            print(f"加载图标失败: {e}")

        app = App(root)
        root.mainloop()
    except Exception as e:
        messagebox.showerror("启动错误", f"程序初始化失败:\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
