"""
GigE Vision 相机发现模块
通过海康 MVS SDK 枚举 GigE 相机，修改相机 IP
"""

import os
import ctypes
import struct
import ipaddress
import socket
import json

from .net_mgr import NetMgr, run_ps, is_admin, APP_NAME

class GigEVisionScanner:
    """通过海康 MVS SDK 枚举相机"""

    @staticmethod
    def _find_mvs_dll():
        """查找 MvCameraControl.dll"""
        for p in [
            r'C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64\MvCameraControl.dll',
            r'C:\Program Files (x86)\MVS\Development\Libraries\win64\MvCameraControl.dll',
        ]:
            if os.path.isfile(p):
                return p
        return None

    @staticmethod
    def _parse_device_ptr(ptr):
        """从 MV_CC_DEVICE_INFO 指针解析相机信息"""
        def _u32(off):
            return ctypes.c_uint.from_address(ptr + off).value
        mac_low = _u32(8)
        mac_high = _u32(4)
        tlayer = _u32(12)
        ip = '0.0.0.0'
        subnet = '0.0.0.0'
        if tlayer == 1:  # GigE
            r = _u32(40)
            ip = f"{(r >> 24) & 0xFF}.{(r >> 16) & 0xFF}.{(r >> 8) & 0xFF}.{r & 0xFF}"
            r = _u32(44)
            subnet = f"{(r >> 24) & 0xFF}.{(r >> 16) & 0xFF}.{(r >> 8) & 0xFF}.{r & 0xFF}"
        mb = struct.pack('<II', mac_low, mac_high)[:6]
        mac = ':'.join(f'{b:02x}' for b in mb)
        return {'ip': ip, 'mac': mac, 'subnet': subnet, '_dev_ptr': ptr}

    @staticmethod
    def discover():
        """MVS SDK 枚举相机（已验证可用的 MV_CC_EnumDevices）"""
        dll_path = GigEVisionScanner._find_mvs_dll()
        if not dll_path:
            return [], {'error': 'MVS SDK 未找到'}
        try:
            dll = ctypes.CDLL(dll_path)
            class _DL(ctypes.Structure):
                _fields_ = [('nDeviceNum', ctypes.c_uint),
                            ('pDeviceInfo', ctypes.c_void_p * 256)]
            dl = _DL()
            ret = dll.MV_CC_EnumDevices(3, ctypes.byref(dl))
            if ret != 0:
                return [], {'error': f'枚举失败 ret={ret}'}
            cameras = []
            for i in range(dl.nDeviceNum):
                ptr = dl.pDeviceInfo[i]
                if ptr:
                    cameras.append(GigEVisionScanner._parse_device_ptr(ptr))
            return cameras, {}
        except Exception as e:
            return [], {'error': str(e)}


    @staticmethod
    def map_to_adapters(cameras, adapter_ips):
        """
        将相机匹配到适配器：
        1. 优先用 SDK 返回的 adapter_ip 匹配
        2. 子网匹配兜底
        """
        mapped = {n: [] for n in adapter_ips}
        mapped['__unmatched__'] = []

        # 建反向索引: IP → 适配器名
        ip_to_name = {}
        for name, ips in adapter_ips.items():
            for aip, _ in ips:
                ip_to_name[aip] = name

        for cam in cameras:
            matched = False
            # 方案 A: SDK 明确告知了网卡 IP
            aip = cam.get('adapter_ip')
            if aip and aip in ip_to_name:
                mapped[ip_to_name[aip]].append(cam)
                matched = True

            # 方案 B: 子网匹配
            if not matched:
                cam_ip = ipaddress.IPv4Address(cam['ip'])
                for name, ips in adapter_ips.items():
                    for aip, apre in ips:
                        try:
                            net = ipaddress.IPv4Network(f"{aip}/{apre}", strict=False)
                            if cam_ip in net:
                                mapped[name].append(cam)
                                matched = True
                                break
                        except ValueError:
                            continue
                    if matched:
                        break

            if not matched:
                mapped['__unmatched__'].append(cam)
        return mapped

