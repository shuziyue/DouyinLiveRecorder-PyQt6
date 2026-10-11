# -*- encoding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Date: 2023-07-17 23:52:05
Update: 2025-10-23 19:48:05
Copyright (c) 2023-2025 by Hmily, All Rights Reserved.
Function: Record Douyin live stream video.
"""
# 导入所需模块
import asyncio  # 用于异步操作
import builtins
import json  # 用于输出运行状态（供桌面端状态看板读取）
import os
import sys
import subprocess  # 用于启动ffmpeg进程
import threading  # 用于多线程操作
import time
import datetime  # 用于处理时间相关操作
import re  # 用于正则表达式操作
import shutil  # 用于文件操作
import signal  # 用于信号处理
from pathlib import Path
import configparser  # 用于读取配置文件
import random  # 用于生成随机数

import httpx  # 用于HTTP请求
import urllib.request
from urllib.error import URLError, HTTPError

from typing_extensions import Any

from src import spider, stream  # 用于获取直播流
from src.utils import logger  # 日志模块
from src import utils  # 工具函数

# 被 GUI 用管道读取时（不是真正的控制台）：
# 1) 输出改成 UTF-8，避免 emoji 被 GBK 编码成 \u2705 这种转义残留；
# 2) 去掉带颜色的控制台 sink，避免界面日志里出现 [0m / [32m 这类 ANSI 码，
#    也避免同一条消息被 loguru 和 print 各写一遍（界面看起来像重复刷屏）。
# 文件日志（logs\recorder.log 等）不受影响，内容依旧完整。
try:
    if not getattr(sys.stdout, 'isatty', lambda: False)():
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
        try:
            logger.remove(0)   # 0 号 sink = src/logger.py 加的控制台 sink
        except Exception:
            pass
except Exception:
    pass
from ffmpeg_install import (
    check_ffmpeg, ffmpeg_path, current_env_path  # ffmpeg相关
)

# ========== 日志重定向：把所有 print 输出同时写入日志文件 ==========
class TeeOutput:
    """同时输出到控制台和日志文件的 Tee 类（线程安全）"""
    def __init__(self, original_stream, log_file):
        self.original = original_stream
        self.log_file = log_file
        self._isatty = getattr(original_stream, 'isatty', lambda: False)()
        self._lock = threading.Lock()  # 线程锁

    def write(self, text):
        if not text or text == '\n':
            return
        with self._lock:  # 加锁，避免多线程写入交错
            # 写入原始流（控制台）
            try:
                if self.original:
                    self.original.write(text)
                    self.original.flush()
            except Exception:
                pass
            # 写入日志文件
            try:
                timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                line = f"[{timestamp}] {text.rstrip()}\n"
                self.log_file.write(line)
                self.log_file.flush()
            except Exception:
                pass

    def flush(self):
        with self._lock:
            try:
                if self.original:
                    self.original.flush()
            except Exception:
                pass
            try:
                self.log_file.flush()
            except Exception:
                pass

    def isatty(self):
        return self._isatty

    @property
    def encoding(self):
        return getattr(self.original, 'encoding', 'utf-8')

# 初始化日志重定向
_script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]
_log_dir = os.path.join(_script_path, 'logs')
os.makedirs(_log_dir, exist_ok=True)
_log_file_path = os.path.join(_log_dir, f'recorder_console_{datetime.datetime.now().strftime("%Y%m%d")}.log')
_log_file = open(_log_file_path, 'a', encoding='utf-8', buffering=1)

# 重定向 stdout 和 stderr
sys.stdout = TeeOutput(sys.stdout, _log_file)
sys.stderr = TeeOutput(sys.stderr, _log_file)

logger.info(f"程序启动完成")
logger.info(f"工作目录: {_script_path}")
logger.info(f"控制台日志文件: {_log_file_path}")

# ========== 版本信息 ==========
version = "v4.0.7"
platforms = "抖音"

# 全局变量
recording = set()  # 存储正在录制的直播
error_count = 0  # 错误计数
pre_max_request = 10  # 最大请求数
max_request_lock = threading.Lock()  # 请求数锁
error_window = []  # 错误窗口
error_window_size = 10  # 错误窗口大小
error_threshold = 5  # 错误阈值
monitoring = 0  # 正在监测的直播数
running_list = []  # 运行列表
url_tuples_list = []  # URL元组列表
# 同一个直播间挂了哪些画质：{地址: {画质, ...}}
# 用来判断“同一直播间同时录多个档位”的情况（这时文件名要带上画质，避免同秒撞名）
url_quality_map = {}
# 已经提示过“同一直播间挂了多个画质”的组合，避免每次重读配置都刷一行日志
multi_quality_logged = set()
url_comments = []  # 注释的URL
text_no_repeat_url = []  # 不重复的URL
create_var = locals()  # 本地变量字典
first_start = True  # 是否首次启动
exit_recording = False  # 是否退出录制
need_update_line_list = []  # 需要更新的行列表
first_run = True  # 是否首次运行
not_record_list = []  # 不录制的列表
start_display_time = datetime.datetime.now()  # 开始显示时间
recording_time_list = {}
# ===== 正在跑的录制进程计数 =====
# 收到退出请求时，录制线程会先从 recording 集合里摘掉自己，再去登记“收尾任务
# （修时间戳 / 转 mp4）”，中间有一个空档；主循环如果正好在这个空档里做判断，
# 就会直接退出，留下一堆没转 mp4 的 .ts（端到端测试里实测到过）。
# 这里用一个计数器把整段录制过程罩住，主循环必须等它归零才允许退出。
active_record_runs = 0
active_record_lock = threading.Lock()


def mark_record_run(delta: int) -> None:
    global active_record_runs
    with active_record_lock:
        active_record_runs = max(0, active_record_runs + delta)


def has_active_record_runs() -> bool:
    with active_record_lock:
        return active_record_runs > 0
last_ffmpeg_return_code = 0  # 记录最后一次ffmpeg退出码，用于判断是否异常退出  # 录制时间列表
last_live_status = {}  # 记录每个主播的上次直播状态（True=正在直播，False=未开播）
applied_save_path = None  # 记录已生效的保存路径，用于避免重复打印
last_live_status_lock = threading.Lock()  # 线程锁

# ===== 收尾（修复时间戳 / 转 mp4）任务登记 =====
# 作用一：同一份分段只允许一个执行者转码 —— 否则“分段循环收尾”和 check_subprocess 里的
#         老逻辑会同时写同一个 mp4，写出半截文件（时长忽长忽短、播放卡住）。
# 作用二：停止录制时能等到收尾真的做完再退出，不会留下“一半 ts 一半 mp4”。
post_task_lock = threading.Lock()
pending_post_tasks = set()   # 正在收尾的任务标识
claimed_outputs = set()      # 已被认领的输出文件（一次转码只做一次）
# 收尾任务序号：key 必须唯一。
# 以前用 time.time() 拼 key，Windows 上 time.time() 只有约 15ms 精度，
# 停止录制时“退出分支”和“分段循环”几乎同时登记，两条任务会撞成同一个 key；
# 先结束的那条 discard 掉 key 之后，还在跑的那条就“查无此人”了，
# 主循环据此以为收尾已完成、直接退出，留下一半没转的 .ts。
post_task_sequence = 0

# 路径设置
script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]  # 脚本路径
config_file = f'{script_path}/config/config.ini'  # 配置文件路径
url_config_file = f'{script_path}/config/URL_config.ini'  # URL配置文件路径
backup_dir = f'{script_path}/backup_config'  # 备份目录
text_encoding = 'utf-8-sig'  # 文本编码
rstr = r"[\/\\:\*\？?\"\<\>\|&#.。,， ~！· ]"  # 文件名非法字符

# ========== 目录可用性工具 ==========
# 注意：Windows 上 os.access(path, os.W_OK) 只检查文件/目录的只读属性，不检查 ACL，
# 对「盘根存在但当前用户无写权限」这类情况会误判为可写。所以这里一律用真实写入探测。
_save_path_probe_cache = {'path': None, 'ok': False, 'err': '', 'ts': 0.0}


def is_drive_root(path: str) -> bool:
    r"""判断路径是否为盘根（如 F:\ 、C:\）。
    只认「盘符 + 冒号 + 斜杠」这种形式；UNC 网络共享（\\server\share）不算盘根。
    """
    try:
        drive, tail = os.path.splitdrive(os.path.abspath(path))
    except Exception:
        return False
    return len(drive) == 2 and drive.endswith(':') and tail.strip('\\/') == ''


def probe_dir_writable(path: str):
    """真实写入探测：确保目录存在且真的能写入文件，返回 (是否可用, 错误描述)"""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as err:
        return False, f'无法创建目录（{type(err).__name__}: {err}）'
    if not os.path.isdir(path):
        return False, '目标路径已存在但不是文件夹'
    # 探测文件名必须线程间唯一：同一直播间挂两条画质时，两个录制线程会同时探测
    # 同一个目录，只精确到毫秒的话会撞名——一个线程把另一个的探测文件删掉，
    # 对方就拿到 FileNotFoundError，被误判成“目录没有写入权限”。
    probe_file = os.path.join(
        path,
        f'.__save_probe_{os.getpid()}_{threading.get_ident()}_{time.time_ns()}')
    try:
        with open(probe_file, 'wb') as fp:
            fp.write(b'1')
        os.remove(probe_file)
        return True, ''
    except Exception as err:
        try:
            if os.path.exists(probe_file):
                os.remove(probe_file)
        except Exception:
            pass
        return False, f'目录没有写入权限（{type(err).__name__}: {err}）'


def check_save_path(path: str, retry_interval: float = 300.0):
    """带缓存的目录可用性检查。
    可用的结果长期缓存（路径不变就不重复写盘）；不可用的结果 5 分钟后重试，
    这样既不会每轮轮询都写一次磁盘，也不会在盘符重新挂载后一直判定为不可用。
    """
    now = time.time()
    cache = _save_path_probe_cache
    if cache['path'] == path and (cache['ok'] or now - cache['ts'] < retry_interval):
        return cache['ok'], cache['err']
    ok, err = probe_dir_writable(path)
    cache['path'] = path
    cache['ok'] = ok
    cache['err'] = err
    cache['ts'] = now
    return ok, err


def ensure_record_dir(target_dir: str, fallback_dir: str = ''):
    """确保录制目录可用，不可用时回退到备用目录。
    返回 (最终可用目录, 是否用了备用目录, 错误描述)；两者都不可用时目录为空字符串。
    """
    ok, err = probe_dir_writable(target_dir)
    if ok:
        return target_dir, False, ''
    logger.warning(f'录制目录不可用({target_dir}): {err}')
    print(f'⚠️ 录制目录不可用: {target_dir}（{err}）')
    if fallback_dir and os.path.normpath(fallback_dir) != os.path.normpath(target_dir):
        ok_fb, err_fb = probe_dir_writable(fallback_dir)
        if ok_fb:
            logger.warning(f'已自动回退到备用目录: {fallback_dir}')
            print(f'⚠️ 已自动回退到备用目录: {fallback_dir}')
            return fallback_dir, True, err
        err = f'{err}；备用目录同样不可用: {err_fb}'
    return '', False, err


def build_record_dir(root_dir: str, platform_name: str, anchor: str,
                     live_time: str = '', live_title: str = '',
                     by_author: bool = True, by_time: bool = False, by_title: bool = False) -> str:
    """按「根目录 / 平台 / 主播 / 时间 / 标题」拼出录制目录。
    根目录为盘根（F:\）时同样会在其下创建子目录，不会把文件直接堆在盘根上。
    """
    path = os.path.join(root_dir, platform_name)
    if by_author and anchor:
        path = os.path.join(path, anchor)
    if by_time and live_time:
        path = os.path.join(path, live_time[:10])
    if by_title and live_title:
        if by_time:
            path = os.path.join(path, f'{live_title}_{anchor}')
        else:
            path = os.path.join(path, f'{live_time[:10]}_{live_title}')
    return path


# 默认下载路径（程序目录不可写时自动回退到用户主目录，
# 避免程序被放在 C:\Program Files 这类只读位置时直接失败）
_default_save_candidates = [
    os.path.join(script_path, 'downloads'),
    os.path.join(os.path.expanduser('~'), 'DouyinLiveRecorder', 'downloads'),
]
default_path = _default_save_candidates[0]
for _cand in _default_save_candidates:
    _cand_ok, _cand_err = probe_dir_writable(_cand)
    if _cand_ok:
        default_path = _cand
        if _cand != _default_save_candidates[0]:
            print(f"⚠️ 程序目录不可写（{_cand_err}），默认保存路径已切换到: {_cand}")
        break
    print(f"⚠️ 默认保存路径不可用（{_cand}）: {_cand_err}")
default_save_root = default_path  # 记录默认根目录，供运行中回退使用
print(f"使用默认保存路径: {default_path}")

# 停止请求文件：GUI 点「退出程序 / 停止录制」时会写这个文件。
# 录制线程看到它就把当前分段正常收尾（ffmpeg 收到 q）再退出，避免最后一段被硬杀截断；
# 下次启动时先清掉上一次的残留，避免误停。
stop_signal_file = os.path.join(script_path, 'logs', 'stop_request')
try:
    if os.path.exists(stop_signal_file):
        os.remove(stop_signal_file)
except OSError:
    pass

# 文件更新锁
file_update_lock = threading.Lock()

# 系统类型和清屏命令
os_type = os.name
clear_command = "cls" if os_type == 'nt' else "clear"

# 颜色对象
color_obj = utils.Color()


class _PlainColor:
    """非控制台输出（GUI 管道 / PyCharm）专用：只打印文字，不带 ANSI 颜色码"""
    RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = RESET = ''

    @staticmethod
    def print_colored(text, color):
        print(text)


try:
    if not getattr(sys.stdout, 'isatty', lambda: False)():
        color_obj = _PlainColor()
except Exception:
    pass

# 设置环境变量，添加ffmpeg路径
os.environ['PATH'] = ffmpeg_path + os.pathsep + current_env_path


# ========== 运行状态输出（供桌面端状态看板读取） ==========
runtime_status_lock = threading.Lock()  # 运行状态锁
runtime_meta = {}  # record_name -> {anchor, quality, is_live, save_dir, prefix}
runtime_status_file = os.path.join(script_path, 'logs', 'runtime_status.json')
media_extensions = ('.ts', '.mp4', '.mkv', '.flv', '.mp3', '.m4a')


def collect_dir_stats(save_dir: str, prefix: str, started_at: float | None = None) -> tuple:
    """统计“本次录制”产生的文件数量与大小。

    只统计：
      * 文件名以本主播前缀开头的
      * 修改时间不早于本次开录时刻（started_at）的
      * 真正的媒体文件（跳过 .broken.ts / _temp_*.ts / 日志）
    这样目录里本来就有旧视频时，看板的“分段数 / 大小”不会把旧文件算进去。
    """
    count = 0
    total_size = 0
    latest_size = 0
    if not save_dir or not os.path.isdir(save_dir):
        return count, total_size, latest_size
    min_mtime = (float(started_at) - 5.0) if started_at else None  # 允许 5 秒误差
    try:
        with os.scandir(save_dir) as entries:
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                    name = entry.name
                    lower_name = name.lower()
                    if '.broken.' in lower_name or name.startswith('_temp_'):
                        continue
                    if prefix and not name.startswith(prefix):
                        continue
                    if not lower_name.endswith(media_extensions):
                        continue
                    stat = entry.stat()
                    if min_mtime is not None and stat.st_mtime < min_mtime:
                        continue
                    size = stat.st_size
                    count += 1
                    total_size += size
                    if size > latest_size:
                        latest_size = size
                except OSError:
                    continue
    except OSError:
        pass
    return count, total_size, latest_size


def write_runtime_status() -> None:
    """周期性写出运行状态 JSON，桌面端读取它绘制状态看板"""
    while True:
        try:
            with runtime_status_lock:
                meta_snapshot = {key: dict(value) for key, value in runtime_meta.items()}
            with stream_health_state_lock:
                health_snapshot = {key: dict(value) for key, value in stream_health_state.items()}

            now = datetime.datetime.now()
            items = []
            for record_name, meta in meta_snapshot.items():
                is_recording = record_name in recording
                elapsed_seconds = 0
                if is_recording and record_name in recording_time_list:
                    try:
                        started_at = recording_time_list[record_name][0]
                        elapsed_seconds = int((now - started_at).total_seconds())
                    except Exception:
                        elapsed_seconds = 0

                segment_count, total_size, latest_size = collect_dir_stats(
                    meta.get('save_dir', ''), meta.get('prefix', ''), meta.get('started_at')
                )
                # 直播流健康状态：没检测过就报 unknown，桌面端会显示成"—"
                health = health_snapshot.get(record_name) or {}
                items.append({
                    'name': record_name,
                    'anchor': meta.get('anchor', record_name),
                    'quality': meta.get('quality', ''),
                    'is_live': bool(meta.get('is_live', False)),
                    'recording': is_recording,
                    'elapsed': elapsed_seconds,
                    'segments': segment_count,
                    'total_size': total_size,
                    'latest_size': latest_size,
                    'save_dir': meta.get('save_dir', ''),
                    'live_type': meta.get('live_type', ''),
                    'health': health.get('status', ''),
                    'health_detail': health.get('detail', ''),
                    'health_source': health.get('source', ''),
                    'health_bad_count': int(health.get('bad_count') or 0),
                    'health_updated': health.get('updated', 0),
                })

            # 汇总坏流数量，桌面端摘要行直接用
            bad_streams = sum(1 for it in items if it.get('health') == HEALTH_BAD)
            switched_streams = sum(1 for it in items if it.get('health') == HEALTH_SWITCHED)

            payload = {
                'ts': time.time(),
                'time': now.strftime('%Y-%m-%d %H:%M:%S'),
                'pid': os.getpid(),
                'save_path': default_path,
                'bad_streams': bad_streams,
                'switched_streams': switched_streams,
                'items': items,
            }
            tmp_file = runtime_status_file + '.tmp'
            with open(tmp_file, 'w', encoding='utf-8') as status_fh:
                json.dump(payload, status_fh, ensure_ascii=False, indent=1)
            os.replace(tmp_file, runtime_status_file)
        except Exception:
            pass
        time.sleep(2)


def signal_handler(_signal, _frame):
    sys.exit(0)


try:
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, signal_handler)
except (ValueError, RuntimeError):
    pass


def display_info() -> None:
    """状态摘要：默认每 30 秒一行（原来 5 秒打一整屏，会把控制台和日志刷爆）"""
    global start_display_time
    display_interval = 30      # 摘要间隔（秒）
    first_display = True       # 第一次打一块完整状态，后面只打紧凑摘要
    time.sleep(5)
    while True:
        try:
            if sys.stdout is not None:
                sys.stdout.flush()
            time.sleep(display_interval)
            # 打包环境(console=False)或pythonw.exe下跳过清屏，避免os.system启动cmd.exe导致窗口闪烁
            if Path(sys.executable).name != 'pythonw.exe' and not getattr(sys, 'frozen', False):
                # 只在真正的控制台里清屏；PyCharm/重定向输出时 isatty() 为假，
                # 这时 os.system("cls") 会去启动一次 cmd.exe（闪窗、pycharm 控制台也没意义）
                if os_type == 'nt' and sys.stdout.isatty():
                    os.system(clear_command)

            now_time = datetime.datetime.now()
            now = now_time.strftime('%H:%M:%S')
            # 第一次上来打一块完整状态（原来每 5 秒打一次，会把控制台和日志刷爆）
            if first_display:
                first_display = False
                print(f"共监测{monitoring}个直播中 | 同一时间访问网络的线程数: {max_request} | "
                      f"分段: {(split_time + '秒') if split_video_by_time else '否'} | "
                      f"质量: {video_record_quality} | 格式: {video_save_type}"
                      + (" | 生成时间文件: 是" if create_time_file else ""))

            # 之后每 display_interval 秒只打一行摘要（要逐秒明细看 logs\recorder.log，
            # 要看板/每个主播的状态看 GUI 状态表，它读的是 runtime_status.json）
            print(f"[{now}] 监测 {monitoring} 个 | 录制 {len(recording)} 个 | "
                  f"错误 {error_count} | 分段 {((split_time + '秒') if split_video_by_time else '关')}")
            if recording:
                for recording_live in sorted(set(recording)):
                    rt, qa = recording_time_list.get(recording_live, (now_time, ''))
                    have_record_time = str(now_time - rt).split('.')[0]
                    print(f"  录制中: {recording_live}[{qa}] {have_record_time}")
            else:
                print(f"  没有正在录制的直播（循环间隔 {delay_default} 秒）")
            start_display_time = now_time
        except Exception as e:
            logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")


def update_file(file_path: str, old_str: str, new_str: str, start_str: str = None) -> str | None:
    if old_str == new_str and start_str is None:
        return old_str
    with file_update_lock:
        file_data = []
        with open(file_path, "r", encoding=text_encoding) as f:
            try:
                for text_line in f:
                    if old_str in text_line:
                        text_line = text_line.replace(old_str, new_str)
                        if start_str:
                            text_line = f'{start_str}{text_line}'
                    if text_line not in file_data:
                        file_data.append(text_line)
            except RuntimeError as e:
                logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                if ini_URL_content:
                    with open(file_path, "w", encoding=text_encoding) as f2:
                        f2.write(ini_URL_content)
                    return old_str
        if file_data:
            with open(file_path, "w", encoding=text_encoding) as f:
                f.write(''.join(file_data))
        return new_str


def delete_line(file_path: str, del_line: str, delete_all: bool = False) -> None:
    with file_update_lock:
        with open(file_path, 'r+', encoding=text_encoding) as f:
            lines = f.readlines()
            f.seek(0)
            f.truncate()
            skip_line = False
            for txt_line in lines:
                if del_line in txt_line:
                    if delete_all or not skip_line:
                        skip_line = True
                        continue
                else:
                    skip_line = False
                f.write(txt_line)


def get_startup_info(system_type: str):
    if system_type == 'nt':
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    else:
        startup_info = None
    return startup_info


def segment_video(converts_file_path: str, segment_save_file_path: str, segment_format: str, segment_time: str,
                  is_original_delete: bool = True) -> None:
    try:
        if os.path.exists(converts_file_path) and os.path.getsize(converts_file_path) > 0:
            ffmpeg_command = [
                "ffmpeg",
                "-i", converts_file_path,
                "-c:v", "copy",
                "-c:a", "copy",
                "-map", "0",
                "-f", "segment",
                "-segment_time", segment_time,
                "-segment_format", segment_format,
                "-reset_timestamps", "1",
                "-movflags", "+frag_keyframe+empty_moov",
                segment_save_file_path,
            ]
            _output = subprocess.check_output(
                ffmpeg_command, stderr=subprocess.STDOUT, startupinfo=get_startup_info(os_type)
            )
            if is_original_delete:
                time.sleep(1)
                if os.path.exists(converts_file_path):
                    os.remove(converts_file_path)
    except subprocess.CalledProcessError as e:
        logger.error(f'Error occurred during conversion: {e}')
    except Exception as e:
        logger.error(f'An unknown error occurred: {e}')


def converts_mp4(converts_file_path: str, is_original_delete: bool = True) -> None:
    # 认领输出文件：分段循环的收尾和 check_subprocess 里的老逻辑可能同时处理同一个分段，
    # 两个 ffmpeg 同时写同一个 mp4 会写出半截文件（时长忽长忽短、播放卡住）。
    mp4_target = converts_file_path.rsplit('.', maxsplit=1)[0] + '.mp4'
    claim_key = os.path.normcase(os.path.abspath(mp4_target))
    with post_task_lock:
        if claim_key in claimed_outputs:
            logger.debug(f"跳过重复转码（同一文件已在处理）: {os.path.basename(converts_file_path)}")
            return
        claimed_outputs.add(claim_key)
    try:
        if os.path.exists(converts_file_path) and os.path.getsize(converts_file_path) > 0:
            # 坏段不转码：里面没有一帧有效画面，转出来只会是 0 秒的垃圾 mp4，
            # 还会刷一屏 ffmpeg 报错。这里直接把原文件改名隔离，保留证据。
            if is_media_empty(converts_file_path):
                broken_path = quarantine_file(converts_file_path)
                message = (f"跳过转码：{os.path.basename(converts_file_path)} 里没有有效画面"
                           f"（时长 0 秒），已隔离为 {os.path.basename(broken_path)}")
                logger.warning(message)
                print(message)
                return
            if converts_to_h264:
                color_obj.print_colored("正在转码为MP4格式并重新编码为h264\n", color_obj.YELLOW)
                ffmpeg_command = [
                    "ffmpeg", "-y", "-i", converts_file_path,
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "23",
                    "-vf", "format=yuv420p",
                    "-c:a", "copy",
                    "-f", "mp4", converts_file_path.rsplit('.', maxsplit=1)[0] + ".mp4",
                ]
            else:
                color_obj.print_colored("正在转码为MP4格式\n", color_obj.YELLOW)
                ffmpeg_command = [
                    "ffmpeg", "-y", "-i", converts_file_path,
                    "-c:v", "copy",
                    "-c:a", "copy",
                    "-f", "mp4", converts_file_path.rsplit('.', maxsplit=1)[0] + ".mp4",
                ]
            _output = subprocess.check_output(
                ffmpeg_command, stderr=subprocess.STDOUT, startupinfo=get_startup_info(os_type)
            )
            if is_original_delete:
                time.sleep(1)
                if os.path.exists(converts_file_path):
                    os.remove(converts_file_path)
    except subprocess.CalledProcessError as e:
        logger.error(f'Error occurred during conversion: {e}')
        with post_task_lock:      # 失败了放开认领，允许后面重试
            claimed_outputs.discard(claim_key)
    except Exception as e:
        logger.error(f'An unknown error occurred: {e}')
        with post_task_lock:
            claimed_outputs.discard(claim_key)


def get_video_duration(file_path: str) -> float:
    """用 ffprobe 读取视频文件的时长（秒）"""
    try:
        command = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            startupinfo=get_startup_info(os_type),
            encoding='utf-8',
            errors='replace'
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
        return 0.0
    except Exception:
        return 0.0


def describe_source_url(url: str) -> str:
    """从直播流地址里看出用的是哪一档清晰度（只写日志用）"""
    if not url:
        return '未知'
    text = str(url).lower()
    for key, label in (('origin', '原画 origin'), ('full_hd1', '蓝光 full_hd1'), ('uhd1', '超清 uhd1'),
                       ('hd1', '高清 hd1'), ('sd1', '标清 sd1'), ('sd2', '流畅 sd2'),
                       ('ld1', '流畅 ld1'), ('uhd', '超清 uhd'), ('/hd/', '高清 hd'),
                       ('/sd/', '标清 sd'), ('/ld/', '流畅 ld')):
        if key in text:
            return label
    # 认不出来时，带上"不带参数的地址尾部"，方便判断这次取的是哪一路流
    path = text.split('?')[0].rstrip('/')
    hint = '/'.join(path.split('/')[-2:])[-60:]
    return f'未知档位({hint})' if hint else '未知档位'


def get_video_info(file_path: str) -> str:
    """用 ffprobe 读出分辨率/方向/编码/码率，用来确认录到的是不是竖屏原画"""
    try:
        command = [
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,codec_name",
            "-show_entries", "format=bit_rate",
            "-of", "default=noprint_wrappers=1",
            file_path,
        ]
        result = subprocess.run(
            command, capture_output=True, startupinfo=get_startup_info(os_type),
            encoding='utf-8', errors='replace'
        )
        if result.returncode != 0:
            return '未知'
        info = {}
        for line in (result.stdout or '').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                info[key.strip()] = value.strip()
        parts = []
        width, height = info.get('width', ''), info.get('height', '')
        if width.isdigit() and height.isdigit() and int(width) and int(height):
            w, h = int(width), int(height)
            parts.append(f'{w}x{h}' + ('（竖屏）' if h > w else '（横屏）'))
        if info.get('codec_name'):
            parts.append(info['codec_name'])
        rate = info.get('bit_rate', '')
        if rate.isdigit() and int(rate) > 0:
            parts.append(f'{int(rate) / 1000 / 1000:.2f} Mbps')
        return '  '.join(parts) if parts else '未知'
    except Exception:
        return '未知'


# ===================== 直播流健康度：录制中体检 + 自动换线路 =====================
# 有些直播分发链路会返回"能被 ffmpeg 打开、但里面没有一帧有效画面"的数据：
# 容器识别失败后 ffmpeg 会退化成裸码流解析，把垃圾一路封进 TS，
# 结果是文件名正常、体积一直在涨，可任何播放器打开都报"编码格式不受支持"。
# 这类流自己不会报错，会一直"录"到几十分钟，所以必须主动体检。
force_hls_until = {}          # record_name -> 这个时间戳之前改用 HLS 线路
force_hls_lock = threading.Lock()
bad_stream_count = {}         # record_name -> 连续坏流次数
bad_stream_lock = threading.Lock()
pending_line_switch = set()   # record_name -> 待执行的"换线路重试"
pending_line_switch_lock = threading.Lock()
quick_retry_records = set()   # record_name -> 换线路后跳过等待、立刻重连
quick_retry_lock = threading.Lock()
live_type_checked = set()     # 已经探过“团播 / 个播”的 record_name（一场只探一次）
record_orientation = {}       # record_name -> 房间方向标记（1=竖屏 2=横屏），
                              # 用来判断“录到横屏”到底是不是异常

# 以下取值会被 config.ini 覆盖（见文件末尾读取配置处）
stream_health_enabled = True      # 是否开启录制中体检
stream_health_seconds = 60        # 文件写入满多少秒后开始体检
stream_auto_switch_line = True    # 发现无画面时是否自动换线路
stream_switch_hold_seconds = 600  # 换线路后维持多久再切回原线路
source_health_detail = True       # 收尾时是否做“源流详细体检”（抽抖音推流 SEI）

# ===== C：实时写入速率监控（零成本，只用已经拿到的文件大小） =====
# 主播端推流掉档（帧率/码率腰斩）时，本机看到的直接表现就是“文件增长速率骤降”。
# 这一项不需要任何额外 I/O：监控线程本来就在看这个分段文件的大小。
write_rate_alert_ratio = 0.5      # 低于本段峰值的这个比例就报一次
write_rate_min_bytes = 200 * 1024  # 峰值速率低于这个值就不判断（避免小流误报）


def mark_bad_stream(record_name: str) -> int:
    """记一次"取到的流没有有效画面"，并要求下一轮换线路重试"""
    with bad_stream_lock:
        count = bad_stream_count.get(record_name, 0) + 1
        bad_stream_count[record_name] = count
    if stream_auto_switch_line and record_name:
        with force_hls_lock:
            force_hls_until[record_name] = time.time() + stream_switch_hold_seconds
        with pending_line_switch_lock:
            pending_line_switch.add(record_name)
    return count


def reset_bad_stream(record_name: str) -> None:
    """确认录到了正常画面，清空坏流计数"""
    with bad_stream_lock:
        bad_stream_count.pop(record_name, None)


def should_switch_line(record_name: str) -> bool:
    """该主播当前是否处于"临时改用另一条线路"的状态"""
    if not record_name:
        return False
    with force_hls_lock:
        return bool(force_hls_until.get(record_name, 0) > time.time())


def take_line_switch_request(record_name: str) -> bool:
    """取出并清除"需要换线路重试"标记（只生效一次）"""
    with pending_line_switch_lock:
        if record_name in pending_line_switch:
            pending_line_switch.discard(record_name)
            return True
    return False


def mark_quick_retry(record_name: str) -> None:
    """换线路后不要再等一个完整循环时间，尽快重连"""
    if record_name:
        with quick_retry_lock:
            quick_retry_records.add(record_name)


def take_quick_retry(record_name: str) -> bool:
    with quick_retry_lock:
        if record_name in quick_retry_records:
            quick_retry_records.discard(record_name)
            return True
    return False


# ===== 直播流健康状态：给桌面端看板"一眼看出流坏没坏" =====
# 只保存"结论"，不保存原始数据；桌面端从 runtime_status.json 里读走直接渲染。
stream_health_state = {}          # record_name -> {status, detail, source, bad_count, updated}
stream_health_state_lock = threading.Lock()

HEALTH_UNKNOWN = 'unknown'    # 还没检测出结论（未开播、未开探测、探测拿不到信息）
HEALTH_GOOD = 'good'          # 取到的流正常，能读出画面
HEALTH_WARN = 'warn'          # 可疑：容器/编码不像正常直播流，有录成空壳的风险
HEALTH_BAD = 'bad'            # 已确认录到的是没有画面的空壳，本段已放弃
HEALTH_SWITCHED = 'switched'  # 因坏流已自动换到另一条线路（HLS）在重试


def set_stream_health(record_name: str, status: str, detail: str = '',
                      source: str = '', bad_count=None) -> None:
    """更新某主播当前的直播流健康状态（供桌面端状态看板展示）"""
    if not record_name:
        return
    with stream_health_state_lock:
        entry = stream_health_state.setdefault(record_name, {})
        entry['status'] = status
        if detail:
            entry['detail'] = detail
        if source:
            entry['source'] = source
        if bad_count is not None:
            entry['bad_count'] = bad_count
        entry['updated'] = time.time()


def get_stream_health_status(record_name: str) -> str:
    """读当前健康状态码（空串代表没记录过）"""
    if not record_name:
        return ''
    with stream_health_state_lock:
        return (stream_health_state.get(record_name) or {}).get('status', '')


def clear_stream_health(record_name: str) -> None:
    """主播下播时清掉健康状态，免得把上一场的结论显示到下一场"""
    if not record_name:
        return
    with stream_health_state_lock:
        stream_health_state.pop(record_name, None)
    # 下一场重新探一次“团播 / 个播”（同一场里形态不会变）
    live_type_checked.discard(record_name)


def is_media_empty(file_path: str, min_duration: float = 1.0) -> bool:
    """判断文件里到底有没有能解码的画面：时长≈0 就是空壳（没录到任何有效数据）"""
    try:
        if not os.path.exists(file_path) or os.path.getsize(file_path) <= 0:
            return True
        return get_video_duration(file_path) < min_duration
    except Exception:
        return False


def quarantine_file(file_path: str) -> str:
    """把坏分段改名加 .broken，既不会被当成正常分段，也不会被复查/覆盖"""
    try:
        root, ext = os.path.splitext(file_path)
        if not ext or root.endswith('.broken'):
            return file_path
        new_path = f"{root}.broken{ext}"
        os.replace(file_path, new_path)
        return new_path
    except Exception as e:
        logger.error(f"隔离坏分段失败 {file_path}: {e}")
        return file_path


def quarantine_broken_segments(files: list) -> tuple:
    """挑出没有有效画面的分段并隔离，返回 (可用分段, 已隔离分段)"""
    good, broken = [], []
    for path in files:
        if is_media_empty(path):
            broken.append(quarantine_file(path))
        else:
            good.append(path)
    return good, broken


def probe_stream(url: str, headers: str | None = None, timeout_seconds: int = 10) -> dict:
    """
    开录前先用 ffprobe 快速探一次流：这一步的价值在于把"这次到底取到了什么"
    写进日志——是正常的 flv 直播流，还是被中间层返回的错误页/裸流/压缩数据。
    探测失败不阻断录制，只记日志。
    """
    info = {'ok': False, 'format': '', 'video': '', 'size': '', 'bitrate': '', 'error': ''}
    command = [
        "ffprobe", "-v", "error",
        # 只选视频流：不加这一句时，多流（视频+音频）会互相覆盖 codec_name，
        # 日志里会出现“视频=aac”这种误导信息（流的先后顺序不同，结果还会变）。
        "-select_streams", "v:0",
        "-rw_timeout", str(max(5, int(timeout_seconds)) * 1000000),
        "-analyzeduration", "5000000",
        "-probesize", "5000000",
        "-user_agent", ("Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 ("
                        "KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile "
                        "Safari/537.36"),
    ]
    if headers:
        command.extend(["-headers", headers])
    command.extend([
        "-show_entries", "format=format_name,duration,bit_rate:stream=codec_type,codec_name,width,height",
        "-of", "default=noprint_wrappers=1",
        url,
    ])
    try:
        result = subprocess.run(
            command, capture_output=True, startupinfo=get_startup_info(os_type),
            encoding='utf-8', errors='replace', timeout=max(10, int(timeout_seconds) + 10)
        )
        raw = {}
        for line in (result.stdout or '').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                raw[key.strip()] = value.strip()
        info['format'] = raw.get('format_name', '')
        info['video'] = raw.get('codec_name', '')
        width, height = raw.get('width', ''), raw.get('height', '')
        if width.isdigit() and height.isdigit() and int(width) and int(height):
            info['size'] = f"{width}x{height}" + ('(竖屏)' if int(height) > int(width) else '(横屏)')
        rate = raw.get('duration', '')
        info['duration'] = rate
        bitrate = raw.get('bit_rate', '')
        if bitrate.isdigit() and int(bitrate) > 0:
            info['bitrate'] = f"{int(bitrate) / 1000 / 1000:.2f} Mbps"
        info['ok'] = bool(info['format']) and info['format'].lower() not in ('mpeg4', 'm4v')
        info['error'] = (result.stderr or '').strip().splitlines()[-1] if result.stderr else ''
    except subprocess.TimeoutExpired:
        info['error'] = f'探测超时（超过 {timeout_seconds} 秒）'
    except Exception as e:
        info['error'] = str(e)
    return info


def describe_stream_probe(info: dict) -> str:
    """把探测结果拼成一行日志"""
    if not info.get('ok') and not info.get('format'):
        return f"录前探测失败：{info.get('error') or '没有读到任何流信息'}"
    parts = [f"容器={info.get('format') or '未知'}"]
    if info.get('video'):
        parts.append(f"视频={info['video']}")
    if info.get('size'):
        parts.append(info['size'])
    if info.get('bitrate'):
        parts.append(info['bitrate'])
    return '  '.join(parts)


def stop_ffmpeg_process(process, quit_timeout: float = 10.0) -> None:
    """让 ffmpeg 正常收尾（写入文件尾），不听话就强杀，保证分段能切下去"""
    try:
        if process.stdin:
            process.stdin.write(b'q')
            process.stdin.flush()
    except Exception:
        pass
    try:
        process.wait(timeout=quit_timeout)
        return
    except Exception:
        pass
    # ffmpeg 已经卡死（不读 stdin）时，只能强杀，否则这一段的长度会一直涨下去
    logger.warning("ffmpeg 未响应退出指令，强制结束当前分段进程后重连")
    try:
        process.kill()
        process.wait(timeout=10)
    except Exception:
        pass


def segment_watchdog(save_file_path: str, split_time, process, stop_event: threading.Event,
                     start_index: int = 0, record_name: str | None = None,
                     run_started_at: float | None = None):
    """
    录制监控线程（保险丝 + 体检）

    两个职责：

    1) 分段兜底：ffmpeg 的分段复用器正常情况下会自己按“视频分段时间(秒)”切分，但它
       计算切分点依赖输入流的 PTS。长时间直播里一旦出现 PTS 跳变/回退（主播断流重连、
       CDN 切换等），切分点就再也对不上，后果是当前分段一直写下去、内部时间戳错乱。
       所以这里改成“只认墙钟时间”，写满“分段时长 + 余量”就直接让它收尾，
       录制线程随后重连、接着下一段（分段编号连续）。

    2) 体检：有些流能被 ffmpeg 打开、但里面没有一帧有效画面（容器识别失败后
       ffmpeg 退化成裸码流解析），录出来就是 0 秒空壳。这类流自己不会报错，
       不体检就会白录几十分钟。所以文件写入满 stream_health_seconds 秒后
       用 ffprobe 查一次时长，仍然是 0 就判定“这次取到的是坏流”，立刻收工换线路。

    分段超时、连接卡死、坏流三类判定都会终止当前进程，交给外层重新获取地址后重试。
    """
    import glob

    is_split_mode = bool(split_time) and '%03d' in os.path.basename(save_file_path)
    watch_dir = os.path.dirname(save_file_path)
    prefix = os.path.basename(save_file_path).rsplit('_', maxsplit=1)[0]

    if is_split_mode:
        try:
            threshold = float(split_time)  # 严格按用户设置的分段时长
        except (TypeError, ValueError):
            threshold = 1800.0
        # 检查间隔随分段时长自适应：分段越短，检查越频繁，越不会超出设置
        check_interval = max(3.0, min(30.0, threshold / 10.0))
        # 允许的超出量：ffmpeg 正常切分时要等下一个关键帧，留一点余量即可
        slack = max(15.0, min(threshold * 0.05, 90.0))
        max_segment_age = threshold + slack
        # 长时间没有数据写入，说明连接已卡死（这种段再等也不会自己恢复）
        stall_limit = max(120.0, min(threshold * 0.15, 300.0))
        start_msg = (f"分段监控已启动：分段时长 {threshold:.0f} 秒，"
                     f"单段最长 {max_segment_age:.0f} 秒，每 {check_interval:.0f} 秒检查一次")
        logger.info(start_msg)
        print(start_msg)
    else:
        # 不分段录制：只做体检和“连接卡死”兜底，不主动切分
        threshold = float('inf')
        check_interval = 15.0
        max_segment_age = float('inf')
        stall_limit = 180.0
        start_msg = ("录制监控已启动（不分段模式）：每 15 秒检查一次，"
                     f"写入满 {stream_health_seconds:.0f} 秒后体检画面")
        logger.info(start_msg)
        print(start_msg)

    exceed_times = 0  # 连续两次看到“文件时长已达标”才处理，避免 ffmpeg 正在切分时被误判
    warned_file = None
    health_checked_file = None
    # C：写入速率监控状态（零成本，只看已经拿到的文件大小）
    rate_file = None
    rate_last_size = 0
    rate_last_time = 0.0
    rate_samples = []
    rate_low_streak = 0
    rate_low_reported = False
    while not stop_event.wait(check_interval):
        try:
            if is_split_mode:
                # 只看本次录制产生的分段文件（分段序号不小于本次的起始序号）
                pattern = os.path.join(watch_dir, f"{prefix}_*.*")
                files = [f for f in glob.glob(pattern)
                         if _segment_index_of(os.path.basename(f), prefix) >= start_index]
                if not files:
                    continue
                # 分段序号最大的那个，就是当前正在写的分段
                current_file = max(files, key=lambda p: _segment_index_of(os.path.basename(p), prefix))
            else:
                current_file = save_file_path
                if not os.path.exists(current_file):
                    continue

            now = time.time()
            try:
                # 文件创建时间 ≈ 本段开始录制的时间（不能看“最后写入时间”，那是一直在变的）
                file_created_at = os.path.getctime(current_file)
            except OSError:
                file_created_at = now
            # 只有「本次 ffmpeg 运行切出来的第一段」才需要用进程启动时刻兜底：
            # ffmpeg 在 -analyzeduration 探测阶段还没建文件，创建时间会晚于真正开录的时刻，
            # 只按创建时间算会低估段龄，让“单段最长”被探测时间抵消。
            # 但同一个进程之后切出来的段（001、002…），创建时间本来就晚于进程启动时刻，
            # 绝不能再用 min() 兜底 —— 否则段龄会被算成“整个进程跑了多久”，
            # 新切出来的段会被误判成超时，录个 1~2 分钟就被强制切掉。
            if run_started_at:
                current_index = _segment_index_of(os.path.basename(current_file), prefix)
                if current_index <= start_index:
                    file_created_at = min(file_created_at, float(run_started_at))
            segment_age = now - file_created_at
            try:
                idle_time = now - os.path.getmtime(current_file)
            except OSError:
                idle_time = 0.0

            # ===== C：写入速率骤降 = 源流码率掉档（零额外 I/O） =====
            # 主播端推流帧率/码率腰斩时，本机看到的就是文件增长速率明显掉下来。
            # 只做一次提示，等速率恢复后再重新武装，避免刷屏。
            try:
                current_size = os.path.getsize(current_file)
            except OSError:
                current_size = None
            if current_size is not None:
                if rate_file != current_file:
                    # 换到新的分段文件，重新开始统计
                    rate_file = current_file
                    rate_last_size = current_size
                    rate_last_time = now
                    # 初始基准用“这个文件从创建到现在”的平均写入速率。
                    # 否则万一掉速发生在第一次采样之前，峰值基准就永远拿不到，
                    # 报警会被漏掉（单测里就是这么暴露出来的）。
                    rate_samples = [current_size / max(segment_age, 1.0)]
                    rate_low_streak = 0
                    rate_low_reported = False
                elif rate_last_time and now > rate_last_time:
                    instant_rate = (current_size - rate_last_size) / (now - rate_last_time)
                    rate_last_size = current_size
                    rate_last_time = now
                    if instant_rate >= 0:
                        rate_samples.append(instant_rate)
                        if len(rate_samples) > 20:
                            rate_samples.pop(0)
                        peak_rate = max(rate_samples)
                        if peak_rate >= write_rate_min_bytes:
                            if instant_rate < peak_rate * write_rate_alert_ratio:
                                rate_low_streak += 1
                            else:
                                rate_low_streak = 0
                                if instant_rate > peak_rate * 0.8:
                                    rate_low_reported = False
                            if rate_low_streak >= 2 and not rate_low_reported:
                                rate_low_reported = True
                                # 同一个直播间挂了两个画质时，提示里带上画质才能分清是哪一路
                                quality_tag = ''
                                if record_name:
                                    try:
                                        quality_tag = f"[{recording_time_list[record_name][1]}]"
                                    except Exception:
                                        quality_tag = ''
                                now_mbps = instant_rate * 8 / 1000000
                                peak_mbps = peak_rate * 8 / 1000000
                                if instant_rate < 8 * 1024:
                                    # 每秒不足 8KB ≈ 完全没新数据：这是下播/断流，不是“掉档”
                                    rate_msg = (
                                        f"⚠ 源流已停止写入：{record_name}{quality_tag} 现在 "
                                        f"{now_mbps:.1f} Mbps，本段峰值 {peak_mbps:.1f} Mbps。"
                                        f"主播可能已下播或连接被切断；若是正常下播，稍后会自动回到"
                                        f"“等待开播”（若长时间没恢复可检查网络）")
                                else:
                                    rate_msg = (
                                        f"⚠ 源流写入速率骤降：{record_name}{quality_tag} 现在 "
                                        f"{now_mbps:.1f} Mbps，本段峰值 {peak_mbps:.1f} Mbps。"
                                        f"主播端推流可能掉档了（后面容易出现画面掉帧/音画不同步）")
                                logger.warning(rate_msg)
                                print(f"\n{rate_msg}\n")

            reason = None
            duration = 0.0
            bad_stream = False

            # ===== 体检：已经写入这么久，却连一帧有效画面都没有 =====
            if (stream_health_enabled and record_name and health_checked_file != current_file
                    and stream_health_seconds <= segment_age < max_segment_age):
                health_checked_file = current_file
                try:
                    written_size = os.path.getsize(current_file)
                except OSError:
                    written_size = 0
                # 文件里确实写进了数据（不是还没开始写），却读不出时长 → 录到的是垃圾
                if written_size > 64 * 1024:
                    duration = get_video_duration(current_file)
                    if duration < 1.0:
                        bad_stream = True
                        reason = (f"已写入 {segment_age:.0f} 秒、"
                                  f"{written_size / 1024 / 1024:.1f} MB，但读不出任何画面"
                                  f"（ffprobe 时长 0 秒）")
                    else:
                        reset_bad_stream(record_name)
                        # 体检通过也留一行证据：以后翻日志能确认"这次确实体检过、画面是好的"
                        health_msg = (f"录制体检正常：{os.path.basename(current_file)} 已写入 "
                                      f"{segment_age:.0f} 秒，读到时长为 {duration:.0f} 秒")
                        logger.info(health_msg)
                        print(health_msg)
                        set_stream_health(record_name, HEALTH_GOOD, health_msg)
                        # 一场只探一次：读出开头几秒的 SEI，判断团播 / 个播，
                        # 写进日志和状态看板（读开头几秒，代价可以忽略）
                        if record_name and record_name not in live_type_checked:
                            live_type_checked.add(record_name)
                            live_shape = detect_live_type(current_file)
                            if live_shape:
                                shape_msg = f"{record_name} 直播间形态：{live_shape}"
                                logger.info(shape_msg)
                                print(shape_msg)
                                with runtime_status_lock:
                                    runtime_meta.setdefault(record_name, {})['live_type'] = live_shape
                                set_stream_health(record_name, HEALTH_GOOD,
                                                  f"{health_msg}；形态：{live_shape}")

            if reason is None and segment_age >= max_segment_age:
                duration = get_video_duration(current_file)
                reason = (f"已经从 0 写到 {segment_age:.0f} 秒仍未切分"
                          f"（上限 {max_segment_age:.0f} 秒）")
            elif reason is None and idle_time >= stall_limit:
                duration = get_video_duration(current_file)
                reason = f"已经 {idle_time:.0f} 秒没有任何新数据写入（连接卡死）"
            elif reason is None and is_split_mode and segment_age >= threshold:
                # 只有“可能没切成”的时候才去探测文件时长（大文件探测很慢）
                duration = get_video_duration(current_file)
                if duration >= threshold:
                    exceed_times += 1
                    if exceed_times < 2:
                        continue  # 可能 ffmpeg 正在等下一个关键帧分段，再观察一次
                    reason = f"文件时长 {duration:.0f} 秒，已达到分段时长 {threshold:.0f} 秒"
                else:
                    exceed_times = 0
                    if duration < segment_age * 0.5 and warned_file != current_file:
                        warned_file = current_file
                        logger.warning(f"分段监控：{os.path.basename(current_file)} 实际已写 "
                                       f"{segment_age:.0f} 秒，但文件时长只有 {duration:.0f} 秒，"
                                       f"直播流时间戳可能异常，到 {max_segment_age:.0f} 秒会强制切分")
            elif reason is None:
                exceed_times = 0

            if reason:
                if bad_stream:
                    count = mark_bad_stream(record_name)
                    message = (f"⚠ {record_name} 这次取到的直播流没有有效画面：{reason}。"
                               f"录进去的是无法播放的空壳，本段放弃"
                               + ("，正在换用另一条线路重新获取地址后重试"
                                  if stream_auto_switch_line else "")
                               + (f"（连续第 {count} 次）" if count > 1 else ""))
                    logger.error(message)
                    print(f"\n{message}\n")
                    set_stream_health(record_name, HEALTH_BAD,
                                      f"录到的是没有画面的空壳：{reason}", bad_count=count)
                else:
                    logger.warning(f"分段监控：{os.path.basename(current_file)} {reason}"
                                   f"{f'，ffprobe 时长 {duration:.0f} 秒' if duration else ''}，"
                                   f"结束当前分段并开始下一段...")
                stop_ffmpeg_process(process)
                break
        except Exception as e:
            logger.debug(f"分段监控异常: {e}")


def fix_segment_timestamps(file_list: list) -> None:
    """批量修复分段文件的时间戳（每个文件重置到0开始）"""
    import tempfile
    for file_path in file_list:
        temp_path = None
        try:
            # 创建临时文件
            dir_name = os.path.dirname(file_path)
            base_name = os.path.basename(file_path)
            temp_path = os.path.join(dir_name, f"_temp_{base_name}")
            
            # 用 ffmpeg 重新封装，重置时间戳
            command = [
                "ffmpeg", "-y", "-i", file_path,
                "-c", "copy",
                "-reset_timestamps", "1",
                "-avoid_negative_ts", "make_zero",
                temp_path
            ]
            
            result = subprocess.run(
                command, 
                capture_output=True, 
                startupinfo=get_startup_info(os_type),
                encoding='utf-8', 
                errors='replace'
            )
            
            if result.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                # 替换原文件
                os.remove(file_path)
                os.rename(temp_path, file_path)
                logger.debug(f"时间戳修复成功: {base_name}")
            else:
                logger.warning(f"时间戳修复失败: {base_name}, returncode: {result.returncode}")
        except Exception as e:
            logger.error(f"修复分段时间戳异常 {file_path}: {e}")
        finally:
            # 确保临时文件被清理
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass


# 分段文件名前缀（同一主播同一次直播内的分段共用，保证编号连续）
segment_series = {}
segment_series_lock = threading.Lock()


def get_segment_series_prefix(record_name: str, anchor_name: str, title_in_name: str) -> str:
    """同一主播同一次直播内的所有分段共用一个文件名前缀，分段编号连续（000、001、002...）"""
    with segment_series_lock:
        prefix = segment_series.get(record_name)
        if not prefix:
            now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
            prefix = f"{anchor_name}_{title_in_name}{now}"
            segment_series[record_name] = prefix
        return prefix


def clear_segment_series(record_name: str) -> None:
    """主播下播后，下次开播重新开始一套分段编号"""
    with segment_series_lock:
        segment_series.pop(record_name, None)


def _segment_index_of(file_name: str, prefix: str) -> int:
    """从分段文件名中取出分段序号，取不到返回 -1。
    注意：体检隔离出来的 xxx_000.broken.ts 也会返回 -1，
    这样它既不会被当成待处理分段，也不会影响下一段的编号。"""
    if not file_name.startswith(prefix + '_'):
        return -1
    stem = file_name[len(prefix) + 1:]
    if '.' not in stem:
        return -1
    number, ext = stem.split('.', 1)
    if not number.isdigit() or '.' in ext:
        return -1
    return int(number)


def next_segment_index(folder: str, prefix: str) -> int:
    """返回下一个可用的分段序号，避免覆盖已经录好的分段文件"""
    max_index = -1
    try:
        for name in os.listdir(folder):
            max_index = max(max_index, _segment_index_of(name, prefix))
    except Exception as e:
        logger.error(f"扫描分段文件失败: {e}")
    return max_index + 1


def segment_files_of_series(folder: str, prefix: str, start_index: int) -> list:
    """列出本次分段录制新产生的分段文件（按分段序号排序）"""
    result = []
    try:
        for name in os.listdir(folder):
            index = _segment_index_of(name, prefix)
            if index >= start_index and not name.startswith('_temp_'):
                result.append(os.path.join(folder, name))
    except Exception as e:
        logger.error(f"读取分段文件失败: {e}")
    result.sort(key=lambda p: _segment_index_of(os.path.basename(p), prefix))
    return result


def segment_files_summary(files: list) -> str:
    """分段结果摘要：每段的时长（秒），方便确认分段时长设置是否生效"""
    summary = []
    for path in files:
        duration = get_video_duration(path)
        summary.append(f"{os.path.basename(path)}({duration:.0f}秒)")
    return '、'.join(summary)


# ===================== A：源流详细体检（读抖音推流 SDK 写在视频帧里的 SEI） =====================
# 抖音直播伴侣/推流 SDK 会把一段 JSON 塞在每个关键帧的 SEI 里，内容包括主播端自报的
# 实时帧率、实时码率、推流延迟，以及这一帧的墙钟时间。它本来就在我们录到的码流里，
# 所以“事后只读扫一遍”就能判断这条流是不是在主播端就已经掉帧/掉码率/时间轴跑偏。
#
# 开销说明（为什么不影响录制）：
#   1) 只读 + 只 copy + 只把 SEI 那几个 NAL 丢出来（几百 KB），不解码、不转码；
#   2) 跑在分段收尾的后台线程里（和“修时间戳 / 转 mp4”同一阶段），录制主链路完全不碰；
#   3) 关掉「源流详细体检」开关就一次都不跑。
SEI_KEY = '{"audio_loudness"'
SEI_MAX_BYTES = 64 * 1024 * 1024   # 极端情况下兜底，防止内存失控
CDN_RECONNECT_MARKS = ('Stream ends prematurely', 'Packet mismatch')
# 同一批分段只允许收尾一次。
# 停止录制时，“check_subprocess 的退出分支”和“分段循环”会把同一批分段各登记一次收尾，
# 两条后台线程于是同时跑“修时间戳”（内部是 删除原文件 + 改名 两步），
# 实测过撞车会把一个分段文件直接弄丢（原文件被删、临时文件又被 finally 清掉）。
post_processed_files = set()
post_processed_lock = threading.Lock()


def _to_positive_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _median_or_none(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _format_pts(pts) -> str:
    if pts is None:
        return '未知位置'
    seconds = max(0, int(pts))
    return f"第 {seconds // 60} 分 {seconds % 60} 秒处"


def _sustained_drop(points, ratio: float, min_run: int = 3, min_share: float = 0.05):
    """判断是否“持续掉档”：连续 min_run 个点低于中位数的 ratio，
    或者这类点占比超过 min_share。返回 (是否异常, 最长连续数, 总个数, 中位数)。

    只用“有没有 1 个点偏低”来判断会严重误报——实测主播端偶发 1 秒抖动
    （帧率掉到 30、码率掉到 10.9Mbps）就被判成“源流有问题”。
    """
    if len(points) < min_run:
        return False, 0, 0, None
    values = [value for _, value in points]
    med = _median_or_none(values)
    if not med:
        return False, 0, 0, None
    low_count = 0
    run = longest = 0
    for _, value in points:
        if value < med * ratio:
            low_count += 1
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    abnormal = longest >= min_run or low_count >= max(min_run, len(points) * min_share)
    return abnormal, longest, low_count, med


def iter_source_sei(buf: bytes):
    """在字节流里逐条解析 SEI JSON，产出 (字节偏移, 字典)"""
    text = buf.decode('latin-1', 'ignore')
    decoder = json.JSONDecoder()
    idx = 0
    while True:
        pos = text.find(SEI_KEY, idx)
        if pos < 0:
            return
        try:
            obj, end = decoder.raw_decode(text, pos)
        except ValueError:
            idx = pos + 1
            continue
        yield pos, obj
        idx = end


def collect_source_sei(file_path: str, timeout: int | None = None, seconds=None) -> list:
    """只读扫一遍录像文件，抽出抖音推流 SEI：返回 [(文件PTS秒, SEI字典), ...]

    把只含 SEI 的码流重新封成 mpegts，就能顺便拿到每个 PES 的 PTS，
    这样“这条 SEI 在文件里的第几秒”也能对上。读不到就返回空列表，不报错刷屏。
    seconds 不为空时只读文件开头这么多秒（判断直播间形态时用，代价很小）。
    """
    try:
        if not os.path.exists(file_path) or os.path.getsize(file_path) < 188 * 10:
            return []
    except OSError:
        return []
    command = ["ffmpeg", "-v", "error"]
    if seconds:
        command += ["-t", str(seconds)]
    command += [
        "-i", file_path,
        "-map", "0:v:0", "-c", "copy",
        "-bsf:v", "filter_units=pass_types=6",
        "-f", "mpegts", "-",
    ]
    if timeout is None:
        # 这一步挡在“转 mp4”前面，卡住会把整个收尾拖死（UI 最多等 180 秒）。
        # 实测 891MB 约 0.5 秒（缓存命中），冷读机械盘也就几十秒，所以给足余量但必须封顶。
        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
        except OSError:
            size_mb = 0
        timeout = int(max(30, min(180, size_mb / 20)))
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=timeout,
            startupinfo=get_startup_info(os_type)
        )
    except Exception as e:
        logger.debug(f"源流体检：抽取 SEI 失败 {os.path.basename(file_path)}: {e}")
        return []
    data = result.stdout or b''
    if len(data) < 188 * 2:
        return []

    buf = bytearray()
    pes_marks = []
    total = (len(data) // 188) * 188
    for offset in range(0, total, 188):
        packet = data[offset:offset + 188]
        if packet[0] != 0x47:
            continue
        payload_unit_start = packet[1] & 0x40
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        if pid == 0x1FFF:
            continue
        afc = (packet[3] >> 4) & 0x03
        if afc == 0:
            continue
        pos = 4
        if afc == 3:
            pos += 1 + packet[4]
        if pos >= 188:
            continue
        payload = packet[pos:]
        if payload_unit_start and len(payload) > 13 and payload[:3] == b'\x00\x00\x01':
            flags = payload[7]
            if flags & 0x80:      # 带 PTS
                pts = (((payload[9] >> 1) & 0x07) << 30) | (payload[10] << 22) | \
                      (((payload[11] >> 1) & 0x7F) << 15) | (payload[12] << 7) | \
                      ((payload[13] >> 1) & 0x7F)
                pes_marks.append((len(buf), pts / 90000.0))
            pos = 9 + payload[8]
            if pos >= len(payload):
                continue
            payload = payload[pos:]
        buf.extend(payload)
        if len(buf) > SEI_MAX_BYTES:
            break

    samples = []
    mark_index = 0
    for sei_offset, info in iter_source_sei(bytes(buf)):
        while mark_index + 1 < len(pes_marks) and pes_marks[mark_index + 1][0] <= sei_offset:
            mark_index += 1
        pts = pes_marks[mark_index][1] if pes_marks else None
        samples.append((pts, info))
    return samples


def detect_live_type(file_path: str) -> str:
    """读文件开头的 SEI，判断这场是团播还是个播；读不到返回空串。

    判据：抖音推流 SDK 只在团播/多人直播里写 group_live 这一段
    （实测团播有、个播没有），比猜房间字段可靠。
    """
    try:
        samples = collect_source_sei(file_path, seconds=8)
    except Exception as e:
        logger.debug(f"形态探测失败: {e}")
        return ''
    if not samples:
        return ''
    for _, info in samples:
        group = info.get('group_live')
        if isinstance(group, dict):
            playmode = ((group.get('game_event_params') or {})
                        .get('group_live_playmode') or '')
            return f"团播（{playmode}）" if playmode else '团播'
    return '个播'


def count_cdn_reconnects(split_files: list) -> int:
    """统计这次录制对应的 ffmpeg 日志里 CDN 断流重连的次数（日志很小，开销可忽略）"""
    try:
        log_dir = os.path.join(os.path.dirname(split_files[0]), 'ffmpeg_logs')
        if not os.path.isdir(log_dir):
            return 0
        prefix = os.path.basename(split_files[0]).rsplit('_', 1)[0]
        count = 0
        for name in os.listdir(log_dir):
            if not name.startswith(prefix) or not name.lower().endswith('.log'):
                continue
            try:
                with open(os.path.join(log_dir, name), 'r',
                          encoding='utf-8', errors='replace') as fh:
                    text = fh.read()
            except OSError:
                continue
            for mark in CDN_RECONNECT_MARKS:
                count += text.count(mark)
        return count
    except Exception:
        return 0


def summarize_source_health(split_files: list, tag: str = '') -> None:
    """给这次录制的源流出一份体检报告；读不到 SEI（非抖音流等）就静默跳过"""
    files = [path for path in split_files if path]
    if not files:
        return
    # 分段多时只抽查头/中/尾，避免把收尾时间拉长
    picks = [files[0], files[len(files) // 2], files[-1]] if len(files) > 3 else files
    samples = []
    per_file = []          # [(文件名, 该文件的采样)]，时间轴漂移必须按单个文件算
    for path in picks:
        try:
            got = collect_source_sei(path)
        except Exception as e:
            logger.debug(f"源流体检异常 {os.path.basename(path)}: {e}")
            continue
        if got:
            per_file.append((os.path.basename(path), got))
            samples.extend(got)
    if not samples:
        return

    fps_points = [(pts, _to_positive_number(info.get('real_video_framerate')))
                  for pts, info in samples]
    fps_points = [(pts, value) for pts, value in fps_points if value]
    rate_points = [(pts, _to_positive_number(info.get('real_bitrate')))
                   for pts, info in samples]
    rate_points = [(pts, value) for pts, value in rate_points if value]

    name = tag or os.path.basename(files[0])
    lines = [f"📊 源流体检（{name}）：抽取 {len(samples)} 个样本（主播端推流元数据）"]

    # 直播间形态：抖音推流 SDK 只在团播/多人直播里写 group_live 这一段。
    # 实测（团播 Lw_珑舞训练营 有、个播 陈刀仔 无），所以拿它当团播标记。
    group_live_info = None
    for _, info in samples:
        if isinstance(info.get('group_live'), dict):
            group_live_info = info['group_live']
            break
    if group_live_info is not None:
        playmode = ((group_live_info.get('game_event_params') or {})
                    .get('group_live_playmode') or '')
        shape_text = f"团播（{playmode}）" if playmode else '团播'
    else:
        shape_text = '个播'
    lines.append(f"   直播间形态：{shape_text}")

    fps_abnormal = False
    if fps_points:
        median_fps = _median_or_none([value for _, value in fps_points])
        low_fps = min(fps_points, key=lambda item: item[1])
        low_count = sum(1 for _, value in fps_points if value < median_fps * 0.8)
        lines.append(f"   主播端自报帧率：中位 {median_fps:.0f}，最低 {low_fps[1]:.0f}"
                     f"（{_format_pts(low_fps[0])}），明显掉帧的点 {low_count} 个")
        fps_abnormal, fps_run, _, _ = _sustained_drop(fps_points, 0.8)
        if fps_abnormal:
            lines.append(f"      ⚠ 帧率连续 {fps_run} 个采样低于中位 80%")
        elif low_count:
            lines.append("      （只是偶发抖动，不算掉档）")

    rate_abnormal = False
    if rate_points:
        median_rate = _median_or_none([value for _, value in rate_points])
        low_rate = min(rate_points, key=lambda item: item[1])
        lines.append(f"   主播端自报码率：中位 {median_rate / 1000:.1f} Mbps，"
                     f"最低 {low_rate[1] / 1000:.1f} Mbps（{_format_pts(low_rate[0])}）")
        rate_abnormal, rate_run, _, _ = _sustained_drop(rate_points, 0.6)
        if rate_abnormal:
            lines.append(f"      ⚠ 码率连续 {rate_run} 个采样低于中位 60%")

    # 时间轴漂移：**必须按单个文件算**。
    # 每个分段的 PTS 都从 0 重新开始（"修时间戳"就是干这个的），
    # 把多个文件的采样拼起来相减会得到几万秒的假漂移
    # （实测被误报过 +12600 秒，并据此错误判定"源流有问题"）。
    drifts = []
    for file_name, got in per_file:
        paired = [(pts, _to_positive_number(info.get('ts')))
                  for pts, info in got if pts is not None]
        paired = [(pts, stamp) for pts, stamp in paired if stamp]
        if len(paired) >= 2:
            span_pts = paired[-1][0] - paired[0][0]
            span_wall = (paired[-1][1] - paired[0][1]) / 1000.0
            drifts.append((file_name, span_wall - span_pts))
    drift = None
    if drifts:
        worst_name, worst_drift = max(drifts, key=lambda item: abs(item[1]))
        drift = worst_drift
        lines.append(f"   时间轴漂移：{worst_drift:+.1f} 秒（最大的一段：{worst_name}）")
        if len(drifts) > 1:
            lines.append('      各段漂移：' + '、'.join(f'{d:+.1f}s' for _, d in drifts[:10]))

    reconnects = count_cdn_reconnects(files)
    if reconnects:
        lines.append(f"   CDN 断流重连：{reconnects} 次")

    if fps_abnormal or rate_abnormal or (drift is not None and abs(drift) >= 2.0):
        lines.append("   ⚠ 判定：这条流在主播端就已经掉帧/掉码率或时间轴跑偏，"
                     "录到的画面问题来自源流本身，不是本机录制造成的")
        if group_live_info is not None:
            lines.append("   💡 团播提示：原画=主播端直推的流，超清=抖音服务端转码流。"
                         "若录像里出现音画不同步/掉帧，可以把这个直播间改成「超清」再录一场做对比。")
    else:
        lines.append("   ✅ 判定：主播端推流帧率/码率/时间轴都正常")

    for line in lines:
        logger.info(line)
        print(line)


def post_process_segments(split_files: list, is_original_delete: bool = True,
                          tag: str = '') -> None:
    """分段录制结束后的收尾：先体检隔离坏段，再修复时间戳，最后按需转成 mp4（串行，避免互相抢占同一个文件）"""
    files = [path for path in split_files if path]
    # 同一批分段只收尾一次：两条登记路径同时跑会让“修时间戳”的删除/改名撞车，
    # 轻则重复转码刷屏，重则直接把分段文件弄丢（实测到过）。
    try:
        process_key = (os.path.dirname(files[0]),
                       tuple(sorted(os.path.basename(p) for p in files)))
    except Exception:
        process_key = None
    if process_key is not None:
        with post_processed_lock:
            if process_key in post_processed_files:
                logger.debug(f"这批分段已经收尾过（{len(files)} 个），跳过重复收尾")
                return
            post_processed_files.add(process_key)
    try:
        # 体检：没有有效画面的分段直接改名隔离，绝不送进时间戳修复和转码
        good_files, broken_files = quarantine_broken_segments(split_files)
        if broken_files:
            message = (f"本次有 {len(broken_files)} 个分段没有有效画面，已隔离为 .broken.ts："
                       + '、'.join(os.path.basename(p) for p in broken_files))
            logger.error(message)
            print(f"\n⚠ {message}\n")
        split_files = good_files
        if not split_files:
            return
    except Exception as e:
        logger.debug(f"分段体检失败: {e}")
    try:
        logger.info(f"本次分段时长：{segment_files_summary(split_files)}")
        # 把录到的画面信息写进日志：万一又出现"横屏/画质低"，一眼就能看出是不是取流档位的问题
        for path in split_files[:3]:
            info = get_video_info(path)
            message = f"分段画面信息: {os.path.basename(path)} -> {info}"
            logger.info(message)
            print(message)
            if '（横屏）' in info:
                # 只在“竖屏房间却录到横屏”时才提醒。
                # 房间本身就是横屏（方向标记=2，比如潇潇那样的秀场）属于正常情况，
                # 以前每次都报警，纯属噪音。
                orientation = record_orientation.get(tag)
                if orientation == 1:
                    warn = (f"⚠ {os.path.basename(path)} 录到的是横屏画面（{info}）。"
                            f"这个房间标记是竖屏，说明这次取到的不是竖屏原画那一档，"
                            f"请把这条记录发给我确认。")
                    logger.warning(warn)
                    print(warn)
                else:
                    logger.debug(f"房间方向标记={orientation}，横屏属于正常，跳过横屏提醒")
    except Exception as e:
        logger.debug(f"统计分段时长失败: {e}")
    try:
        logger.info(f"开始处理本次录制的 {len(split_files)} 个分段文件：修复时间戳...")
        fix_segment_timestamps(split_files)
        logger.info("分段时间戳修复完成")
    except Exception as e:
        logger.error(f"修复分段时间戳失败: {e}")

    # 源流详细体检：只读抽一次 SEI（必须在转 mp4 / 删原文件之前做）
    if source_health_detail:
        try:
            summarize_source_health(
                split_files,
                tag=os.path.basename(split_files[0]).rsplit('_', 1)[0])
        except Exception as e:
            logger.debug(f"源流体检跳过: {e}")

    if not converts_to_mp4:
        return

    for path in split_files:
        if not path.lower().endswith('.ts'):
            continue
        mp4_path = path.rsplit('.', maxsplit=1)[0] + '.mp4'
        if os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 0:
            continue
        try:
            converts_mp4(path, is_original_delete)
        except Exception as e:
            logger.error(f"转码失败: {e}")


def start_post_process(split_files: list, is_original_delete: bool = True, tag: str = '') -> None:
    """登记并启动一个收尾任务（体检隔离 → 修时间戳 → 转 mp4）。

    登记的意义：停止录制时能等到收尾真的做完再退出，
    不会留下“一半 ts 一半 mp4”或半截 mp4。
    """
    if not split_files:
        return
    global post_task_sequence
    with post_task_lock:
        post_task_sequence += 1
        key = f'{tag}|{post_task_sequence}|{len(split_files)}'
        pending_post_tasks.add(key)

    def _run() -> None:
        try:
            post_process_segments(split_files, is_original_delete, tag)
        finally:
            with post_task_lock:
                pending_post_tasks.discard(key)

    threading.Thread(target=_run, daemon=True).start()


def has_pending_post_tasks() -> bool:
    with post_task_lock:
        return bool(pending_post_tasks)


def generate_subtitles(record_name: str, ass_filename: str, sub_format: str = 'srt') -> None:
    index_time = 0
    today = datetime.datetime.now()
    re_datatime = today.strftime('%Y-%m-%d %H:%M:%S')

    def transform_int_to_time(seconds: int) -> str:
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    while True:
        index_time += 1
        txt = str(index_time) + "\n" + transform_int_to_time(index_time) + ',000 --> ' + transform_int_to_time(
            index_time + 1) + ',000' + "\n" + str(re_datatime) + "\n\n"

        with open(f"{ass_filename}.{sub_format.lower()}", 'a', encoding=text_encoding) as f:
            f.write(txt)

        if record_name not in recording:
            return
        time.sleep(1)
        today = datetime.datetime.now()
        re_datatime = today.strftime('%Y-%m-%d %H:%M:%S')


def adjust_max_request() -> None:
    global max_request, error_count, pre_max_request, error_window
    preset = max_request

    while True:
        time.sleep(5)
        with max_request_lock:
            if error_window:
                error_rate = sum(error_window) / len(error_window)
            else:
                error_rate = 0

            if error_rate > error_threshold:
                max_request = max(1, max_request - 1)
            elif error_rate < error_threshold / 2 and max_request < preset:
                max_request += 1
            else:
                pass

            if pre_max_request != max_request:
                pre_max_request = max_request
                print(f"\r同一时间访问网络的线程数动态改为 {max_request}")

        error_window.append(error_count)
        if len(error_window) > error_window_size:
            error_window.pop(0)
        error_count = 0


def clear_record_info(record_name: str, record_url: str, record_quality: str | None = None) -> None:
    """把这条录制从运行列表里摘掉。

    运行列表的键是 (画质, 地址)：同一个直播间可以同时挂两条不同画质，
    这里只摘掉自己那一条，不会把另一个画质的记录一起摘掉。
    """
    global monitoring
    recording.discard(record_name)
    if record_url not in url_comments:
        return
    if record_quality is None:
        # 没显式传画质时，从录制时登记的信息里取（同一个直播间可能挂着两条不同画质）
        try:
            record_quality = (recording_time_list.get(record_name) or [None, None])[1]
        except Exception:
            record_quality = None
    removed = False
    if record_quality is not None:
        key = (record_quality, record_url)
        if key in running_list:
            running_list.remove(key)
            removed = True
    else:
        for item in list(running_list):
            if isinstance(item, tuple) and item[1] == record_url:
                running_list.remove(item)
                removed = True
    if removed:
        monitoring -= 1
        color_obj.print_colored(f"[{record_name}]已经从录制列表中移除\n", color_obj.YELLOW)


def direct_download_stream(source_url: str, save_path: str, record_name: str, live_url: str,
                           platform: str, record_quality: str | None = None) -> bool:
    try:
        with open(save_path, 'wb') as f:
            client = httpx.Client(timeout=None)

            headers = {}
            header_params = get_record_headers(platform, live_url)
            if header_params:
                key, value = header_params.split(":", 1)
                headers[key] = value

            with client.stream('GET', source_url, headers=headers, follow_redirects=True) as response:
                if response.status_code != 200:
                    logger.error(f"请求直播流失败，状态码: {response.status_code}")
                    return False

                downloaded = 0
                chunk_size = 1024 * 16

                for chunk in response.iter_bytes(chunk_size):
                    if live_url in url_comments or exit_recording:
                        color_obj.print_colored(f"[{record_name}]录制时已被注释或请求停止,下载中断", color_obj.YELLOW)
                        clear_record_info(record_name, live_url, record_quality)
                        return False

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                print()
                return True
    except Exception as e:
        logger.error(f"FLV下载错误: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
        return False


def _check_subprocess_impl(record_name: str, record_url: str, ffmpeg_command: list, save_type: str,
                           script_command: str | None = None, split_time: int = None,
                           segment_start_index: int = 0) -> bool:
    save_file_path = ffmpeg_command[-1]
    # 把 ffmpeg 的输出重定向到单独的日志文件，方便排查错误
    # 不通过管道，避免阻塞
    log_dir = os.path.join(os.path.dirname(save_file_path), 'ffmpeg_logs')
    os.makedirs(log_dir, exist_ok=True)
    ffmpeg_log_file = os.path.join(
        log_dir,
        f"{Path(save_file_path).stem}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    ffmpeg_log_fh = open(ffmpeg_log_file, 'w', encoding='utf-8', errors='replace')
    
    # 记录 ffmpeg 进程启动时刻：首段可能在探测（-analyzeduration）之后才建文件，
    # 监控线程用它兜底，避免“单段最长”被探测时间抵消（见 segment_watchdog）。
    run_started_at = time.time()
    process = subprocess.Popen(
        ffmpeg_command, stdin=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdout=ffmpeg_log_fh,
        startupinfo=get_startup_info(os_type)
    )
    
    # 启动录制监控线程：分段录制负责切分兜底，不分段录制只做“体检 + 卡死”兜底
    watchdog_thread = None
    watchdog_stop_event = None
    is_media_output = bool(save_file_path) and save_file_path.lower().endswith(('.ts', '.mkv', '.flv', '.mp4'))
    if is_media_output:
        watchdog_stop_event = threading.Event()
        watchdog_thread = threading.Thread(
            target=segment_watchdog,
            args=(save_file_path, split_time, process, watchdog_stop_event,
                  int(segment_start_index or 0), record_name, run_started_at),
            daemon=True
        )
        watchdog_thread.start()

    def _cleanup_after_stop() -> None:
        """统一收尾：停监控线程 + 关 ffmpeg 日志句柄（正常结束、提前退出都要走）"""
        if watchdog_stop_event:
            watchdog_stop_event.set()
        try:
            ffmpeg_log_fh.close()
        except Exception:
            pass

    subs_file_path = save_file_path.rsplit('.', maxsplit=1)[0]
    subs_thread_name = f'subs_{Path(subs_file_path).name}'
    if create_time_file and not split_video_by_time and '音频' not in save_type:
        create_var[subs_thread_name] = threading.Thread(
            target=generate_subtitles, args=(record_name, subs_file_path)
        )
        create_var[subs_thread_name].daemon = True
        create_var[subs_thread_name].start()

    while process.poll() is None:
        if record_url in url_comments or exit_recording or os.path.exists(stop_signal_file):
            stop_reason = "收到退出请求" if os.path.exists(stop_signal_file) else "录制时已被注释"
            color_obj.print_colored(f"[{record_name}]{stop_reason},正在收尾当前分段后退出",
                                    color_obj.YELLOW)
            clear_record_info(record_name, record_url)
            if os.name == 'nt':
                if process.stdin:
                    process.stdin.write(b'q')
                    process.stdin.close()
            else:
                process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except Exception:
                # ffmpeg 卡着不退出时强杀，否则日志句柄和监控线程都收不掉
                stop_ffmpeg_process(process)
            # 提前退出也必须收尾：停监控线程、关日志句柄、清理状态，避免句柄/线程泄漏
            _cleanup_after_stop()
            with last_live_status_lock:
                last_live_status.pop(record_name, None)
            # 被注释/收到退出请求：当前 ffmpeg 已经收尾，把这一轮已经写完的分段也走一遍收尾
            # 流程（修时间戳 + 转 mp4），否则文件夹里会留下一堆没转的 .ts
            try:
                if save_type == 'TS' and '%03d' in os.path.basename(save_file_path):
                    folder = os.path.dirname(save_file_path)
                    series_prefix = os.path.basename(save_file_path).rsplit('_', maxsplit=1)[0]
                    finished = [p for p in segment_files_of_series(
                        folder, series_prefix, int(segment_start_index or 0))
                        if '.broken.' not in os.path.basename(p).lower()]
                    start_post_process(finished, delete_origin_file, tag=record_name)
            except Exception as e:
                logger.debug(f"退出前收尾登记失败: {e}")
            # 收尾任务登记完之后才把自己从“录制中”摘掉：
            # 否则主循环会有机会在这个空档里直接退出（见 active_record_runs 的注释）
            recording.discard(record_name)
            return True
        time.sleep(1)

    # 停止分段监控线程 + 关闭 ffmpeg 日志文件句柄
    _cleanup_after_stop()
    
    return_code = process.returncode
    stop_time = time.strftime('%Y-%m-%d %H:%M:%S')
    if return_code == 0:
        # 分段录制（ts + %03d）的收尾统一由分段循环负责（start_post_process），
        # 这里不再重复转一次，避免两条路径并发写同一个 mp4。
        is_segment_ts = save_type == 'TS' and '%03d' in os.path.basename(save_file_path)
        if converts_to_mp4 and save_type == 'TS' and not is_segment_ts:
            if split_video_by_time:
                # 只扫本主播目录里的 .ts 分段源文件：
                # 老写法是递归扫描整个目录 + “文件名含前缀就转”，结果会把 ffmpeg_logs\*.log、
                # 已经转好的 .mp4 也丢给转码函数（重复转码、报错刷屏，非空日志还会被
                # is_media_empty 判成坏文件改名成 .broken.log）。
                folder = os.path.dirname(save_file_path)
                prefix = os.path.basename(save_file_path).rsplit('_', maxsplit=1)[0]
                try:
                    names = os.listdir(folder)
                except OSError as e:
                    logger.error(f"读取分段目录失败: {e}")
                    names = []
                for name in names:
                    lower_name = name.lower()
                    # 只处理本次录制的 .ts；跳过日志、已隔离的 .broken.ts、以及已经转好的 mp4
                    if not lower_name.endswith('.ts') or '.broken.' in lower_name:
                        continue
                    if not name.startswith(prefix):
                        continue
                    path = os.path.join(folder, name)
                    if not os.path.isfile(path):
                        continue
                    mp4_path = path.rsplit('.', maxsplit=1)[0] + '.mp4'
                    if os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 0:
                        continue
                    threading.Thread(target=converts_mp4, args=(path, delete_origin_file)).start()
            else:
                threading.Thread(target=converts_mp4, args=(save_file_path, delete_origin_file)).start()
        print(f"\n{record_name} {stop_time} 直播录制完成\n")

        if script_command:
            logger.debug("开始执行脚本命令!")
            if "python" in script_command:
                params = [
                    f'--record_name "{record_name}"',
                    f'--save_file_path "{save_file_path}"',
                    f'--save_type {save_type}',
                    f'--split_video_by_time {split_video_by_time}',
                    f'--converts_to_mp4 {converts_to_mp4}',
                ]
            else:
                params = [
                    f'"{record_name.split(" ", maxsplit=1)[-1]}"',
                    f'"{save_file_path}"',
                    save_type,
                    f'split_video_by_time:{split_video_by_time}',
                    f'converts_to_mp4:{converts_to_mp4}'
                ]
            script_command = script_command.strip() + ' ' + ' '.join(params)
            run_script(script_command)
            logger.debug("脚本命令执行结束!")

    else:
        error_msg = f"\n{record_name} {stop_time} 直播录制出错,返回码: {return_code},将快速自动重试\n"
        color_obj.print_colored(error_msg, color_obj.RED)
        logger.error(error_msg)
        logger.error(f"ffmpeg 详细错误日志: {ffmpeg_log_file}")

    global last_ffmpeg_return_code
    last_ffmpeg_return_code = return_code

    recording.discard(record_name)
    # 清理该主播的直播状态记录，避免内存泄漏
    with last_live_status_lock:
        if record_name in last_live_status:
            del last_live_status[record_name]
    return False


def check_subprocess(*args, **kwargs):
    """录制进程外壳：统计“正在跑的录制”，保证退出请求不会被收尾空档钻空子。

    真正的实现在 _check_subprocess_impl；这里只是加一层计数（进出各一次），
    主循环据此判断“录制相关的事情还有没有在跑”。
    """
    mark_record_run(1)
    try:
        return _check_subprocess_impl(*args, **kwargs)
    finally:
        mark_record_run(-1)


def run_script(command: str) -> None:
    try:
        process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=get_startup_info(os_type)
        )
        stdout, stderr = process.communicate()
        stdout_decoded = stdout.decode('utf-8')
        stderr_decoded = stderr.decode('utf-8')
        if stdout_decoded.strip():
            print(stdout_decoded)
        if stderr_decoded.strip():
            print(stderr_decoded)
    except PermissionError as e:
        logger.error(e)
        logger.error('脚本无执行权限!, 若是Linux环境, 请先执行:chmod +x your_script.sh 授予脚本可执行权限')
    except OSError as e:
        logger.error(e)
        logger.error('Please add `#!/bin/bash` at the beginning of your bash script file.')


def clean_name(input_text):
    cleaned_name = re.sub(rstr, "_", input_text.strip()).strip('_')
    cleaned_name = cleaned_name.replace("（", "(").replace("）", ")")
    if clean_emoji:
        cleaned_name = utils.remove_emojis(cleaned_name, '_').strip('_')
    return cleaned_name or '空白昵称'


def get_quality_code(qn):
    QUALITY_MAPPING = {
        "原画": "OD",
        "蓝光": "BD",
        "超清": "UHD",
        "高清": "HD",
        "标清": "SD",
        "流畅": "LD"
    }
    return QUALITY_MAPPING.get(qn)


def get_record_headers(platform, live_url):
    live_domain = '/'.join(live_url.split('/')[0:3])
    # 构建完整的 HTTP 头，增强兼容性
    headers_parts = [
        'origin:https://www.douyin.com',
        'referer:https://live.douyin.com/',
        'accept:*/*',
        'accept-language:zh-CN,zh;q=0.9',
    ]
    # 如果配置了 Cookie，添加 Cookie 头
    try:
        if dy_cookie:
            headers_parts.append(f'cookie:{dy_cookie}')
    except Exception:
        pass
    record_headers = {
        'Douyin': '\r\n'.join(headers_parts)
    }
    return record_headers.get(platform)


def is_flv_preferred_platform(link):
    return any(i in link for i in ["douyin", "tiktok"])


def select_source_url(link, stream_info, record_name: str | None = None):
    if is_flv_preferred_platform(link):
        # 上一次取到的流没有有效画面时，临时改用另一条线路（FLV -> HLS）重新获取地址
        if should_switch_line(record_name):
            hls_url = stream_info.get('record_url')
            if hls_url:
                logger.warning(f"{record_name} 上次取到的直播流无有效画面，"
                               f"本次临时改用 HLS 线路获取地址")
                return hls_url
            logger.warning(f"{record_name} 没有可用的备用线路地址，仍使用 FLV 线路")
        codec = utils.get_query_params(stream_info.get('flv_url'), "codec")
        if codec and codec[0] == 'h265':
            logger.warning("FLV is not supported for h265 codec, use HLS source instead")
        else:
            return stream_info.get('flv_url')

    return stream_info.get('record_url')


def start_record(url_data: tuple, count_variable: int = -1) -> None:
    global error_count
    global default_path
    global folder_by_author
    global folder_by_time
    global folder_by_title
    global filename_by_title
    global last_ffmpeg_return_code

    while True:
        try:
            record_finished = False
            run_once = False
            start_pushed = False
            new_record_url = ''
            count_time = time.time()
            retry = 0
            record_quality_zh, record_url, anchor_name = url_data
            record_quality = get_quality_code(record_quality_zh)
            platform = '抖音直播'
            live_domain = '/'.join(record_url.split('/')[0:3])

            while True:
                try:
                    port_info = []
                    if record_url.find("douyin.com/") > -1:
                        platform = '抖音直播'
                        # 让"同一时间访问网络的线程数"只限制并发的网络请求；
                        # 加超时是为了万一某个请求卡死，也不会永久占着名额，
                        # 导致后面的主播一直轮不到、只能录前面几个。
                        current_semaphore = semaphore
                        semaphore_acquired = current_semaphore.acquire(timeout=120)
                        try:
                            if 'v.douyin.com' not in record_url and '/user/' not in record_url:
                                json_data = asyncio.run(spider.get_douyin_web_stream_data(
                                    url=record_url,
                                    proxy_addr=None,
                                    cookies=dy_cookie))
                            else:
                                json_data = asyncio.run(spider.get_douyin_app_stream_data(
                                    url=record_url,
                                    proxy_addr=None,
                                    cookies=dy_cookie))
                            port_info = asyncio.run(
                                stream.get_douyin_stream_url(json_data, record_quality, None))
                        finally:
                            if semaphore_acquired:
                                current_semaphore.release()

                    if anchor_name:
                        if '主播:' in anchor_name:
                            anchor_split: list = anchor_name.split('主播:')
                            if len(anchor_split) > 1 and anchor_split[1].strip():
                                anchor_name = anchor_split[1].strip()
                            else:
                                anchor_name = port_info.get("anchor_name", '')
                    else:
                        anchor_name = port_info.get("anchor_name", '')

                    if not port_info.get("anchor_name", ''):
                        print(f'序号{count_variable} 网址内容获取失败,进行重试中...获取失败的地址是:{url_data}')
                        with max_request_lock:
                            error_count += 1
                            error_window.append(1)
                    else:
                        anchor_name = clean_name(anchor_name)
                        record_name = f'序号{count_variable} {anchor_name}'

                        if record_url in url_comments:
                            print(f"[{anchor_name}]已被注释,本条线程将会退出")
                            clear_record_info(record_name, record_url, record_quality_zh)
                            return

                        if not url_data[-1] and run_once is False:
                            if new_record_url:
                                need_update_line_list.append(
                                    f'{record_url}|{new_record_url},主播: {anchor_name.strip()}')
                                not_record_list.append(new_record_url)
                            else:
                                need_update_line_list.append(f'{record_url}|{record_url},主播: {anchor_name.strip()}')
                            run_once = True

                        push_at = datetime.datetime.today().strftime('%Y-%m-%d %H:%M:%S')
                        current_is_live = port_info['is_live']
                        
                        # 检测直播状态变化，状态变化时输出明确信息（不刷屏）
                        with last_live_status_lock:
                            prev_status = last_live_status.get(record_name, None)
                            if prev_status is None or prev_status != current_is_live:
                                # 状态变化了，输出明确信息
                                if current_is_live:
                                    status_msg = f"✅ {record_name} 主播正在直播中，开始录制..."
                                else:
                                    status_msg = f"⏳ {record_name} 主播未开播，等待直播..."
                                print(status_msg)
                                logger.info(status_msg)
                                last_live_status[record_name] = current_is_live

                        # 同步运行状态（供桌面端状态看板读取）
                        with runtime_status_lock:
                            meta_item = runtime_meta.setdefault(record_name, {})
                            meta_item['anchor'] = anchor_name
                            meta_item['quality'] = record_quality_zh
                            meta_item['is_live'] = bool(current_is_live)
                        
                        if not current_is_live:
                            # 主播下播了，下次开播重新开始一套分段编号
                            clear_segment_series(record_name)
                            clear_stream_health(record_name)
                            # 看板数字只反映“本次录制”，下播后清零，避免把目录里的旧视频算进去
                            with runtime_status_lock:
                                old_meta = runtime_meta.get(record_name)
                                if old_meta:
                                    for key in ('save_dir', 'prefix', 'started_at', 'live_type'):
                                        old_meta.pop(key, None)
                            print(f"\r{record_name} 等待直播... ")
                        else:
                            print(f"\r{record_name} 正在直播中...")

                            if disable_record:
                                time.sleep(push_check_seconds)
                                continue

                            real_url = select_source_url(record_url, port_info, record_name)
                            stream_info = (json_data or {}).get('stream_url') or {}
                            # 记下房间方向标记：收尾时判断“录到横屏”是不是异常要用
                            try:
                                record_orientation[record_name] = stream_info.get('stream_orientation')
                            except Exception:
                                pass
                            # 只记录实际选中的档位，不做任何干预（取流逻辑保持原样）
                            source_message = (f"{record_name} 选用直播源档位: {describe_source_url(real_url)}"
                                              f"（配置要求: {record_quality_zh}，"
                                              f"方向标记: {stream_info.get('stream_orientation')}）")
                            logger.info(source_message)
                            print(source_message)
                            full_path = f'{default_path}/{platform}'
                            if real_url:
                                now = datetime.datetime.today().strftime("%Y-%m-%d_%H-%M-%S")
                                live_title = port_info.get('title')
                                title_in_name = ''
                                if live_title:
                                    live_title = clean_name(live_title)
                                    title_in_name = live_title + '_' if filename_by_title else ''
                                # 同一个直播间挂了两条不同画质时（例如「原画 + 超清」各录一份做对比），
                                # 文件名里带上画质：两条同时开录也不会生成同名文件互相覆盖。
                                # 只挂一条时不加，保持原来的命名习惯。
                                if len(url_quality_map.get(record_url, ())) > 1:
                                    title_in_name = f"{record_quality_zh}_" + title_in_name

                                try:
                                    # 按「根目录 / 抖音直播 / 主播 / 时间 / 标题」构建完整路径
                                    # 根目录即使是盘根（F:\），也会在其下建子目录，不会把文件直接堆在盘根上
                                    by_title_flag = bool(folder_by_title and port_info.get('title'))
                                    full_path = build_record_dir(
                                        default_path, platform, anchor_name,
                                        live_time=now, live_title=live_title,
                                        by_author=folder_by_author, by_time=folder_by_time, by_title=by_title_flag,
                                    )
                                    
                                    # 确保目录真的能创建、能写入（用真实写入探测，不用 Windows 上不可靠的 os.access）
                                    fallback_record_dir = build_record_dir(
                                        default_save_root, platform, anchor_name,
                                        live_time=now, live_title=live_title,
                                        by_author=folder_by_author, by_time=folder_by_time, by_title=by_title_flag,
                                    )
                                    if not os.path.isdir(full_path):
                                        print(f"创建目录: {full_path}")
                                    usable_dir, used_fallback, dir_err = ensure_record_dir(full_path, fallback_record_dir)
                                    if not usable_dir:
                                        logger.error(f"录制目录创建失败，本次跳过: {dir_err}")
                                        print(f"❌ 录制目录创建失败（{dir_err}），本次跳过，稍后自动重试")
                                        with runtime_status_lock:
                                            meta_item = runtime_meta.setdefault(record_name, {})
                                            meta_item['save_dir'] = ''
                                            meta_item['save_error'] = dir_err
                                        time.sleep(delay_default)
                                        continue
                                    if used_fallback:
                                        print(f"⚠️ 自定义保存路径不可用，已改用备用目录: {usable_dir}")
                                    full_path = usable_dir
                                    print(f"保存路径: {full_path}")

                                    # 记录保存目录与文件名前缀，供状态看板统计文件数量与大小
                                    with runtime_status_lock:
                                        meta_item = runtime_meta.setdefault(record_name, {})
                                        meta_item['save_dir'] = full_path
                                        meta_item['prefix'] = f'{anchor_name}_{title_in_name}'
                                        # 本次开录时刻：看板只统计这之后的文件，旧视频不算进去
                                        meta_item['started_at'] = time.time()
                                        meta_item.pop('save_error', None)
                                except Exception as e:
                                    logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")

                                if platform != '自定义录制直播':
                                    if enable_https_recording and real_url.startswith("http://"):
                                        real_url = real_url.replace("http://", "https://")

                                user_agent = ("Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 ("
                                              "KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile "
                                              "Safari/537.36")

                                rw_timeout = "30000000"  # 读写超时30秒（原15秒）
                                analyzeduration = "30000000"  # 流分析时长30秒（原20秒）
                                probesize = "20000000"  # 探测大小20MB（原10MB）
                                bufsize = "8000k"
                                max_muxing_queue_size = "1024"

                                ffmpeg_command = [
                                    'ffmpeg', "-y",
                                    "-v", "verbose",
                                    "-timeout", "30000000",  # HTTP超时30秒
                                    "-rw_timeout", rw_timeout,
                                    "-loglevel", "error",
                                    "-hide_banner",
                                    "-user_agent", user_agent,
                                    "-protocol_whitelist", "rtmp,crypto,file,http,https,tcp,tls,udp,rtp",
                                    # ⚠ -thread_queue_size 不要写在这里！
                                    # 新版 FFmpeg 已把它归到「复用器（输出）侧」，
                                    # 写在 -i 之前会被直接判错：
                                    #   Option thread_queue_size ... cannot be applied to input url
                                    # 它现在统一写在下面 -i real_url 之后的输出参数段里。
                                    "-analyzeduration", analyzeduration,
                                    "-probesize", probesize,
                                    "-fflags", "+discardcorrupt+genpts",
                                    "-reconnect", "1",
                                    "-reconnect_streamed", "1",
                                    "-reconnect_at_eof", "1",
                                    "-reconnect_on_network_error", "1",  # 网络错误时重连
                                    "-reconnect_on_http_error", "4xx,5xx",  # HTTP错误时重连
                                    "-reconnect_delay_max", "30",
                                    "-i", real_url,
                                    "-bufsize", bufsize,
                                    "-sn", "-dn",
                                    # 复用器侧数据包队列：录制过程中磁盘/网络瞬时抖动时
                                    # 用来吸收积压，避免丢包导致分段损坏。
                                    # 旧版 FFmpeg（libavformat 61 / ffmpeg 7.x）认为它是解复用器
                                    # 选项，但放在 -i 之后同样接受；新版（libavformat 63 /
                                    # ffmpeg 9.x）只认输出侧。所以这一份命令两代通吃。
                                    "-thread_queue_size", "1024",
                                    "-max_muxing_queue_size", max_muxing_queue_size,
                                    "-correct_ts_overflow", "1",
                                    "-avoid_negative_ts", "1"
                                ]

                                headers = get_record_headers(platform, record_url)
                                if headers:
                                    ffmpeg_command.insert(11, "-headers")
                                    ffmpeg_command.insert(12, headers)

                                # ===== 录前探测：先看清这次到底取到了什么流 =====
                                # 目的不是拦截录制，而是把“真实地址 + 容器 + 编码”留在日志里。
                                # 下次再出现“录出来打不开”，一眼就能判断是不是取流环节拿错了东西。
                                if probe_source_before_record:
                                    try:
                                        _probe = probe_stream(real_url, headers)
                                        _source_desc = describe_source_url(real_url)
                                        probe_message = (f"{platform} | {anchor_name} | "
                                                         f"录前探测: {describe_stream_probe(_probe)}")
                                        logger.info(probe_message)
                                        print(probe_message)
                                        logger.info(f"{platform} | {anchor_name} | 直播源地址: {real_url}")
                                        print(f"{platform} | {anchor_name} | 直播源地址: {real_url}")
                                        if should_switch_line(record_name):
                                            # 刚刚因为坏流换过线路，这一轮的结果标注成"已换线路"
                                            set_stream_health(
                                                record_name, HEALTH_SWITCHED,
                                                f"已换线路重试（{describe_stream_probe(_probe)}）",
                                                source=_source_desc)
                                        elif not _probe.get('format'):
                                            # 没能读到流信息（防盗链拦截、网络抖动等）只做记录。
                                            # 这种情况录制本身大概率仍然正常，不要吓人。
                                            set_stream_health(
                                                record_name, HEALTH_UNKNOWN,
                                                f"录前探测未读到流信息："
                                                f"{_probe.get('error') or '无返回'}",
                                                source=_source_desc)
                                            logger.info(f"录前探测未能读取流信息："
                                                        f"{_probe.get('error') or '无返回'}")
                                        elif not _probe.get('ok'):
                                            probe_warn = (f"⚠ {anchor_name} 录前探测显示这次拿到的不是"
                                                          f"正常直播容器（{describe_stream_probe(_probe)}），"
                                                          f"这种流很可能录不到画面")
                                            logger.warning(probe_warn)
                                            print(probe_warn)
                                            set_stream_health(
                                                record_name, HEALTH_WARN,
                                                f"录前探测异常：{describe_stream_probe(_probe)}",
                                                source=_source_desc)
                                        else:
                                            set_stream_health(
                                                record_name, HEALTH_GOOD,
                                                f"录前探测正常：{describe_stream_probe(_probe)}",
                                                source=_source_desc)
                                    except Exception as probe_err:
                                        logger.debug(f"录前探测异常: {probe_err}")
                                elif not should_switch_line(record_name):
                                    set_stream_health(record_name, HEALTH_UNKNOWN,
                                                      "未开启录前探测，等待录制中体检",
                                                      source=describe_source_url(real_url))

                                recording.add(record_name)
                                start_record_time = datetime.datetime.now()
                                recording_time_list[record_name] = [start_record_time, record_quality_zh]
                                rec_info = f"\r{anchor_name} 准备开始录制视频: {full_path}"
                                if show_url:
                                    logger.info(
                                        f"{platform} | {anchor_name} | 直播源地址: {real_url}")

                                only_flv_record = False
                                only_audio_record = False

                                record_save_type = video_save_type

                                if is_flv_preferred_platform(record_url) and port_info.get('flv_url'):
                                    codec = utils.get_query_params(port_info['flv_url'], "codec")
                                    if codec and codec[0] == 'h265':
                                        logger.warning("FLV is not supported for h265 codec, use TS format instead")
                                        record_save_type = "TS"

                                if only_audio_record or any(i in record_save_type for i in ['MP3', 'M4A']):
                                    try:
                                        now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                        extension = "mp3" if "m4a" not in record_save_type.lower() else "m4a"
                                        name_format = "_%03d" if split_video_by_time else ""
                                        save_file_path = os.path.join(full_path, f'{anchor_name}_{title_in_name}{now}{name_format}.{extension}')

                                        if split_video_by_time:
                                            print(f'\r{anchor_name} 准备开始录制音频: {save_file_path}')

                                            if "MP3" in record_save_type:
                                                command = [
                                                    "-map", "0:a",
                                                    "-c:a", "libmp3lame",
                                                    "-ab", "320k",
                                                    "-f", "segment",
                                                    "-segment_time", split_time,
                                                    "-reset_timestamps", "1",
                                                    save_file_path,
                                                ]
                                            else:
                                                command = [
                                                    "-map", "0:a",
                                                    "-c:a", "aac",
                                                    "-bsf:a", "aac_adtstoasc",
                                                    "-ab", "320k",
                                                    "-f", "segment",
                                                    "-segment_time", split_time,
                                                    "-segment_format", 'mpegts',
                                                    "-segment_start_number", "0",
                                                    "-reset_timestamps", "1",
                                                    save_file_path,
                                                ]

                                        else:
                                            if "MP3" in record_save_type:
                                                command = [
                                                    "-map", "0:a",
                                                    "-c:a", "libmp3lame",
                                                    "-ab", "320k",
                                                    save_file_path,
                                                ]

                                            else:
                                                command = [
                                                    "-map", "0:a",
                                                    "-c:a", "aac",
                                                    "-bsf:a", "aac_adtstoasc",
                                                    "-ab", "320k",
                                                    "-movflags", "+faststart",
                                                    save_file_path,
                                                ]

                                        ffmpeg_command.extend(command)
                                        comment_end = check_subprocess(
                                            record_name,
                                            record_url,
                                            ffmpeg_command,
                                            record_save_type,
                                            custom_script
                                        )
                                        if comment_end:
                                            return

                                    except subprocess.CalledProcessError as e:
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                if only_flv_record:
                                    logger.info(f"Use Direct Downloader to Download FLV Stream: {record_url}")
                                    filename = anchor_name + f'_{title_in_name}' + now + '.flv'
                                    save_file_path = os.path.join(full_path, filename)
                                    print(f'{rec_info}/{filename}')

                                    subs_file_path = save_file_path.rsplit('.', maxsplit=1)[0]
                                    subs_thread_name = f'subs_{Path(subs_file_path).name}'
                                    if create_time_file:
                                        create_var[subs_thread_name] = threading.Thread(
                                            target=generate_subtitles, args=(record_name, subs_file_path)
                                        )
                                        create_var[subs_thread_name].daemon = True
                                        create_var[subs_thread_name].start()

                                    try:
                                        flv_url = port_info.get('flv_url')
                                        if flv_url:
                                            recording.add(record_name)
                                            start_record_time = datetime.datetime.now()
                                            recording_time_list[record_name] = [start_record_time, record_quality_zh]

                                            download_success = direct_download_stream(
                                                flv_url, save_file_path, record_name, record_url,
                                                platform, record_quality_zh
                                            )

                                            if download_success:
                                                record_finished = True
                                                print(
                                                    f"\n{anchor_name} {time.strftime('%Y-%m-%d %H:%M:%S')} 直播录制完成\n")

                                            recording.discard(record_name)
                                        else:
                                            logger.debug("未找到FLV直播流，跳过录制")
                                    except Exception as e:
                                        clear_record_info(record_name, record_url, record_quality_zh)
                                        color_obj.print_colored(
                                            f"\n{anchor_name} {time.strftime('%Y-%m-%d %H:%M:%S')} 直播录制出错,请检查网络\n",
                                            color_obj.RED)
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                elif record_save_type == "FLV":
                                    filename = anchor_name + f'_{title_in_name}' + now + ".flv"
                                    print(f'{rec_info}/{filename}')
                                    save_file_path = os.path.join(full_path, filename)

                                    try:
                                        if split_video_by_time:
                                            now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                            save_file_path = os.path.join(full_path, f'{anchor_name}_{title_in_name}{now}_%03d.flv')
                                            command = [
                                                "-map", "0",
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-bsf:a", "aac_adtstoasc",
                                                "-f", "segment",
                                                "-segment_time", split_time,
                                                "-segment_format", "flv",
                                                "-segment_start_number", "0",
                                                "-reset_timestamps", "1",
                                                save_file_path
                                            ]

                                        else:
                                            command = [
                                                "-map", "0",
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-bsf:a", "aac_adtstoasc",
                                                "-f", "flv",
                                                "{path}".format(path=save_file_path),
                                            ]
                                        ffmpeg_command.extend(command)

                                        comment_end = check_subprocess(
                                            record_name,
                                            record_url,
                                            ffmpeg_command,
                                            record_save_type,
                                            custom_script,
                                            split_time=split_time if split_video_by_time else None
                                        )
                                        if comment_end:
                                            return

                                    except subprocess.CalledProcessError as e:
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                    try:
                                        if converts_to_mp4:
                                            seg_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.mp4"
                                            if split_video_by_time:
                                                segment_video(
                                                    save_file_path, seg_file_path,
                                                    segment_format='mp4', segment_time=split_time,
                                                    is_original_delete=delete_origin_file
                                                )
                                            else:
                                                threading.Thread(
                                                    target=converts_mp4,
                                                    args=(save_file_path, delete_origin_file)
                                                ).start()

                                        else:
                                            seg_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.flv"
                                            if split_video_by_time:
                                                segment_video(
                                                    save_file_path, seg_file_path,
                                                    segment_format='flv', segment_time=split_time,
                                                    is_original_delete=delete_origin_file
                                                )
                                    except Exception as e:
                                        logger.error(f"转码失败: {e} ")

                                elif record_save_type == "MKV":
                                    filename = anchor_name + f'_{title_in_name}' + now + ".mkv"
                                    print(f'{rec_info}/{filename}')
                                    save_file_path = os.path.join(full_path, filename)

                                    try:
                                        if split_video_by_time:
                                            now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                            save_file_path = os.path.join(full_path, f'{anchor_name}_{title_in_name}{now}_%03d.mkv')
                                            command = [
                                                "-flags", "global_header",
                                                "-c:v", "copy",
                                                "-c:a", "aac",
                                                "-map", "0",
                                                "-f", "segment",
                                                "-segment_time", split_time,
                                                "-segment_format", "matroska",
                                                "-segment_start_number", "0",
                                                "-reset_timestamps", "1",
                                                save_file_path,
                                            ]

                                        else:
                                            command = [
                                                "-flags", "global_header",
                                                "-map", "0",
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-f", "matroska",
                                                "{path}".format(path=save_file_path),
                                            ]
                                        ffmpeg_command.extend(command)

                                        comment_end = check_subprocess(
                                            record_name,
                                            record_url,
                                            ffmpeg_command,
                                            record_save_type,
                                            custom_script,
                                            split_time=split_time if split_video_by_time else None
                                        )
                                        if comment_end:
                                            return

                                    except subprocess.CalledProcessError as e:
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                elif record_save_type == "MP4":
                                    filename = anchor_name + f'_{title_in_name}' + now + ".mp4"
                                    print(f'{rec_info}/{filename}')
                                    save_file_path = os.path.join(full_path, filename)

                                    try:
                                        if split_video_by_time:
                                            now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                            save_file_path = os.path.join(full_path, f'{anchor_name}_{title_in_name}{now}_%03d.mp4')
                                            command = [
                                                "-c:v", "copy",
                                                "-c:a", "aac",
                                                "-map", "0",
                                                "-f", "segment",
                                                "-segment_time", split_time,
                                                "-segment_format", "mp4",
                                                "-segment_format_options", "movflags=+faststart",
                                                "-segment_start_number", "0",
                                                "-reset_timestamps", "1",
                                                "-movflags", "+frag_keyframe+empty_moov",
                                                save_file_path,
                                            ]

                                        else:
                                            command = [
                                                "-map", "0",
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-f", "mp4",
                                                save_file_path,
                                            ]

                                        ffmpeg_command.extend(command)
                                        comment_end = check_subprocess(
                                            record_name,
                                            record_url,
                                            ffmpeg_command,
                                            record_save_type,
                                            custom_script,
                                            split_time=split_time if split_video_by_time else None
                                        )
                                        if comment_end:
                                            return

                                    except subprocess.CalledProcessError as e:
                                        logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                        with max_request_lock:
                                            error_count += 1
                                            error_window.append(1)

                                else:
                                    if split_video_by_time:
                                        try:
                                            # 同一主播同一次直播内的分段共用前缀、编号连续
                                            series_prefix = get_segment_series_prefix(record_name, anchor_name, title_in_name)
                                            logger.info(f"{record_name} 开始分段录制：分段时长 {split_time} 秒，"
                                                        f"文件名 {series_prefix}_000.ts 起")
                                            base_command = list(ffmpeg_command)

                                            while True:
                                                # 用磁盘上已有的分段序号往后排，避免覆盖已经录好的分段
                                                start_index = next_segment_index(full_path, series_prefix)
                                                save_file_path = f"{full_path}/{series_prefix}_%03d.ts"
                                                print(f'{rec_info}/{series_prefix}_{start_index:03d}.ts')

                                                command = [
                                                    "-c:v", "copy",
                                                    "-c:a", "copy",
                                                    "-map", "0",
                                                    "-f", "segment",
                                                    "-segment_time", split_time,
                                                    "-segment_format", 'mpegts',
                                                    "-segment_format_options", "mpegts_flags=resend_headers",
                                                    "-segment_start_number", str(start_index),
                                                    save_file_path,
                                                ]

                                                ffmpeg_command = base_command + command
                                                segment_run_start = time.time()
                                                comment_end = check_subprocess(
                                                    record_name,
                                                    record_url,
                                                    ffmpeg_command,
                                                    record_save_type,
                                                    custom_script,
                                                    split_time=split_time,
                                                    segment_start_index=start_index
                                                )
                                                # 本次 ffmpeg 实际跑了多久（墙钟时间，不受直播流时间戳影响）
                                                segment_run_elapsed = time.time() - segment_run_start

                                                # 本次录制新产生的分段文件
                                                split_files = segment_files_of_series(full_path, series_prefix, start_index)
                                                if split_files:
                                                    logger.info(f"{record_name} 本次录制完成 "
                                                                f"{len(split_files)} 个分段（最后一段 "
                                                                f"{series_prefix}_{start_index + len(split_files) - 1:03d}.ts）")
                                                    # 后台线程收尾（修复时间戳 + 转 mp4），不阻塞继续录制
                                                    start_post_process(split_files, delete_origin_file,
                                                                       tag=record_name)

                                                if comment_end:
                                                    return
                                                # 体检判定“这次取到的流没有有效画面”：立刻换线路重连，
                                                # 不要傻等到下一个循环时间
                                                if take_line_switch_request(record_name):
                                                    switch_msg = (f"{record_name} 本次取到的直播流没有有效画面，"
                                                                  f"已放弃本段，换线路重新获取地址后重试")
                                                    logger.warning(switch_msg)
                                                    print(f"\n{switch_msg}\n")
                                                    # 看板显示"已换线路"而不是一直挂着"坏流"，避免让人以为已经放弃
                                                    set_stream_health(record_name, HEALTH_SWITCHED,
                                                                      "已放弃空壳段，正在换线路重试")
                                                    mark_quick_retry(record_name)
                                                    break
                                                if last_ffmpeg_return_code != 0:
                                                    # 录制出错（网络/直播流异常），交给外层重新获取地址后重试
                                                    logger.warning(f"{record_name} 分段录制中断，稍后重新开始录制")
                                                    if get_stream_health_status(record_name) not in (HEALTH_BAD, HEALTH_SWITCHED):
                                                        set_stream_health(
                                                            record_name, HEALTH_WARN,
                                                            f"本次录制中断（退出码 {last_ffmpeg_return_code}），正在重连")
                                                    break
                                                if not split_files:
                                                    break
                                                # 最后一段明显短于设置的分段时长，说明直播已经结束或中断。
                                                # 注意：直播流时间戳异常时 ffprobe 读出的时长会不准，
                                                # 所以同时看“本次实际录了多久”，避免误判成直播结束、
                                                # 白等一个循环时间才重新开始录。
                                                if get_video_duration(split_files[-1]) < float(split_time) * 0.8 \
                                                        and segment_run_elapsed < float(split_time) * 0.8:
                                                    break
                                                # 直播还在继续：立即开始下一段（不再等待循环时间）

                                        except subprocess.CalledProcessError as e:
                                            logger.error(
                                                f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                            with max_request_lock:
                                                error_count += 1
                                                error_window.append(1)

                                    else:
                                        filename = anchor_name + f'_{title_in_name}' + now + ".ts"
                                        print(f'{rec_info}/{filename}')
                                        save_file_path = os.path.join(full_path, filename)

                                        try:
                                            command = [
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-map", "0",
                                                "-f", "mpegts",
                                                save_file_path,
                                            ]

                                            ffmpeg_command.extend(command)
                                            comment_end = check_subprocess(
                                                record_name,
                                                record_url,
                                                ffmpeg_command,
                                                record_save_type,
                                                custom_script
                                            )
                                            if comment_end:
                                                threading.Thread(
                                                    target=converts_mp4, args=(save_file_path, delete_origin_file)
                                                ).start()
                                                return

                                        except subprocess.CalledProcessError as e:
                                            logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                                            with max_request_lock:
                                                error_count += 1
                                                error_window.append(1)

                                count_time = time.time()

                except Exception as e:
                    logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
                    with max_request_lock:
                        error_count += 1
                        error_window.append(1)

                num = random.randint(-5, 5) + delay_default
                if num < 0:
                    num = 0
                x = num

                if error_count > 20:
                    x = x + 60
                    color_obj.print_colored("\r瞬时错误太多,延迟加60秒", color_obj.YELLOW)

                # 判定为坏流、换线路重连：5 秒就重试，不等一个完整循环时间
                if take_quick_retry(record_name):
                    x = 5
                    color_obj.print_colored(f"\r{anchor_name} 取流异常，换线路重连，5 秒后重试", color_obj.YELLOW)
                    last_ffmpeg_return_code = 0
                # ffmpeg异常退出时，快速重试（15秒），而不是等待默认的120秒
                elif last_ffmpeg_return_code != 0:
                    x = 15
                    color_obj.print_colored("\rffmpeg异常退出,15秒后快速重试", color_obj.YELLOW)
                    if get_stream_health_status(record_name) not in (HEALTH_BAD, HEALTH_SWITCHED):
                        set_stream_health(record_name, HEALTH_WARN,
                                          f"本次录制中断（退出码 {last_ffmpeg_return_code}），正在重连")
                    last_ffmpeg_return_code = 0  # 重置，避免影响下一次

                elif record_finished:
                    count_time_end = time.time() - count_time
                    if count_time_end < 60:
                        x = 30
                    record_finished = False

                else:
                    x = num

                while x:
                    x = x - 1
                    if loop_time:
                        print(f'\r{anchor_name}循环等待{x}秒 ', end="")
                    time.sleep(1)
                if loop_time:
                    print('\r检测直播间中...', end="")
        except Exception as e:
            logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
            with max_request_lock:
                error_count += 1
                error_window.append(1)
            time.sleep(2)


def backup_file(file_path: str, backup_dir_path: str, limit_counts: int = 6) -> None:
    try:
        if not os.path.exists(backup_dir_path):
            os.makedirs(backup_dir_path)

        timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        backup_file_name = os.path.basename(file_path) + '_' + timestamp
        backup_file_path = os.path.join(backup_dir_path, backup_file_name).replace("\\", "/")
        shutil.copy2(file_path, backup_file_path)

        files = os.listdir(backup_dir_path)
        _files = [f for f in files if f.startswith(os.path.basename(file_path))]
        _files.sort(key=lambda x: os.path.getmtime(os.path.join(backup_dir_path, x)))

        while len(_files) > limit_counts:
            oldest_file = _files[0]
            os.remove(os.path.join(backup_dir_path, oldest_file))
            _files = _files[1:]

    except Exception as e:
        logger.error(f'\r备份配置文件 {file_path} 失败：{str(e)}')


def backup_file_start() -> None:
    config_md5 = ''
    url_config_md5 = ''

    while True:
        try:
            if os.path.exists(config_file):
                new_config_md5 = utils.check_md5(config_file)
                if new_config_md5 != config_md5:
                    backup_file(config_file, backup_dir)
                    config_md5 = new_config_md5

            if os.path.exists(url_config_file):
                new_url_config_md5 = utils.check_md5(url_config_file)
                if new_url_config_md5 != url_config_md5:
                    backup_file(url_config_file, backup_dir)
                    url_config_md5 = new_url_config_md5
            time.sleep(600)
        except Exception as e:
            logger.error(f"备份配置文件失败, 错误信息: {e}")


def check_ffmpeg_existence() -> bool:
    try:
        result = subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            version_line = lines[0]
            built_line = lines[1]
            print(version_line)
            print(built_line)
    except subprocess.CalledProcessError as e:
        logger.error(e)
    except FileNotFoundError:
        pass
    finally:
        if check_ffmpeg():
            time.sleep(1)
            return True
    return False


print("-----------------------------------------------------")
print("|                DouyinLiveRecorder                 |")
print("-----------------------------------------------------")

print(f"版本号: {version}")
print("GitHub: https://github.com/ihmily/DouyinLiveRecorder")
print(f'支持平台: {platforms}')
print('.....................................................')
if not check_ffmpeg_existence():
    logger.error("缺少ffmpeg无法进行录制，程序退出")
    sys.exit(1)
os.makedirs(os.path.dirname(config_file), exist_ok=True)
t3 = threading.Thread(target=backup_file_start, args=(), daemon=True)
t3.start()
t4 = threading.Thread(target=write_runtime_status, args=(), daemon=True)
t4.start()
utils.remove_duplicate_lines(url_config_file)


def read_config_value(config_parser: configparser.RawConfigParser, section: str, option: str, default_value: Any) \
        -> Any:
    try:

        config_parser.read(config_file, encoding=text_encoding)
        if '录制设置' not in config_parser.sections():
            config_parser.add_section('录制设置')
        if '推送配置' not in config_parser.sections():
            config_parser.add_section('推送配置')
        if 'Cookie' not in config_parser.sections():
            config_parser.add_section('Cookie')
        return config_parser.get(section, option)
    except (configparser.NoSectionError, configparser.NoOptionError):
        config_parser.set(section, option, str(default_value))
        with open(config_file, 'w', encoding=text_encoding) as f:
            config_parser.write(f)
        return default_value


options = {"是": True, "否": False}
config = configparser.RawConfigParser()
language = read_config_value(config, '录制设置', 'language(zh_cn/en)', "zh_cn")
if language and 'en' not in language.lower():
    from i18n import translated_print

    builtins.print = translated_print

while True:

    try:
        if not os.path.isfile(config_file):
            with open(config_file, 'w', encoding=text_encoding) as file:
                pass

        ini_URL_content = ''
        if os.path.isfile(url_config_file):
            with open(url_config_file, 'r', encoding=text_encoding) as file:
                ini_URL_content = file.read().strip()

        # 如果URL_config.ini为空，不等待用户输入，直接继续
        # if not ini_URL_content.strip():
        #     input_url = input('请输入要录制的主播直播间网址（尽量使用PC网页端的直播间地址）:\n')
        #     with open(url_config_file, 'w', encoding=text_encoding) as file:
        #         file.write(input_url)
    except OSError as err:
        logger.error(f"发生 I/O 错误: {err}")

    folder_by_author = options.get(read_config_value(config, '录制设置', '保存文件夹是否以作者区分', "是"), False)
    folder_by_time = options.get(read_config_value(config, '录制设置', '保存文件夹是否以时间区分', "否"), False)
    folder_by_title = options.get(read_config_value(config, '录制设置', '保存文件夹是否以标题区分', "否"), False)
    filename_by_title = options.get(read_config_value(config, '录制设置', '保存文件名是否包含标题', "否"), False)
    clean_emoji = options.get(read_config_value(config, '录制设置', '是否去除名称中的表情符号', "是"), True)
    video_save_type = read_config_value(config, '录制设置', '视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', "ts")
    video_record_quality = read_config_value(config, '录制设置', '原画|超清|高清|标清|流畅', "原画")
    max_request = int(read_config_value(config, '录制设置', '同一时间访问网络的线程数', 3))
    semaphore = threading.Semaphore(max_request)
    delay_default = int(read_config_value(config, '录制设置', '循环时间(秒)', 120))
    local_delay_default = int(read_config_value(config, '录制设置', '排队读取网址时间(秒)', 0))
    loop_time = options.get(read_config_value(config, '录制设置', '是否显示循环秒数', "否"), False)
    show_url = options.get(read_config_value(config, '录制设置', '是否显示直播源地址', "否"), False)
    split_video_by_time = options.get(read_config_value(config, '录制设置', '分段录制是否开启', "否"), False)
    enable_https_recording = options.get(read_config_value(config, '录制设置', '是否强制启用https录制', "否"), False)
    disk_space_limit = float(read_config_value(config, '录制设置', '录制空间剩余阈值(gb)', 1.0))
    split_time = str(read_config_value(config, '录制设置', '视频分段时间(秒)', 1800))
    converts_to_mp4 = options.get(read_config_value(config, '录制设置', '录制完成后自动转为mp4格式', "否"), False)
    converts_to_h264 = options.get(read_config_value(config, '录制设置', 'mp4格式重新编码为h264', "否"), False)
    delete_origin_file = options.get(read_config_value(config, '录制设置', '追加格式后删除原文件', "否"), False)
    create_time_file = options.get(read_config_value(config, '录制设置', '生成时间字幕文件', "否"), False)
    is_run_script = options.get(read_config_value(config, '录制设置', '是否录制完成后执行自定义脚本', "否"), False)
    custom_script = read_config_value(config, '录制设置', '自定义脚本执行命令', "") if is_run_script else None
    disable_record = options.get(read_config_value(config, '推送配置', '只推送通知不录制(是/否)', "否"), False)
    push_check_seconds = int(read_config_value(config, '推送配置', '直播推送检测频率(秒)', 1800))
    dy_cookie = read_config_value(config, 'Cookie', '抖音cookie', '')

    # GUI 请求退出：等所有录制线程把当前分段收尾后，录制进程自己退出
    # （GUI 侧还有强杀兜底，这里退出是为了让 PyCharm/任务管理器里也能干净地结束）
    # 退出条件三件套：录制列表空了 + 没有正在跑的录制进程 + 没有还没做完的收尾任务。
    # 少任何一条都可能在收尾空档里提前退出，留下没转 mp4 的 .ts。
    if (os.path.exists(stop_signal_file) and not recording
            and not has_active_record_runs()):
        if has_pending_post_tasks():
            # 还有分段在修时间戳 / 转 mp4，必须等它们做完：
            # 提前退出会留下半截 mp4、或者一半 ts 一半 mp4
            logger.info("收到退出请求：正在等待分段收尾（修复时间戳 / 转 mp4）完成 ...")
        else:
            logger.info("收到退出请求，录制与收尾都已完成，程序退出")
            try:
                # 删掉停止请求 = 告诉 GUI“已经收尾完成，可以收了”
                os.remove(stop_signal_file)
            except OSError:
                pass
            sys.exit(0)

    # ===== 稳定性兜底：录前探测 / 录制中体检 / 坏流自动换线路 =====
    # 这三项用来对付"能被 ffmpeg 打开、但里面没有一帧有效画面"的坏流
    # （以前的表现是：录了几十分钟，文件一直在长，打开却报编码格式不支持）
    probe_source_before_record = options.get(
        read_config_value(config, '录制设置', '录前探测直播源(是/否)', "是"), True)
    stream_health_enabled = options.get(
        read_config_value(config, '录制设置', '直播流健康体检(是/否)', "是"), True)
    stream_auto_switch_line = options.get(
        read_config_value(config, '录制设置', '坏流自动切换线路(是/否)', "是"), True)
    try:
        stream_health_seconds = float(
            read_config_value(config, '录制设置', '坏流体检判定秒数', 60))
    except (TypeError, ValueError):
        stream_health_seconds = 60.0
    stream_health_seconds = max(15.0, stream_health_seconds)
    # 收尾时的“源流详细体检”（读抖音推流 SEI）：默认开，关掉就完全不跑，
    # 适合录制盘是机械盘、不想多读一遍文件的用户。
    source_health_detail = options.get(
        read_config_value(config, '录制设置', '源流详细体检(是/否)', "是"), True)

    # ===== 保存路径：让 config.ini 的「直播保存路径(不填则默认)」真正生效 =====
    # 留空            → 程序目录下的 downloads（默认行为，不变）
    # 填了盘根(F:\)   → 在盘根下建「抖音直播\<主播>\」，不会把文件直接堆在盘根上
    # 填了普通目录    → 在该目录下建「抖音直播\<主播>\」
    # 目录建不出来（盘符不存在 / 权限不足 / 只读）→ 自动回退到 downloads，并在日志写明原因
    fallback_path = default_save_root
    configured_save_path = str(
        read_config_value(config, '录制设置', '直播保存路径(不填则默认)', "") or ""
    ).strip()
    if configured_save_path and configured_save_path not in ("不填则默认", "默认", "default"):
        candidate_path = os.path.expandvars(os.path.expanduser(configured_save_path))
        if not os.path.isabs(candidate_path):
            candidate_path = os.path.join(script_path, candidate_path)
        candidate_path = os.path.normpath(candidate_path)
        # 真实写入探测（比 os.access 可靠），结果带缓存，不会每轮轮询都写盘
        ok_path, path_err = check_save_path(candidate_path)
        if ok_path:
            default_path = candidate_path
            if applied_save_path != candidate_path:
                applied_save_path = candidate_path
                logger.info(f"保存路径已切换为: {candidate_path}")
                if os.path.normcase(candidate_path) == os.path.normcase(default_save_root):
                    # 配置里写的就是默认路径（例如发版时预先填好的），不必说成"自定义"
                    print(f"使用默认保存路径: {candidate_path}")
                elif is_drive_root(candidate_path):
                    print(f"使用自定义保存路径: {candidate_path}（将在其下创建「抖音直播\\主播」目录）")
                else:
                    print(f"使用自定义保存路径: {candidate_path}")
        else:
            default_path = fallback_path
            if applied_save_path != fallback_path:
                applied_save_path = fallback_path
                logger.warning(f"保存路径不可用({configured_save_path}): {path_err}，已回退到默认路径 {fallback_path}")
                print(f"⚠️ 保存路径不可用（{configured_save_path}）: {path_err}")
                print(f"⚠️ 已自动回退到默认路径: {fallback_path}")
            os.makedirs(default_path, exist_ok=True)
    else:
        default_path = fallback_path
        os.makedirs(default_path, exist_ok=True)

    video_save_type_list = ("FLV", "MKV", "TS", "MP4", "MP3音频", "M4A音频", "MP3", "M4A")
    if video_save_type and video_save_type.upper() in video_save_type_list:
        video_save_type = video_save_type.upper()
    else:
        video_save_type = "TS"

    check_path = default_path
    if utils.check_disk_capacity(check_path, show=first_run) < disk_space_limit:
        exit_recording = True
        if not recording:
            logger.warning(f"Disk space remaining is below {disk_space_limit} GB. "
                           f"Exiting program due to the disk space limit being reached.")
            sys.exit(-1)


    def contains_url(string: str) -> bool:
        pattern = r"(https?://)?(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/.*)?"
        return re.search(pattern, string) is not None


    try:
        url_comments, line_list, url_line_list = [[] for _ in range(3)]
        # 每轮按当前配置重建：用户把重复的画质行删掉后，文件名不该还带着旧画质标记
        url_quality_map = {}
        # 抖音地址要去掉 ? 参数，但**不能边读边改文件**：
        # 规范化后两行文本可能撞车，而删除重复行是按“文本包含”匹配的，
        # 会把本该保留的那行一起删掉（实测过：超清那条整行消失）。
        # 所以先记下来，等这一轮读完再统一写回。
        pending_url_rewrites = []
        # 同一轮里同一行只允许删一次：否则“完全相同的两行”会被删两遍，
        # 连本该保留的那条一起没了（配置项凭空消失）
        deleted_origin_lines = set()

        def drop_origin_line(raw_line: str) -> None:
            if raw_line in deleted_origin_lines:
                return
            deleted_origin_lines.add(raw_line)
            delete_line(url_config_file, raw_line)

        with (open(url_config_file, "r", encoding=text_encoding, errors='ignore') as file):
            for origin_line in file:
                if origin_line in line_list:
                    drop_origin_line(origin_line)
                line_list.append(origin_line)
                line = origin_line.strip()
                if len(line) < 18:
                    continue

                line_spilt = line.split('主播: ')
                if len(line_spilt) > 2:
                    line = update_file(url_config_file, line, f'{line_spilt[0]}主播: {line_spilt[-1]}')

                is_comment_line = line.startswith("#")
                if is_comment_line:
                    line = line.lstrip('#')

                if re.search('[,，]', line):
                    split_line = re.split('[,，]', line)
                else:
                    split_line = [line, '']

                if len(split_line) == 1:
                    url = split_line[0]
                    quality, name = [video_record_quality, '']
                elif len(split_line) == 2:
                    if contains_url(split_line[0]):
                        quality = video_record_quality
                        url, name = split_line
                    else:
                        quality, url = split_line
                        name = ''
                else:
                    # 只认前三段（画质, 地址, 名字），名字里再带逗号也不会像以前那样
                    # 直接抛 "too many values to unpack" 把整轮配置解析打断
                    quality, url = split_line[0], split_line[1]
                    name = ','.join(split_line[2:])

                if quality not in ("原画", "蓝光", "超清", "高清", "标清", "流畅"):
                    quality = '原画'

                # 去重键 = 画质 + 地址：
                #   · 同一个直播间 + 同一个画质 → 重复，忽略并提示
                #   · 同一个直播间 + 不同画质 → 允许（例如「原画」和「超清」各录一份做对比）
                url = 'https://' + url if '://' not in url else url
                # 抖音地址后面会统一去掉 ? 参数（下面 update_file 会写回文件），
                # 所以去重时也按去掉参数的形态算，否则「带参数的同一地址」要等到下一轮才被发现
                dedupe_url = url
                try:
                    host = url.split('//')[-1].split('/')[0].lower()
                    if host in ('live.douyin.com', 'v.douyin.com', 'www.douyin.com'):
                        dedupe_url = url.split('?')[0]
                except Exception:
                    dedupe_url = url
                dedupe_key = f'{quality}|{dedupe_url}'
                if dedupe_key not in url_line_list:
                    # 只有第一次撞见“同一个直播间挂多个画质”时才记一行，
                    # 否则主循环每几秒重读一次配置，日志会被刷屏
                    if dedupe_url in url_line_list and dedupe_key not in multi_quality_logged:
                        multi_quality_logged.add(dedupe_key)
                        logger.info(f"同一个直播间挂了多个画质：{url} 现在按"
                                    f"「{quality}」再录一份（两份会分别保存）")
                    url_line_list.append(dedupe_url)
                    url_line_list.append(dedupe_key)
                else:
                    logger.warning(
                        f"⚠ 地址重复已忽略：{url} 已经用「{quality}」在录了。"
                        f"同一个直播间同一个画质重复添加没有意义；"
                        f"想同时录两个画质做对比，请把其中一行的画质改成别的"
                        f"（例如一行「原画」、一行「超清」）。")
                    drop_origin_line(origin_line)

                url_host = url.split('/')[2]

                platform_host = [
                    'live.douyin.com',
                    'v.douyin.com',
                    'www.douyin.com'
                ]

                if url_host in platform_host or any(ext in url for ext in (".flv", ".m3u8")):
                    if url_host in platform_host:
                        normalized_url = url.split('?')[0]
                        if normalized_url != url:
                            pending_url_rewrites.append((url, normalized_url))
                            url = normalized_url

                    url_comments = [i for i in url_comments if url not in i]
                    if is_comment_line:
                        url_comments.append(url)
                    else:
                        new_line = (quality, url, name)
                        url_tuples_list.append(new_line)
                        url_quality_map.setdefault(url, set()).add(quality)
                else:
                    if not origin_line.startswith('#'):
                        color_obj.print_colored(f"\r{origin_line.strip()} 本行包含未知链接.此条跳过", color_obj.YELLOW)
                        update_file(url_config_file, old_str=origin_line, new_str=origin_line, start_str='#')

        # 先把要规范化的地址写回文件（必须在补“主播: 名字”之前，
        # 否则补名字时匹配的是旧地址，会把参数拼到奇怪的位置）
        for old_str, new_str in pending_url_rewrites:
            try:
                update_file(url_config_file, old_str=old_str, new_str=new_str)
            except Exception as e:
                logger.debug(f"地址规范化写回失败: {e}")
        pending_url_rewrites = []

        while len(need_update_line_list):
            a = need_update_line_list.pop()
            replace_words = a.split('|')
            if replace_words[0] != replace_words[1]:
                if replace_words[1].startswith("#"):
                    start_with = '#'
                    new_word = replace_words[1][1:]
                else:
                    start_with = None
                    new_word = replace_words[1]
                update_file(url_config_file, old_str=replace_words[0], new_str=new_word, start_str=start_with)

        text_no_repeat_url = list(set(url_tuples_list))

        if len(text_no_repeat_url) > 0:
            for url_tuple in text_no_repeat_url:
                monitoring = len(running_list)

                if url_tuple[1] in not_record_list:
                    continue

                # 运行列表用 (画质, 地址) 做键：同一个直播间挂两条不同画质时，
                # 两条都要各自起一个录制线程（同时录、分别保存）。
                run_key = (url_tuple[0], url_tuple[1])
                if run_key not in running_list:
                    print(f"\r{'新增' if not first_start else '传入'}地址: {url_tuple[1]}")
                    monitoring += 1
                    args = [url_tuple, monitoring]
                    create_var[f'thread_{monitoring}'] = threading.Thread(target=start_record, args=args)
                    create_var[f'thread_{monitoring}'].daemon = True
                    create_var[f'thread_{monitoring}'].start()
                    running_list.append(run_key)
                    time.sleep(local_delay_default)
        url_tuples_list = []
        first_start = False

    except Exception as err:
        logger.error(f"错误信息: {err} 发生错误的行数: {err.__traceback__.tb_lineno}")

    if first_run:
        t = threading.Thread(target=display_info, args=(), daemon=True)
        t.start()
        t2 = threading.Thread(target=adjust_max_request, args=(), daemon=True)
        t2.start()
        first_run = False

    time.sleep(3)
