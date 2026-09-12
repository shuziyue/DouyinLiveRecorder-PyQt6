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
from ffmpeg_install import (
    check_ffmpeg, ffmpeg_path, current_env_path  # ffmpeg相关
)

# 版本信息
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
last_ffmpeg_return_code = 0  # 记录最后一次ffmpeg退出码，用于判断是否异常退出  # 录制时间列表

# 路径设置
script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]  # 脚本路径
config_file = f'{script_path}/config/config.ini'  # 配置文件路径
url_config_file = f'{script_path}/config/URL_config.ini'  # URL配置文件路径
backup_dir = f'{script_path}/backup_config'  # 备份目录
text_encoding = 'utf-8-sig'  # 文本编码
rstr = r"[\/\\:\*\？?\"\<\>\|&#.。,， ~！· ]"  # 文件名非法字符

# 默认下载路径
default_path = f'{script_path}/downloads'
os.makedirs(default_path, exist_ok=True)  # 创建下载目录
print(f"使用默认保存路径: {default_path}")

# 文件更新锁
file_update_lock = threading.Lock()

# 系统类型和清屏命令
os_type = os.name
clear_command = "cls" if os_type == 'nt' else "clear"

# 颜色对象
color_obj = utils.Color()

# 设置环境变量，添加ffmpeg路径
os.environ['PATH'] = ffmpeg_path + os.pathsep + current_env_path


def signal_handler(_signal, _frame):
    sys.exit(0)


try:
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, signal_handler)
except (ValueError, RuntimeError):
    pass


def display_info() -> None:
    global start_display_time
    time.sleep(5)
    while True:
        try:
            if sys.stdout is not None:
                sys.stdout.flush()
            time.sleep(5)
            # 打包环境(console=False)或pythonw.exe下跳过清屏，避免os.system启动cmd.exe导致窗口闪烁
            if Path(sys.executable).name != 'pythonw.exe' and not getattr(sys, 'frozen', False):
                os.system(clear_command)
            print(f"\r共监测{monitoring}个直播中", end=" | ")
            print(f"同一时间访问网络的线程数: {max_request}", end=" | ")
            if split_video_by_time:
                print(f"录制分段开启: {split_time}秒", end=" | ")
            else:
                print("录制分段开启: 否", end=" | ")
            if create_time_file:
                print("是否生成时间文件: 是", end=" | ")
            print(f"录制视频质量为: {video_record_quality}", end=" | ")
            print(f"录制视频格式为: {video_save_type}", end=" | ")
            print(f"目前瞬时错误数为: {error_count}", end=" | ")
            now = time.strftime("%H:%M:%S", time.localtime())
            print(f"当前时间: {now}")

            if len(recording) == 0:
                time.sleep(5)
                if monitoring == 0:
                    print("\r没有正在监测和录制的直播")
                else:
                    print(f"\r没有正在录制的直播 循环监测间隔时间：{delay_default}秒")
            else:
                now_time = datetime.datetime.now()
                print("x" * 60)
                no_repeat_recording = list(set(recording))
                print(f"正在录制{len(no_repeat_recording)}个直播: ")
                for recording_live in no_repeat_recording:
                    rt, qa = recording_time_list[recording_live]
                    have_record_time = now_time - rt
                    print(f"{recording_live}[{qa}] 正在录制中 {str(have_record_time).split('.')[0]}")

                print("x" * 60)
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
    try:
        if os.path.exists(converts_file_path) and os.path.getsize(converts_file_path) > 0:
            if converts_to_h264:
                color_obj.print_colored("正在转码为MP4格式并重新编码为h264\n", color_obj.YELLOW)
                ffmpeg_command = [
                    "ffmpeg", "-i", converts_file_path,
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
                    "ffmpeg", "-i", converts_file_path,
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
    except Exception as e:
        logger.error(f'An unknown error occurred: {e}')


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


def clear_record_info(record_name: str, record_url: str) -> None:
    global monitoring
    recording.discard(record_name)
    if record_url in url_comments and record_url in running_list:
        running_list.remove(record_url)
        monitoring -= 1
        color_obj.print_colored(f"[{record_name}]已经从录制列表中移除\n", color_obj.YELLOW)


def direct_download_stream(source_url: str, save_path: str, record_name: str, live_url: str, platform: str) -> bool:
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
                        clear_record_info(record_name, live_url)
                        return False

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                print()
                return True
    except Exception as e:
        logger.error(f"FLV下载错误: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")
        return False


def check_subprocess(record_name: str, record_url: str, ffmpeg_command: list, save_type: str,
                     script_command: str | None = None) -> bool:
    save_file_path = ffmpeg_command[-1]
    process = subprocess.Popen(
        ffmpeg_command, stdin=subprocess.PIPE, stderr=subprocess.STDOUT, startupinfo=get_startup_info(os_type)
    )

    subs_file_path = save_file_path.rsplit('.', maxsplit=1)[0]
    subs_thread_name = f'subs_{Path(subs_file_path).name}'
    if create_time_file and not split_video_by_time and '音频' not in save_type:
        create_var[subs_thread_name] = threading.Thread(
            target=generate_subtitles, args=(record_name, subs_file_path)
        )
        create_var[subs_thread_name].daemon = True
        create_var[subs_thread_name].start()

    while process.poll() is None:
        if record_url in url_comments or exit_recording:
            color_obj.print_colored(f"[{record_name}]录制时已被注释,本条线程将会退出", color_obj.YELLOW)
            clear_record_info(record_name, record_url)
            if os.name == 'nt':
                if process.stdin:
                    process.stdin.write(b'q')
                    process.stdin.close()
            else:
                process.send_signal(signal.SIGINT)
            process.wait()
            return True
        time.sleep(1)

    return_code = process.returncode
    stop_time = time.strftime('%Y-%m-%d %H:%M:%S')
    if return_code == 0:
        if converts_to_mp4 and save_type == 'TS':
            if split_video_by_time:
                file_paths = utils.get_file_paths(os.path.dirname(save_file_path))
                prefix = os.path.basename(save_file_path).rsplit('_', maxsplit=1)[0]
                for path in file_paths:
                    if prefix in path:
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
        color_obj.print_colored(f"\n{record_name} {stop_time} 直播录制出错,返回码: {return_code},将快速自动重试\n", color_obj.RED)

    global last_ffmpeg_return_code
    last_ffmpeg_return_code = return_code

    recording.discard(record_name)
    return False


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
    record_headers = {
        'Douyin': 'origin:https://www.douyin.com'
    }
    return record_headers.get(platform)


def is_flv_preferred_platform(link):
    return any(i in link for i in ["douyin", "tiktok"])


def select_source_url(link, stream_info):
    if is_flv_preferred_platform(link):
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
                        with semaphore:
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
                            clear_record_info(record_name, record_url)
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
                        if port_info['is_live'] is False:
                            print(f"\r{record_name} 等待直播... ")

                        else:
                            content = f"\r{record_name} 正在直播中..."
                            print(content)

                            if disable_record:
                                time.sleep(push_check_seconds)
                                continue

                            real_url = select_source_url(record_url, port_info)
                            full_path = f'{default_path}/{platform}'
                            if real_url:
                                now = datetime.datetime.today().strftime("%Y-%m-%d_%H-%M-%S")
                                live_title = port_info.get('title')
                                title_in_name = ''
                                if live_title:
                                    live_title = clean_name(live_title)
                                    title_in_name = live_title + '_' if filename_by_title else ''

                                try:
                                    # 始终使用默认路径
                                    base_path = os.path.join(default_path, platform)

                                    # 构建完整路径（包含主播、时间、标题等）
                                    full_path = base_path
                                    if folder_by_author:
                                        full_path = os.path.join(full_path, anchor_name)
                                    if folder_by_time:
                                        full_path = os.path.join(full_path, now[:10])
                                    if folder_by_title and port_info.get('title'):
                                        if folder_by_time:
                                            full_path = os.path.join(full_path, f'{live_title}_{anchor_name}')
                                        else:
                                            full_path = os.path.join(full_path, f'{now[:10]}_{live_title}')
                                    
                                    # 确保路径存在
                                    if not os.path.exists(full_path):
                                        print(f"创建目录: {full_path}")
                                        os.makedirs(full_path, exist_ok=True)
                                    print(f"保存路径: {full_path}")
                                except Exception as e:
                                    logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")

                                if platform != '自定义录制直播':
                                    if enable_https_recording and real_url.startswith("http://"):
                                        real_url = real_url.replace("http://", "https://")

                                user_agent = ("Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 ("
                                              "KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile "
                                              "Safari/537.36")

                                rw_timeout = "15000000"
                                analyzeduration = "20000000"
                                probesize = "10000000"
                                bufsize = "8000k"
                                max_muxing_queue_size = "1024"

                                ffmpeg_command = [
                                    'ffmpeg', "-y",
                                    "-v", "verbose",
                                    "-rw_timeout", rw_timeout,
                                    "-loglevel", "error",
                                    "-hide_banner",
                                    "-user_agent", user_agent,
                                    "-protocol_whitelist", "rtmp,crypto,file,http,https,tcp,tls,udp,rtp",
                                    "-thread_queue_size", "1024",
                                    "-analyzeduration", analyzeduration,
                                    "-probesize", probesize,
                                    "-fflags", "+discardcorrupt",
                                    "-reconnect", "1",
                                    "-reconnect_streamed", "1",
                                    "-reconnect_at_eof", "1",
                                    "-reconnect_delay_max", "30",
                                    "-i", real_url,
                                    "-bufsize", bufsize,
                                    "-sn", "-dn",
                                    "-max_muxing_queue_size", max_muxing_queue_size,
                                    "-correct_ts_overflow", "1",
                                    "-avoid_negative_ts", "1"
                                ]

                                headers = get_record_headers(platform, record_url)
                                if headers:
                                    ffmpeg_command.insert(11, "-headers")
                                    ffmpeg_command.insert(12, headers)

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
                                                flv_url, save_file_path, record_name, record_url, platform
                                            )

                                            if download_success:
                                                record_finished = True
                                                print(
                                                    f"\n{anchor_name} {time.strftime('%Y-%m-%d %H:%M:%S')} 直播录制完成\n")

                                            recording.discard(record_name)
                                        else:
                                            logger.debug("未找到FLV直播流，跳过录制")
                                    except Exception as e:
                                        clear_record_info(record_name, record_url)
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
                                            custom_script
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
                                            custom_script
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
                                            custom_script
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
                                        now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                                        filename = anchor_name + f'_{title_in_name}' + now + ".ts"
                                        print(f'{rec_info}/{filename}')

                                        try:
                                            save_file_path = f"{full_path}/{anchor_name}_{title_in_name}{now}_%03d.ts"
                                            command = [
                                                "-c:v", "copy",
                                                "-c:a", "copy",
                                                "-map", "0",
                                                "-f", "segment",
                                                "-segment_time", split_time,
                                                "-segment_format", 'mpegts',
                                                "-reset_timestamps", "1",
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
                                                if converts_to_mp4:
                                                    file_paths = utils.get_file_paths(os.path.dirname(save_file_path))
                                                    prefix = os.path.basename(save_file_path).rsplit('_', maxsplit=1)[0]
                                                    for path in file_paths:
                                                        if prefix in path:
                                                            try:
                                                                threading.Thread(
                                                                    target=converts_mp4,
                                                                    args=(path, delete_origin_file)
                                                                ).start()
                                                            except subprocess.CalledProcessError as e:
                                                                logger.error(f"转码失败: {e} ")
                                                return

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

                # ffmpeg异常退出时，快速重试（15秒），而不是等待默认的120秒
                if last_ffmpeg_return_code != 0:
                    x = 15
                    color_obj.print_colored("\rffmpeg异常退出,15秒后快速重试", color_obj.YELLOW)
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
        with (open(url_config_file, "r", encoding=text_encoding, errors='ignore') as file):
            for origin_line in file:
                if origin_line in line_list:
                    delete_line(url_config_file, origin_line)
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
                    quality, url, name = split_line

                if quality not in ("原画", "蓝光", "超清", "高清", "标清", "流畅"):
                    quality = '原画'

                if url not in url_line_list:
                    url_line_list.append(url)
                else:
                    delete_line(url_config_file, origin_line)

                url = 'https://' + url if '://' not in url else url
                url_host = url.split('/')[2]

                platform_host = [
                    'live.douyin.com',
                    'v.douyin.com',
                    'www.douyin.com'
                ]

                if url_host in platform_host or any(ext in url for ext in (".flv", ".m3u8")):
                    if url_host in platform_host:
                        url = update_file(url_config_file, old_str=url, new_str=url.split('?')[0])

                    url_comments = [i for i in url_comments if url not in i]
                    if is_comment_line:
                        url_comments.append(url)
                    else:
                        new_line = (quality, url, name)
                        url_tuples_list.append(new_line)
                else:
                    if not origin_line.startswith('#'):
                        color_obj.print_colored(f"\r{origin_line.strip()} 本行包含未知链接.此条跳过", color_obj.YELLOW)
                        update_file(url_config_file, old_str=origin_line, new_str=origin_line, start_str='#')

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

                if url_tuple[1] not in running_list:
                    print(f"\r{'新增' if not first_start else '传入'}地址: {url_tuple[1]}")
                    monitoring += 1
                    args = [url_tuple, monitoring]
                    create_var[f'thread_{monitoring}'] = threading.Thread(target=start_record, args=args)
                    create_var[f'thread_{monitoring}'].daemon = True
                    create_var[f'thread_{monitoring}'].start()
                    running_list.append(url_tuple[1])
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
