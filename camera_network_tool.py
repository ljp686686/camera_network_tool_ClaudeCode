#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
相机网络配置工具 Camera Network Configuration Tool
适用系统: Windows 8/10/11, Windows Server 2012+
测试环境:IntelCPU,Win10系统,HK一体式工控机

功能:
  1. 扫描并列出已连接的网络适配器
  2. 检查 IPv4、巨型帧、缓冲区、中断裁决、电源管理率等设置
  3. 一键应用相机网络推荐配置
  4.一键重命名多个网络适配器
"""

import sys
import subprocess
import json
import re
import tkinter as tk
from tkinter import ttk, messagebox
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import ctypes

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
APP_NAME = "相机网络配置工具"
APP_VERSION = "v2.3"

# 每种设置的候选注册表关键字列表（按优先级排列）
# 不同网卡驱动可能使用不同的关键字
SETTING_DEFS = [
    {
        "id": "jumbo",
        "display": "巨型帧 (Jumbo Packet)",
        "target_display": "9014 Bytes",
        "target": 9014,
        "type": "int",
        "keywords": ["*JumboPacket"],
    },
    {
        "id": "tx_buffers",
        "display": "传输缓冲区 (Transmit Buffers)",
        "target_display": "2048",
        "target": 2048,
        "type": "int",
        "keywords": ["*TransmitBuffers", "TransmitBufferLen"],
    },
    {
        "id": "rx_buffers",
        "display": "接收缓冲区 (Receive Buffers)",
        "target_display": "2048",
        "target": 2048,
        "type": "int",
        "keywords": ["*ReceiveBuffers", "ReceiveBufferLen"],
    },
    {
        "id": "int_mod",
        "display": "中断裁决率 (Interrupt Moderation)",
        "target_display": "极值 (Extreme)",
        "target": "extreme",
        "type": "extreme",
        # 优先使用 Rate 类关键字（实际速率控制），其次使用开关类
        "rate_keywords": ["*InterruptModerationRate", "ITR", "InterruptThrottleRate"],
        "onoff_keywords": ["*InterruptModeration"],
    },
    {
        "id": "power_mgmt",
        "display": "电源管理 (Power Management)",
        "target_display": "节能已关闭",
        "type": "power",
    },
]

# 接口类型: 6=以太网, 71=WLAN, 其它
INTERFACE_TYPE_ETHERNET = 6


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate():
    """以管理员身份重新启动（支持 .exe 和 .py 两种模式）"""
    if is_admin():
        return
    frozen = getattr(sys, 'frozen', False)
    # 参数拼接：PyInstaller .exe 不传脚本路径，.py 需要传脚本路径
    args = " ".join(f'"{a}"' if ' ' in a else a for a in (sys.argv[1:] if frozen else sys.argv))
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)
    sys.exit(0)


def run_ps(script, timeout=30):
    """
    执行 PowerShell -Command，返回 (stdout, stderr, retcode)
    静默执行，不弹出 PowerShell 窗口
    """
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        proc = subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command",
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out_bytes, err_bytes = proc.communicate(timeout=timeout)
        # 尝试用 UTF-8 解码（我们设置了输出编码为 UTF-8）
        out = out_bytes.decode("utf-8", errors="replace").strip()
        err = err_bytes.decode("utf-8", errors="replace").strip()
        # 去除可能的 BOM
        out = out.lstrip("﻿").strip()
        err = err.lstrip("﻿").strip()
        return out, err, proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()  # 回收子进程，防止僵尸进程泄漏
        return "", "执行超时", -1
    except Exception as e:
        return "", str(e), -1


def ps_escape_sq(value):
    """转义字符串，安全插入 PowerShell 单引号字符串 ('...') 中"""
    return str(value).replace("'", "''")


def to_int(v, default=None):
    try:
        # PowerShell JSON 有时把数值包装成数组
        if isinstance(v, list):
            v = v[0]
        return int(v)
    except (ValueError, TypeError, IndexError):
        return default


def psv(prop_dict):
    """
    安全提取 Get-NetAdapterAdvancedProperty 的 RegistryValue.
    PowerShell 返回的 JSON 中 RegistryValue 可能是标量或数组.
    """
    raw = prop_dict.get("RegistryValue")
    if isinstance(raw, list):
        return raw[0] if raw else None
    return raw


def validate_ip(ip_str):
    """验证 IPv4 地址格式，返回 (ok, 错误消息)"""
    ip_str = ip_str.strip()
    m = re.match(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$', ip_str)
    if not m:
        return False, "IP 地址格式无效（应为 x.x.x.x）"
    for octet in m.groups():
        if int(octet) > 255:
            return False, f"IP 地址段 {octet} 超出范围（0-255）"
    return True, ""


def validate_prefix(prefix_str):
    """验证子网前缀长度，返回 (ok, 错误消息)"""
    prefix_str = prefix_str.strip()
    try:
        prefix = int(prefix_str)
    except ValueError:
        return False, "子网前缀必须为数字（0-32）"
    if prefix < 0 or prefix > 32:
        return False, f"子网前缀 {prefix} 超出范围（0-32）"
    return True, ""


def center_dialog(dialog, parent):
    """将对话框相对于父窗口居中"""
    dialog.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - dialog.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_height()) // 2
    dialog.geometry(f"+{x}+{y}")


# ──────────────────────────────────────────────
# 业务逻辑
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# 业务逻辑层：网络适配器检测与配置
# ──────────────────────────────────────────────
class NetMgr:
    """通过 PowerShell NetAdapter 模块管理网络适配器"""

    # ── PS 脚本（不含 param 块，供 -Command 模式使用）──

    PS_ENUM = r"""
$ErrorActionPreference = 'Stop'
try {
    $mod = Get-Module -ListAvailable -Name NetAdapter
    if (-not $mod) { Write-Error 'NO_MODULE'; exit 1 }
    $list = Get-NetAdapter -Physical | Select-Object Name,InterfaceDescription,InterfaceIndex,Status,LinkSpeed,MacAddress,MediaConnectedState,InterfaceType
    $list | ConvertTo-Json -Compress
} catch {
    Write-Error $_.Exception.Message; exit 1
}
"""

    PS_SET_IPV4 = r"""
$ErrorActionPreference = 'Stop'
try {
    $name = '__NAME__'
    $ip = '__IP__'
    $prefix = __PREFIX__
    $gateway = '__GATEWAY__'
    Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $name -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    if ($gateway.Length -gt 0) {
        New-NetIPAddress -InterfaceAlias $name -IPAddress $ip -PrefixLength $prefix -DefaultGateway $gateway -ErrorAction Stop | Out-Null
    } else {
        New-NetIPAddress -InterfaceAlias $name -IPAddress $ip -PrefixLength $prefix -ErrorAction Stop | Out-Null
    }
    Write-Output 'OK'
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
"""

    PS_RESTART_ADAPTER = r"""
$ErrorActionPreference = 'Stop'
try {
    Restart-NetAdapter -Name '__NAME__' -Confirm:$false -ErrorAction Stop
    Write-Output 'OK'
} catch {
    Write-Error $_.Exception.Message; exit 1
}
"""

    # PS 脚本已改为内联构建（见 get_power_settings / _set_power_saving）

    @staticmethod
    def _fill(cmd, **kw):
        """用 Python 值替换 PS 中的占位符 __KEY__（长 key 优先防 substring 冲突）"""
        for k, v in sorted(kw.items(), key=lambda x: -len(x[0])):
            cmd = cmd.replace(f"__{k}__", str(v))
        return cmd

    @staticmethod
    def enum_adapters():
        out, err, rc = run_ps(NetMgr.PS_ENUM)
        if rc != 0 or not out or out in ("[]", ""):
            return []
        try:
            data = json.loads(out)
            return data if isinstance(data, list) else [data]
        except json.JSONDecodeError:
            return []

    @staticmethod
    def get_settings(name):
        """一次性获取 IPv4、高级属性"""
        qn = ps_escape_sq(name)
        script = NetMgr._fill(r"""
$ErrorActionPreference = 'Stop'
$r = @{}
# IPv4
$ip = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias '__NAME__' -ErrorAction SilentlyContinue
if ($ip) { $r['IPv4Address'] = $ip.IPAddress; $r['PrefixLength'] = $ip.PrefixLength }
else { $r['IPv4Address'] = $null; $r['PrefixLength'] = $null }
# 高级属性
$ap = Get-NetAdapterAdvancedProperty -Name '__NAME__' -ErrorAction SilentlyContinue
$ah = @{}
if ($ap) { foreach ($p in $ap) { $ah[$p.RegistryKeyword] = @{ RegistryValue = $p.RegistryValue; DisplayValue = $p.DisplayValue; DisplayName = $p.DisplayName; ValidValues = $p.ValidRegistryValues; ValidDisplayNames = $p.ValidDisplayValues } } }
$r['AdvancedProperties'] = $ah
# ── 电源管理 ──
$r['PowerManagement'] = @{}
$r | ConvertTo-Json -Compress -Depth 5
""", NAME=qn)
        out, _, rc = run_ps(script)
        if rc != 0 or not out or out == "null":
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def find_keyword(name, candidates):
        """在适配器属性中查找第一个匹配的候选注册表关键字，返回 (keyword, prop) 或 (None, None)"""
        settings = NetMgr.get_settings(name)
        if not settings:
            return None, None
        props = settings.get("AdvancedProperties", {})
        for k in candidates:
            if k in props:
                return k, props[k]
        return None, None

    @staticmethod
    def set_advanced(name, keyword, value):
        """设置高级属性，自动判断数值/字符串"""
        qn = ps_escape_sq(name)
        qk = ps_escape_sq(keyword)
        if isinstance(value, int):
            body = f"""
$ErrorActionPreference = 'Stop'
$cur = Get-NetAdapterAdvancedProperty -Name '{qn}' -RegistryKeyword '{qk}' -ErrorAction SilentlyContinue
if (-not $cur) {{ Write-Error "不支持关键字 '{qk}'"; exit 1 }}
Set-NetAdapterAdvancedProperty -Name '{qn}' -RegistryKeyword '{qk}' -RegistryValue {value} -ErrorAction Stop
Write-Output 'OK'
"""
        else:
            body = f"""
$ErrorActionPreference = 'Stop'
$cur = Get-NetAdapterAdvancedProperty -Name '{qn}' -RegistryKeyword '{qk}' -ErrorAction SilentlyContinue
if (-not $cur) {{ Write-Error "不支持关键字 '{qk}'"; exit 1 }}
Set-NetAdapterAdvancedProperty -Name '{qn}' -RegistryKeyword '{qk}' -RegistryValue '{value}' -ErrorAction Stop
Write-Output 'OK'
"""
        out, err, rc = run_ps(body)
        return rc == 0, (out or err)

    @staticmethod
    def set_ipv4(name, ip_address, prefix_length, gateway=None):
        """设置静态 IPv4 地址"""
        if gateway:
            gateway = gateway.strip()
        qn = ps_escape_sq(name)
        script = NetMgr._fill(
            NetMgr.PS_SET_IPV4,
            NAME=qn, IP=ps_escape_sq(ip_address.strip()),
            PREFIX=str(int(prefix_length)),
            GATEWAY=ps_escape_sq(gateway) if gateway else ""
        )
        out, err, rc = run_ps(script)
        if rc == 0 and out.strip() == "OK":
            return True, "IP 地址设置成功"
        return False, (err or out or "未知错误")

    @staticmethod
    def restart_adapter(name, timeout_sec=60):
        """重启网络适配器（禁用后重新启用），使高级设置生效"""
        qn = ps_escape_sq(name)
        script = NetMgr._fill(NetMgr.PS_RESTART_ADAPTER, NAME=qn)
        out, err, rc = run_ps(script, timeout=timeout_sec)
        if rc == 0 and out.strip() == "OK":
            return True, "适配器已重启"
        return False, (err or out or "重启失败")

    @staticmethod
    def rename_adapter(name, new_name):
        """重命名网络适配器"""
        qn = ps_escape_sq(name)
        qnn = ps_escape_sq(new_name)
        script = f"""
$ErrorActionPreference = 'Stop'
try {{
    Rename-NetAdapter -Name '{qn}' -NewName '{qnn}' -Confirm:$false -ErrorAction Stop
    Write-Output 'OK'
}} catch {{
    Write-Error $_.Exception.Message; exit 1
}}
"""
        out, err, rc = run_ps(script)
        if rc == 0 and out.strip() == "OK":
            return True, "重命名成功"
        return False, (err or out or "重命名失败")

    @staticmethod
    def get_power_settings(name):
        """查询适配器电源管理状态，返回 dict 或 None"""
        qn = ps_escape_sq(name)
        script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$pm = Get-NetAdapterPowerManagement -Name '{qn}'
if (-not $pm) {{ exit 1 }}
@{{
    AllowComputerToTurnOffDevice = [int]$pm.AllowComputerToTurnOffDevice
    WakeOnMagicPacket = [int]$pm.WakeOnMagicPacket
    WakeOnPattern = [int]$pm.WakeOnPattern
}} | ConvertTo-Json -Compress
"""
        out, _, rc = run_ps(script)
        if rc != 0 or not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _set_power_saving(name, enable):
        """启用或关闭电源管理"""
        qn = ps_escape_sq(name)
        cmd = "Enable" if enable else "Disable"
        state = "Enabled" if enable else "Disabled"
        msg = "电源管理已恢复默认" if enable else "电源节能已关闭"
        err_msg = "恢复失败" if enable else "关闭失败"
        script = f"""
$ErrorActionPreference = 'Stop'
try {{
    $pm = Get-NetAdapterPowerManagement -Name '{qn}' -ErrorAction Stop
    $pm.AllowComputerToTurnOffDevice = '{state}'
    $pm.WakeOnMagicPacket = '{state}'
    $pm.WakeOnPattern = '{state}'
    $pm | Set-NetAdapterPowerManagement -NoRestart -ErrorAction Stop
    if ('{cmd}' -eq 'Enable') {{
        Enable-NetAdapterPowerManagement -Name '{qn}' -WakeOnMagicPacket -WakeOnPattern -ErrorAction Stop
    }}
    Write-Output 'OK'
}} catch {{
    Write-Output "ERR: $($_.Exception.Message)"
    exit 1
}}
"""
        out, err, _ = run_ps(script)
        status = out.strip()
        if status == "OK":
            return True, msg
        if status.startswith("ERR:"):
            return False, status[4:].strip()
        return False, (err or out or err_msg)

    disable_power_saving = lambda n: NetMgr._set_power_saving(n, False)
    enable_power_saving = lambda n: NetMgr._set_power_saving(n, True)

    @staticmethod
    def get_valid_values(name, keyword):
        """查询指定注册表关键字的所有有效值及对应显示名称，返回 [(registry_value, display_name), ...] 或 None"""
        qn = ps_escape_sq(name)
        qk = ps_escape_sq(keyword)
        script = f"""
$ErrorActionPreference = 'Stop'
$p = Get-NetAdapterAdvancedProperty -Name '{qn}' -RegistryKeyword '{qk}' -ErrorAction SilentlyContinue
if (-not $p) {{ exit 1 }}
$v = $p.ValidRegistryValues
$d = $p.ValidDisplayValues
if (-not $v -or -not $d) {{ exit 1 }}
$r = @()
for ($i = 0; $i -lt $v.Count; $i++) {{
    $r += @{{ v = $v[$i]; d = $d[$i] }}
}}
$r | ConvertTo-Json -Compress
"""
        out, _, rc = run_ps(script)
        if rc != 0 or not out:
            return None
        try:
            data = json.loads(out)
            if isinstance(data, list):
                return [(item["v"], item["d"]) for item in data]
            return [(data["v"], data["d"])]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    # ── 检查 / 应用辅助方法 ──

    @staticmethod
    def _find_key(keywords, props):
        """在 props 中查找第一个匹配的关键字"""
        for kw in keywords:
            if kw in props:
                return kw
        return None

    @staticmethod
    def _get_extreme_value(valid_pairs):
        """从 [(value, display)] 中查找极值对应的数值"""
        for v, d in valid_pairs:
            if "极" in d or "extreme" in d.lower():
                return v
        return None

    @staticmethod
    def _check_int_setting(sd, props):
        """检查普通 int 类型设置（巨型帧、缓冲区），返回 (cat, id, disp, cur, tgt, ok)"""
        found_key = NetMgr._find_key(sd.get("keywords", []), props)
        if found_key is None:
            return ("高级设置", sd["id"], sd["display"], "不支持", sd["target_display"], False)
        raw = psv(props[found_key])
        disp = props[found_key].get("DisplayValue", str(raw) if raw is not None else "?")
        val = to_int(raw)
        ok = (val == sd["target"])
        return ("高级设置", sd["id"], sd["display"],
                f"{disp} ({val})" if val is not None else str(disp),
                sd["target_display"], ok)

    @staticmethod
    def _check_int_moderation(sd, props, name=None):
        """检查中断裁决设置：先查开启/关闭，再查速率等级"""
        results = []

        # ── 阶段 1：检查 On/Off 开关 ──
        onoff_key = NetMgr._find_key(sd.get("onoff_keywords", []), props)

        if onoff_key:
            raw = psv(props[onoff_key])
            raw_int = to_int(raw)
            disp = props[onoff_key].get("DisplayValue", str(raw) if raw is not None else "?")
            onoff_ok = (raw_int is not None and raw_int >= 1)
            cur = f"{disp}({raw_int})" if raw_int is not None else str(disp)
            results.append(("高级设置", "int_mod_onoff", "中断裁决 (开启/关闭)",
                            cur, "开启 (≥1)", onoff_ok))

        # ── 阶段 2：检查速率等级 ──
        rate_key = NetMgr._find_key(sd.get("rate_keywords", []), props)

        if rate_key:
            raw = psv(props[rate_key])
            disp = props[rate_key].get("DisplayValue", str(raw) if raw is not None else "?")
            raw_int = to_int(raw)

            # 获取该关键字的所有可选值映射
            valid_pairs = None
            vv = props[rate_key].get("ValidValues")
            vn = props[rate_key].get("ValidDisplayNames")
            if vv and vn:
                valid_pairs = list(zip(vv, vn))
            elif name:
                valid_pairs = NetMgr.get_valid_values(name, rate_key)

            if valid_pairs:
                # 从映射中找"极值"对应的数值
                extreme_target = NetMgr._get_extreme_value(valid_pairs)
                target_str = f"极值({extreme_target})" if extreme_target is not None else "极值 (≥4)"
                # 用驱动映射显示当前值名称
                cur_name = dict(valid_pairs).get(raw_int)
                cur_disp = f"{cur_name or disp}({raw_int})" if raw_int is not None else str(disp)
                # 直接数值比较判定
                ei = int(extreme_target) if extreme_target is not None else None
                ri = int(raw_int) if raw_int is not None else None
                is_extreme = (ei is not None and ri is not None and ri == ei)
            else:
                # 回退到启发式判断
                dl = disp.lower()
                if "极" in disp or "extreme" in dl:
                    is_extreme = True
                elif "适应" in disp or "adaptive" in dl or "关闭" in disp or "off" in dl or "disabled" in dl:
                    is_extreme = False
                elif raw_int is not None:
                    is_extreme = raw_int >= 4 if rate_key not in ("ITR", "InterruptThrottleRate") else False
                else:
                    is_extreme = False
                cur_disp = f"极值({raw_int})" if is_extreme else f"{disp}({raw_int})"
                target_str = "极值 (≥4)"

            results.append(("高级设置", sd["id"], sd["display"] + " (速率)",
                            cur_disp, target_str, is_extreme))
        else:
            # 无 rate 关键字时，若 onoff 为 2 (极值) 则算达标
            if onoff_key:
                raw_int = to_int(psv(props[onoff_key]))
                is_extreme = (raw_int == 2)
                results.append(("高级设置", sd["id"], sd["display"],
                                f"{disp}({raw_int})" if raw_int is not None else str(disp),
                                "极值 (2=Extreme)", is_extreme))
            else:
                results.append(("高级设置", sd["id"], sd["display"], "不支持", sd["target_display"], False))
        return results

    @staticmethod
    def check_adapter(name):
        """全面检查适配器，返回 [(category, id, display, current_str, target_str, ok), ...]"""
        results = []
        settings = NetMgr.get_settings(name)
        if not settings:
            return [("错误", "read_fail", "读取适配器设置", "无法读取", "N/A", False)]

        ip = settings.get("IPv4Address")
        prefix = settings.get("PrefixLength", "")
        results.append(("IP配置", "ipv4", "IPv4 地址",
                        f"{ip}/{prefix}" if ip else "未配置",
                        "已配置", bool(ip)))

        props = settings.get("AdvancedProperties", {})
        for sd in SETTING_DEFS:
            if sd["type"] == "extreme":
                results.extend(NetMgr._check_int_moderation(sd, props, name=name))
            elif sd["type"] == "power":
                results.extend(NetMgr._check_power_setting(sd, props, name=name))
            else:
                results.append(NetMgr._check_int_setting(sd, props))
        return results

    @staticmethod
    def _apply_int_setting(name, sd, props):
        """应用普通 int 类型设置（巨型帧、缓冲区）"""
        for kw in sd.get("keywords", []):
            if kw in props:
                ok, msg = NetMgr.set_advanced(name, kw, sd["target"])
                return (f"{sd['display']} → {sd['target_display']}", ok, msg)
        return (sd["display"], False, "此适配器不支持该设置")

    @staticmethod
    def _apply_extreme_setting(name, sd, props):
        """应用中断裁决设置：先开启裁决开关，再设速率为极值"""
        ok = True
        details = []

        # ── 阶段 1：开启裁决开关 ──
        onoff_key = NetMgr._find_key(sd.get("onoff_keywords", []), props)
        if onoff_key:
            oo_ok, _ = NetMgr.set_advanced(name, onoff_key, 2)
            if not oo_ok:
                oo_ok, _ = NetMgr.set_advanced(name, onoff_key, 1)
            details.append("开关" + ("已开启" if oo_ok else "开启失败"))
            ok = ok and oo_ok

        # ── 阶段 2：设速率为极值 ──
        rate_key = NetMgr._find_key(sd.get("rate_keywords", []), props)
        if rate_key:
            rate_ok = False
            # 优先使用 props 中已有的有效值映射（避免额外 PS 调用）
            vv = props[rate_key].get("ValidValues")
            vn = props[rate_key].get("ValidDisplayNames")
            if vv and vn:
                extreme_target = NetMgr._get_extreme_value(zip(vv, vn))
                if extreme_target is not None:
                    rate_ok, _ = NetMgr.set_advanced(name, rate_key, extreme_target)

            if not rate_ok:
                valid_pairs = NetMgr.get_valid_values(name, rate_key)
                if valid_pairs:
                    extreme_target = NetMgr._get_extreme_value(valid_pairs)
                    if extreme_target is not None:
                        rate_ok, _ = NetMgr.set_advanced(name, rate_key, extreme_target)

            if not rate_ok:
                for val in (6, 5, 4, 3):
                    rate_ok, _ = NetMgr.set_advanced(name, rate_key, val)
                    if rate_ok:
                        break
            details.append("速率" + ("已设为极值" if rate_ok else "设置失败"))
            ok = ok and rate_ok

        if not onoff_key and not rate_key:
            return (sd["display"], False, "此适配器不支持该设置")

        label = "中断裁决 → 开启+极值" if ok else "中断裁决 → 设置失败"
        return (label, ok, " | ".join(details))

    @staticmethod
    def _check_power_setting(sd, props, name=None):
        """检查电源管理设置：合并为一行结果"""
        pm = NetMgr.get_power_settings(name) if name else None
        if not pm:
            return [("高级设置", sd["id"], sd["display"], "不支持电源管理", sd["target_display"], True)]

        allow = pm.get("AllowComputerToTurnOffDevice", 0)
        wol = (pm.get("WakeOnMagicPacket", 0), pm.get("WakeOnPattern", 0))
        allow_ok = (allow != 2)
        wol_ok = not any(v == 2 for v in wol)
        cur = f"{'关闭✅' if allow in (0,1) else '开启❌'}, {'唤醒关✅' if wol_ok else '唤醒开❌'}"
        return [("高级设置", sd["id"], sd["display"], cur, sd["target_display"], allow_ok and wol_ok)]

    @staticmethod
    def _apply_power_setting(name, sd, props):
        ok, msg = NetMgr.disable_power_saving(name)
        return ("电源管理 → 节能已关闭", ok, msg)

    @staticmethod
    def apply_settings(name):
        """只写入注册表设置，不重启适配器。返回 [(label, ok, message), ...]"""
        log = []
        settings = NetMgr.get_settings(name)
        props = settings.get("AdvancedProperties", {}) if settings else {}

        for sd in SETTING_DEFS:
            if sd["type"] == "extreme":
                log.append(NetMgr._apply_extreme_setting(name, sd, props))
            elif sd["type"] == "power":
                log.append(NetMgr._apply_power_setting(name, sd, props))
            else:
                log.append(NetMgr._apply_int_setting(name, sd, props))
        return log


# ──────────────────────────────────────────────
# IP 设置对话框
# ──────────────────────────────────────────────
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
        lf = ttk.LabelFrame(top_frame, text=" 适配器列表 ", padding=2)
        lf.pack(fill=tk.BOTH, expand=True)

        cols = ("sel", "name", "desc", "speed", "status", "mac", "type")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=6)
        heads = {"sel": "选", "name": "名称", "desc": "描述",
                 "speed": "速率", "status": "状态", "mac": "MAC 地址", "type": "类型"}
        widths = {"sel": 36, "name": 110, "desc": 240, "speed": 80,
                  "status": 70, "mac": 130, "type": 56}
        for col in cols:
            self.tree.heading(col, text=heads[col])
            self.tree.column(col, width=widths[col],
                           anchor="center" if col in ("sel", "speed", "status", "type") else "w")
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

        ttk.Button(bf, text="↻ 刷新", style="Small.TButton",
                   command=self.refresh).pack(side=tk.LEFT, padx=(0, 4))
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

        ttk.Separator(bf, orient=tk.VERTICAL).pack(side=tk.RIGHT, fill=tk.Y, padx=8)
        ttk.Button(bf, text="全选", style="Small.TButton",
                   command=self.sel_all).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bf, text="全不选", style="Small.TButton",
                   command=self.sel_none).pack(side=tk.RIGHT, padx=2)

        pw.add(top_frame, weight=0)

        # ── Middle: Result Area ──
        mid_frame = ttk.Frame(pw)
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

        pw.add(mid_frame, weight=1)

        # ── Bottom: Log Area ──
        bot_frame = ttk.Frame(pw)
        lgf = ttk.LabelFrame(bot_frame, text=" 日志 ", padding=2)
        lgf.pack(fill=tk.BOTH, expand=True)

        self.tlog = tk.Text(lgf, height=6, wrap=tk.WORD, font=("Consolas", 9),
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

        pw.add(bot_frame, weight=0)

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
            self._wrt("未检测到网络适配器。\n")
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

            # 过滤掉信息性条目（diag_、pnp_ 等）
            check_items = [it for it in items if not it[1].startswith(("diag_", "pnp_"))]
            total = len(check_items)
            ok_count = sum(1 for _, _, _, _, _, ok in check_items if ok)

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
                            all_results.append(future.result())
                        except Exception as e:
                            name = future_map[future]
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
                        results.append((name, msg, ok))
                    except Exception as e:
                        results.append((name, str(e), False))
                    self._log(f"{name} 重命名完成")
            self.root.after(0, self._show_rename_result, results)

        Thread(target=task, daemon=True).start()

    def _show_rename_result(self, results):
        self.tres.config(state=tk.NORMAL)
        self.tres.delete("1.0", tk.END)

        all_ok = True
        for name, msg, ok in results:
            self.tres.insert(tk.END, f"📌 {name}\n", "h")
            self.tres.insert(tk.END, f"{'═' * 80}\n", "info")
            if ok:
                self.tres.insert(tk.END, "  ✅ 重命名成功\n", "sub_ok")
            else:
                self.tres.insert(tk.END, f"  ❌ 重命名失败\n", "sub_fail")
                self.tres.insert(tk.END, f"     原因: {msg}\n", "warn")
                all_ok = False
            self.tres.insert(tk.END, f"{'═' * 80}\n\n", "info")

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

    def _wrt(self, text, tag="info"):
        self.tres.config(state=tk.NORMAL)
        self.tres.insert(tk.END, text, tag)
        self.tres.config(state=tk.DISABLED)

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.root.after(0, lambda t=ts, m=msg: self._log_do(t, m))
    def _wrt(self, text, tag="info"):
        self.tres.config(state=tk.NORMAL)
        self.tres.insert(tk.END, text, tag)
        self.tres.config(state=tk.DISABLED)

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.root.after(0, lambda t=ts, m=msg: self._log_do(t, m))

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
                import os
                icon_path = os.path.join(sys._MEIPASS, "icon.ico")
                if os.path.exists(icon_path):
                    root.iconbitmap(icon_path)
        except Exception:
            pass

        app = App(root)
        root.mainloop()
    except Exception as e:
        messagebox.showerror("启动错误", f"程序初始化失败:\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
