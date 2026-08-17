"""
独立下载器 - 用于在低内存状态下下载应用
通过 loader.get_args() 获取下载参数
"""

import os
import time
import machine
import network
import requests
import sys

# 添加路径以便导入
sys.path = ['', '/lib', '.frozen', '.frozen/lib']

from lib.hydra.config import Config
from lib.zipextractor import ZipExtractor
from lib.hydra import loader

# ===== 常量 =====
_RETRYS = 30
_MAX_WBITS = 15
_CHUNK_SIZE = 1024


# ===== 消息处理 =====
def parse_download_message(msg: str) -> tuple:
    """解析下载消息: DOWNLOADER:START:app_name:compiled"""
    if not msg:
        return (None, None, None)
    
    if not msg.startswith('DOWNLOADER:START:'):
        return (None, None, None)
    
    parts = msg.split(':')
    print(f"[DOWNLOADER] 解析消息: {parts}")
    
    if len(parts) >= 3:
        app_name = parts[2]
        compiled = parts[3] if len(parts) > 3 else 'compiled'
        return ('START', app_name, compiled == 'compiled')
    
    return (None, None, None)

def send_result(status: str, app_name: str, error: str = ""):


    if status == 'DONE':
        msg = f"DOWNLOADER_DONE:{app_name}"
    else:
        msg = f"DOWNLOADER_ERROR:{error}:{app_name}"

    print(f"[DOWNLOADER] 发送结果: {msg}")
    loader.launch_app("/launcher/getapps", msg)

# ===== WiFi 连接 =====
def connect_wifi() -> bool:
    """连接 WiFi（最小化内存占用）"""
    config = Config()
    
    if not config['wifi_ssid']:
        print("[DOWNLOADER] 错误: WiFi 未配置")
        return False
    
    print("[DOWNLOADER] 启用 WiFi...")
    nic = network.WLAN(network.STA_IF)
    
    if not nic.active():
        nic.active(True)
    
    if not nic.isconnected():
        while True:
            try:
                nic.connect(config['wifi_ssid'], config['wifi_pass'])
                print(f"[DOWNLOADER] SSID={config['wifi_ssid']}")
                break
            except OSError as e:
                print(f"[DOWNLOADER] 连接错误: {e}")
                time.sleep_ms(500)
        
        attempts = 0
        while not nic.isconnected() and attempts < _RETRYS:
            print(f"[DOWNLOADER] 连接中... {attempts+1}")
            time.sleep_ms(1000)
            attempts += 1
    
    if nic.isconnected():
        print("[DOWNLOADER] WiFi 已连接")
 
        return True
    else:
        print("[DOWNLOADER] WiFi 连接失败")
        return False

# ===== 下载功能 =====
def download_file(url: str, filename: str) -> bool:
    """下载文件到指定文件名"""
    print(f"[DOWNLOADER] 下载: {url}")
    
    try:
        response = requests.get(url, stream=True, timeout=30)
        
        if response.status_code != 200:
            print(f"[DOWNLOADER] HTTP 错误: {response.status_code}")
            response.close()
            return False
        
        # 获取文件大小
        content_length = response.headers.get('content-length')
        if content_length:
            size_kb = int(content_length) / 1024
            print(f"[DOWNLOADER] 文件大小: {size_kb:.1f} KB")
        
        # 写入文件
        with open(filename, "wb") as f:
            total = 0
            while True:
                chunk = response.raw.read(_CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
                
                # 每 10KB 打印进度
                if total % (10 * 1024) < _CHUNK_SIZE:
                    print(f"[DOWNLOADER] 已下载: {total/1024:.1f} KB")
        
        response.close()
        print(f"[DOWNLOADER] 下载完成: {total/1024:.1f} KB")
        return True
        
    except Exception as e:
        print(f"[DOWNLOADER] 下载失败: {e}")
        return False

# ===== 主下载流程 =====
def download_app(app_name: str, use_compiled: bool = True) -> bool:
    """下载并安装应用"""
    
    # 1. 连接 WiFi
    if not connect_wifi():
        send_result('ERROR', app_name, 'wifi_fail')
        return False

    
    # 2. 获取设备名称
    try:
        from lib.device import Device
        device_name = Device.name.lower()
    except:
        device_name = 'cardputer'
    
    # 3. 构建下载 URL
    compiled_path = "compiled" if use_compiled else "raw"
    zip_url = f"https://raw.githubusercontent.com/echo-lalia/MicroHydra-Apps/main/{compiled_path}/{app_name}.zip"
    
    print(f"[DOWNLOADER] 下载地址: {zip_url}")
    
    # 4. 下载 ZIP
    zip_file = "tempapp.zip"
    if not download_file(zip_url, zip_file):
        # 如果编译版本失败，尝试原始版本
        if use_compiled:
            print("[DOWNLOADER] 编译版本下载失败，尝试原始版本...")
            #raw_url = f"https://raw.githubusercontent.com/echo-lalia/MicroHydra-Apps/main/raw/{app_name}.zip"
            raw_url = f"https://github.com/echo-lalia/MicroHydra-Apps/raw/refs/heads/main/catalog-output/raw/{app_name}.zip"
            if not download_file(raw_url, zip_file):
                send_result('ERROR', app_name, 'download_fail')
                return False
        else:
            send_result('ERROR', app_name, 'download_fail')
            return False
    
    # 5. 解压
    print("[DOWNLOADER] 解压中...")
    try:
        wbits = 8
        extracted = False
        while wbits <= 15:
            try:
                ZipExtractor(zip_file).extract('apps', wbits=wbits)
                extracted = True
                print(f"[DOWNLOADER] 解压成功 (wbits={wbits})")
                break
            except Exception as e:
                print(f"[DOWNLOADER] 解压尝试 {wbits} 失败: {e}")
                wbits += 1

        
        if not extracted:
            print("[DOWNLOADER] 解压失败")
            send_result('ERROR', app_name, 'extract_fail')
            return False
        
        # 6. 清理
        os.remove(zip_file)
        print("[DOWNLOADER] 清理完成")
        
        # 7. 标记完成
        send_result('DONE', app_name)
        print(f"[DOWNLOADER] 下载完成: {app_name}")
        return True
        
    except Exception as e:
        print(f"[DOWNLOADER] 解压错误: {e}")
        send_result('ERROR', app_name, 'extract_error')
        return False

# ===== 主入口 =====
def main():
    """下载器主入口"""
    print("[DOWNLOADER] ===== 启动 =====")


    msg = loader.get_args()[0]
    print(f"[DOWNLOADER] loader.get_args(): {msg}")
    
    # args 格式: ['hydra.downloader'](普通启动)
    

    if msg:
        print(f"[DOWNLOADER] 下载消息: {msg}")
        
        command, app_name, use_compiled = parse_download_message(msg)
        
        if command == 'START' and app_name:
            print(f"[DOWNLOADER] 开始下载: {app_name}, compiled={use_compiled}")
            
            # 执行下载
            download_app(app_name, use_compiled)
            
            # 清理
            try:
                network.WLAN(network.STA_IF).active(False)
            except:
                pass

    
    # 没有有效的下载消息
    send_result('ERROR', "NA", 'No request')

# 直接运行
main()
