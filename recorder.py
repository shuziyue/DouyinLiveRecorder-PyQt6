#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
录制程序入口 - 打包成 recorder.exe
主程序调用这个 exe 来运行 douyin_only.py
"""
import os
import sys

# 获取脚本所在目录
if getattr(sys, 'frozen', False):
    # 打包环境：douyin_only.py 在 _internal 目录（sys._MEIPASS）
    script_path = sys._MEIPASS
else:
    # 开发环境
    script_path = os.path.dirname(os.path.abspath(__file__))

douyin_only_path = os.path.join(script_path, 'douyin_only.py')

# 读取并执行 douyin_only.py（用 utf-8-sig 去掉 BOM 头）
with open(douyin_only_path, 'r', encoding='utf-8-sig') as f:
    code = f.read()

exec(code, {'__name__': '__main__', '__file__': douyin_only_path})
