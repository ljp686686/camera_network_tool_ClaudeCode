"""PyInstaller 入口文件（显式导入所有子模块强制打包）"""
import camera_network_tool.net_mgr
import camera_network_tool.camera_scanner
import camera_network_tool.dialogs
import camera_network_tool.ui
from camera_network_tool import main
main()
