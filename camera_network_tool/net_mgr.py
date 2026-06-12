"""
网络适配器管理模块
包含 PowerShell 调用工具函数、验证函数和 NetMgr 业务逻辑类
"""

import sys
import os
import subprocess
import json
import re
import ctypes

# ── 常量 ──
APP_NAME = "相机网络配置工具"
APP_VERSION = "v2.6"

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

INTERFACE_TYPE_ETHERNET = 6


# ── 工具函数 ──

def is_admin():
    """检查是否以管理员身份运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate():
    """以管理员身份重新启动（支持 .exe 和 .py 两种模式）"""
    if is_admin():
        return
    frozen = getattr(sys, 'frozen', False)
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
        startupinfo.wShowWindow = 0
        proc = subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command",
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out_bytes, err_bytes = proc.communicate(timeout=timeout)
        out = out_bytes.decode("utf-8", errors="replace").strip()
        err = err_bytes.decode("utf-8", errors="replace").strip()
        # 去除可能的 BOM
        out = out.lstrip("﻿").strip()
        err = err.lstrip("﻿").strip()
        return out, err, proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return "", "执行超时", -1
    except Exception as e:
        return "", str(e), -1


def ps_escape_sq(value):
    """转义字符串，安全插入 PowerShell 单引号字符串 ('...') 中"""
    return str(value).replace("'", "''")


def to_int(v, default=None):
    """安全转为整数，处理 PowerShell JSON 可能的数组包装"""
    try:
        if isinstance(v, list):
            v = v[0]
        return int(v)
    except (ValueError, TypeError, IndexError):
        return default


def psv(prop_dict):
    """
    安全提取 Get-NetAdapterAdvancedProperty 的 RegistryValue
    PowerShell 返回的 JSON 中 RegistryValue 可能是标量或数组
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
    # 删除旧 IP 地址（兼容 DHCP 和静态模式）
    Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $name -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    # 前缀长度 → 子网掩码（用字节数组避免小端序反转）
    $mb = [byte[]]@(0,0,0,0); $n = $prefix
    for ($i = 0; $i -lt 4 -and $n -gt 0; $i++) {
        if ($n -ge 8) { $mb[$i] = 255; $n -= 8 }
        else { $mb[$i] = (0xFF -shl (8 - $n)) -band 0xFF; $n = 0 }
    }
    $mask = [System.Net.IPAddress]$mb
    # netsh 设置静态 IP（自动处理 DHCP→静态模式转换）
    if ($gateway) {
        $r = netsh interface ip set address name="$name" source=static addr=$ip mask=$($mask.IPAddressToString) gateway=$gateway 2>&1
    } else {
        $r = netsh interface ip set address name="$name" source=static addr=$ip mask=$($mask.IPAddressToString) gateway=none 2>&1
    }
    if ($LASTEXITCODE) { throw "netsh 失败: $r" }
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
# IPv4 — 排除 APIPA（169.254.x.x 自动地址）取第一个有效 IP
$ip = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias '__NAME__' -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '169.254.*' }
if ($ip) { $first = $ip | Select-Object -First 1; $r['IPv4Address'] = $first.IPAddress; $r['PrefixLength'] = $first.PrefixLength }
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

    @staticmethod
    def get_adapter_ips():
        """获取所有适配器的 IPv4 地址，返回 {名称: [(ip, 前缀), ...]}"""
        out, _, _ = run_ps(r"""
$r = @{}
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object {
    $n = $_.InterfaceAlias
    if (-not $r[$n]) { $r[$n] = @() }
    $r[$n] += @{ip = $_.IPAddress; prefix = $_.PrefixLength }
}
ConvertTo-Json -Compress $r
""")
        if not out or out == '{}':
            return {}
        try:
            data = json.loads(out)
            return {n: [(i['ip'], i['prefix']) for i in ips] for n, ips in data.items()}
        except (json.JSONDecodeError, TypeError, KeyError):
            return {}


# ──────────────────────────────────────────────
# GigE Vision 相机发现
# ──────────────────────────────────────────────
