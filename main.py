#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Date: 2025-10-23
Function: Main script for DouyinLiveRecorder
"""

import os
import sys
import subprocess
import time
import socket
import threading
import datetime

# ========== 日志文件重定向：打包后没有黑窗口，把输出写入日志文件，过滤重复监控信息 ==========
class LogFilter:
    """过滤掉重复的监控信息，只记录关键信息到日志文件"""
    # 需要过滤的关键词（每5秒刷一次的监控信息）
    FILTER_KEYWORDS = [
        '共监测', '正在录制', '当前时间', '瞬时错误数',
        '同一时间访问网络的线程数', '录制视频质量', '录制视频格式',
        '录制分段开启', '是否生成时间文件', '没有正在录制的直播',
        '没有正在监测和录制的直播', 'xxxxxxxxxx', '新增地址',
        '传入地址', '循环监测间隔时间', '动态改为',
    ]

    def __init__(self, original_stream, log_file):
        self.original = original_stream
        self.log_file = log_file

    def write(self, text):
        # 处理 bytes 类型输入（Flask/click 可能输出 bytes）
        if isinstance(text, bytes):
            try:
                text = text.decode('utf-8', errors='ignore')
            except Exception:
                text = str(text)
        # 同时输出到原始控制台（开发环境用）
        try:
            self.original.write(text)
        except Exception:
            pass
        # 过滤掉重复监控信息，只记录关键信息到日志文件
        if text and text.strip():
            should_filter = False
            for keyword in self.FILTER_KEYWORDS:
                if keyword in text:
                    should_filter = True
                    break
            if not should_filter:
                try:
                    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    self.log_file.write(f'[{timestamp}] {text.rstrip()}\n')
                    self.log_file.flush()
                except Exception:
                    pass

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass
        try:
            self.log_file.flush()
        except Exception:
            pass


# 初始化日志文件
script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]
logs_dir = os.path.join(script_path, 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)
log_filename = datetime.datetime.now().strftime('recorder_%Y%m%d.log')
log_filepath = os.path.join(logs_dir, log_filename)
log_file = open(log_filepath, 'a', encoding='utf-8')

# 重定向 stdout 和 stderr
sys.stdout = LogFilter(sys.stdout, log_file)
sys.stderr = LogFilter(sys.stderr, log_file)

print(f'程序启动，日志文件: {log_filepath}')
# ====================================================================================

# 检查是否在打包环境中运行
def is_pyinstaller():
    return getattr(sys, 'frozen', False)

# 检查端口是否被占用
def check_port(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()
    return result == 0

# 释放端口
def release_port(port):
    if check_port(port):
        if os.name == 'nt':
            # Windows系统
            result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if f':{port}' in line:
                    try:
                        pid = line.split()[-1]
                        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True, text=True)
                    except Exception as e:
                        pass

# 启动Flask应用
def start_flask():
    # 检查并释放端口5000
    release_port(5000)

    # 检查templates目录是否存在
    templates_dir = os.path.join(script_path, 'templates')
    if not os.path.exists(templates_dir):
        os.makedirs(templates_dir)

    # 统一在线程中运行 Flask，开发环境和打包环境一致
    import app
    threading.Thread(target=app.app.run, kwargs={'host': '127.0.0.1', 'port': 5000}).start()

    # 等待Flask应用启动
    for i in range(15):
        time.sleep(1)
        if check_port(5000):
            break

if __name__ == '__main__':
    start_flask()

    # 用 pywebview 创建桌面窗口，嵌入 Flask 页面，不打开外部浏览器
    import webview
    window = webview.create_window(
        '抖音直播录制工具',
        'http://127.0.0.1:5000',
        width=900,
        height=700,
        min_size=(800, 600),
        text_select=True
    )
    webview.start(debug=False)

    # 窗口关闭后，杀掉 ffmpeg 进程
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'ffmpeg.exe'], capture_output=True, text=True)
    except Exception:
        pass

    # 关闭日志文件
    try:
        log_file.close()
    except Exception:
        pass
