"""
相机网络配置工具 Camera Network Configuration Tool
适用系统: Windows 8/10/11, Windows Server 2012+
测试环境: IntelCPU, Win10系统, HK一体式工控机

功能:
  1. 扫描并列出已连接的网络适配器
  2. 检查 IPv4、巨型帧、缓冲区、中断裁决、电源管理等设置
  3. 一键应用相机网络推荐配置
  4. 一键重命名多个网络适配器
  5. 扫描 GigE 相机并修改相机 IP
"""

import sys
import os
import ctypes
import tkinter as tk

from .net_mgr import APP_NAME, APP_VERSION, NetMgr, is_admin, elevate
from .ui import App


def main():
    """程序入口：检查权限、初始化 GUI、启动事件循环"""
    # 管理员权限检查
    if not is_admin():
        if tk.messagebox.askyesno("权限不足", "本程序需要管理员权限才能正常运行。\\n是否以管理员身份重新启动？"):
            elevate()
        else:
            tk.messagebox.showerror("权限不足", "请以管理员身份运行")
            return

    try:
        # 检查 NetAdapter 模块是否可用
        adapters = NetMgr.enum_adapters()
        root = tk.Tk()
        root.title(f"{APP_NAME} {APP_VERSION}")
        root.configure(bg="#0d1117")
        root.minsize(900, 600)

        # 尝试设置图标（仅对 PyInstaller 打包有效）
        try:
            if getattr(sys, 'frozen', False):
                icon_path = os.path.join(sys._MEIPASS, "icon.ico")
                if os.path.exists(icon_path):
                    root.iconbitmap(icon_path)
        except Exception:
            pass

        app = App(root)
        root.mainloop()
    except Exception as e:
        tk.messagebox.showerror("启动错误", f"程序初始化失败:\\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
