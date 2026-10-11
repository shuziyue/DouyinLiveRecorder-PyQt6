# -*- coding: utf-8 -*-

import os
import sys
from loguru import logger

logger.remove()

custom_format = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> - <level>{message}</level>"

# 确保sys.stderr不为None
sink = sys.stderr if sys.stderr is not None else open(os.devnull, 'w')

# 控制台输出（开发环境可见，打包后 console=False 自动隐藏）
logger.add(
    sink=sink,
    format=custom_format,
    level="INFO",
    colorize=True,
    enqueue=True
)

script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]

# 确保 logs 目录存在
os.makedirs(f"{script_path}/logs", exist_ok=True)

# 主日志文件：记录所有 INFO 及以上级别日志（包括录制状态、错误等）
logger.add(
    f"{script_path}/logs/recorder.log",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
    serialize=False,
    enqueue=True,
    retention="7 days",      # 保留7天
    rotation="50 MB",        # 单个文件最大50MB
    encoding='utf-8'
)

# 详细调试日志：记录 DEBUG 级别日志（仅排查问题时用）
logger.add(
    f"{script_path}/logs/debug.log",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    serialize=False,
    enqueue=True,
    retention="3 days",       # 保留3天
    rotation="20 MB",         # 单个文件最大20MB
    encoding='utf-8'
)

# 错误日志：专门记录 ERROR 和 CRITICAL 级别
logger.add(
    f"{script_path}/logs/error.log",
    level="ERROR",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    serialize=False,
    enqueue=True,
    retention="30 days",      # 错误日志保留30天
    rotation="10 MB",
    encoding='utf-8'
)

# 全局异常捕获：未捕获的异常也写入日志
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical("未捕获的异常")

sys.excepthook = handle_exception

# 导出 logger 供其他模块使用
__all__ = ['logger']
