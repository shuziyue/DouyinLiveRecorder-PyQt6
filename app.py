#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Date: 2025-10-23
Function: Web interface for DouyinLiveRecorder
功能: 抖音直播录制的Web界面
"""

# 导入所需模块
import os
import sys
import configparser  # 用于读取和写入配置文件
import threading  # 用于在后台运行录制任务
import subprocess  # 用于启动录制进程
import datetime  # 用于处理时间相关操作
from flask import Flask, render_template, request, redirect, url_for, jsonify  # Flask Web框架

# 创建Flask应用实例
app = Flask(__name__)

# 全局变量：录制进程/线程对象，用于跟踪当前录制状态
recording_process = None

# 配置文件路径相关设置
script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]  # 获取当前脚本所在目录
config_file = f'{script_path}/config/config.ini'  # 录制配置文件路径
url_config_file = f'{script_path}/config/URL_config.ini'  # 直播地址配置文件路径
text_encoding = 'utf-8-sig'  # 文本文件编码格式

# 创建templates目录和index.html文件
# 打包环境中，模板在 sys._MEIPASS（_internal目录）下；开发环境中在项目根目录下
if getattr(sys, 'frozen', False):
    templates_dir = os.path.join(sys._MEIPASS, 'templates')
else:
    templates_dir = os.path.join(script_path, 'templates')
if not os.path.exists(templates_dir):
    os.makedirs(templates_dir)

# 创建index.html模板 - Web界面的HTML内容
template_content = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>抖音直播录制配置</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background-color: #f8f9fa;
            min-height: 100vh;
        }
        .container {
            max-width: 800px;
            margin-top: 30px;
        }
        .card {
            margin-bottom: 20px;
        }
        .card-header {
            font-weight: bold;
        }
        .btn-primary {
            background-color: #1a237e;
            border-color: #1a237e;
        }
        .btn-primary:hover {
            background-color: #0d47a1;
            border-color: #0d47a1;
        }
        .list-group-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="text-center mb-4">抖音直播录制配置</h1>
        
        <!-- 直播地址管理卡片 -->
        <div class="card">
            <div class="card-header">直播地址管理</div>
            <div class="card-body">
                <form action="/add_url" method="post" class="mb-3">
                    <div class="input-group">
                        <input type="text" class="form-control" name="url" placeholder="请输入抖音直播间地址" required>
                        <button type="submit" class="btn btn-primary">添加</button>
                    </div>
                </form>
                <ul class="list-group">
                    {% for url in urls %}
                    <li class="list-group-item">
                        <span>{{ url }}</span>
                        <form action="/delete_url" method="post" style="display: inline;">
                            <input type="hidden" name="url" value="{{ url }}">
                            <button type="submit" class="btn btn-danger btn-sm">删除</button>
                        </form>
                    </li>
                    {% else %}
                    <li class="list-group-item">暂无直播地址</li>
                    {% endfor %}
                </ul>
            </div>
        </div>
        
        <!-- 录制配置卡片 -->
        <div class="card">
            <div class="card-header">录制配置</div>
            <div class="card-body">
                <form action="/update_config" method="post">
                    <div class="mb-3">
                        <label for="video_record_quality" class="form-label">视频质量</label>
                        <select class="form-select" id="video_record_quality" name="video_record_quality">
                            <option value="原画" {% if video_record_quality == "原画" %}selected{% endif %}>原画</option>
                            <option value="蓝光" {% if video_record_quality == "蓝光" %}selected{% endif %}>蓝光</option>
                            <option value="超清" {% if video_record_quality == "超清" %}selected{% endif %}>超清</option>
                            <option value="高清" {% if video_record_quality == "高清" %}selected{% endif %}>高清</option>
                            <option value="标清" {% if video_record_quality == "标清" %}selected{% endif %}>标清</option>
                            <option value="流畅" {% if video_record_quality == "流畅" %}selected{% endif %}>流畅</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label for="video_save_type" class="form-label">保存格式</label>
                        <select class="form-select" id="video_save_type" name="video_save_type">
                            <option value="ts" {% if video_save_type == "ts" %}selected{% endif %}>TS</option>
                            <option value="flv" {% if video_save_type == "flv" %}selected{% endif %}>FLV</option>
                            <option value="mp4" {% if video_save_type == "mp4" %}selected{% endif %}>MP4</option>
                            <option value="mkv" {% if video_save_type == "mkv" %}selected{% endif %}>MKV</option>
                            <option value="mp3音频" {% if video_save_type == "mp3音频" %}selected{% endif %}>MP3音频</option>
                            <option value="m4a音频" {% if video_save_type == "m4a音频" %}selected{% endif %}>M4A音频</option>
                        </select>
                    </div>
                    
                    <!-- 分段录制设置 -->
                    <h5 class="mt-4">分段录制设置</h5>
                    <div class="mb-3">
                        <label for="split_video_by_time" class="form-label">分段录制是否开启</label>
                        <select class="form-select" id="split_video_by_time" name="split_video_by_time">
                            <option value="是" {% if split_video_by_time == "是" %}selected{% endif %}>是</option>
                            <option value="否" {% if split_video_by_time == "否" %}selected{% endif %}>否</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label for="split_time" class="form-label">视频分段时间(秒)</label>
                        <input type="number" class="form-control" id="split_time" name="split_time" value="{{ split_time }}" min="60" max="3600">
                    </div>
                    
                    <!-- 转码设置 -->
                    <h5 class="mt-4">转码设置</h5>
                    <div class="mb-3">
                        <label for="converts_to_mp4" class="form-label">录制完成后自动转为mp4格式</label>
                        <select class="form-select" id="converts_to_mp4" name="converts_to_mp4">
                            <option value="是" {% if converts_to_mp4 == "是" %}selected{% endif %}>是</option>
                            <option value="否" {% if converts_to_mp4 == "否" %}selected{% endif %}>否</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label for="converts_to_h264" class="form-label">mp4格式重新编码为h264</label>
                        <select class="form-select" id="converts_to_h264" name="converts_to_h264">
                            <option value="是" {% if converts_to_h264 == "是" %}selected{% endif %}>是</option>
                            <option value="否" {% if converts_to_h264 == "否" %}selected{% endif %}>否</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label for="delete_origin_file" class="form-label">追加格式后删除原文件</label>
                        <select class="form-select" id="delete_origin_file" name="delete_origin_file">
                            <option value="是" {% if delete_origin_file == "是" %}selected{% endif %}>是</option>
                            <option value="否" {% if delete_origin_file == "否" %}selected{% endif %}>否</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label for="create_time_file" class="form-label">生成时间字幕文件</label>
                        <select class="form-select" id="create_time_file" name="create_time_file">
                            <option value="是" {% if create_time_file == "是" %}selected{% endif %}>是</option>
                            <option value="否" {% if create_time_file == "否" %}selected{% endif %}>否</option>
                        </select>
                    </div>
                    
                    <button type="submit" class="btn btn-primary mt-4">保存配置</button>
                </form>
            </div>
        </div>
        
        <!-- 录制控制卡片 -->
        <div class="card">
            <div class="card-header">录制控制</div>
            <div class="card-body">
                <div class="mb-3">
                    <span class="badge {% if is_recording %}bg-success{% else %}bg-secondary{% endif %}">
                        {% if is_recording %}录制中{% else %}未录制{% endif %}
                    </span>
                </div>
                <form action="/start_recording" method="post" class="d-inline">
                    <button type="submit" class="btn btn-success me-2" {% if is_recording %}disabled{% endif %}>开始录制</button>
                </form>
                <form action="/stop_recording" method="post" class="d-inline">
                    <button type="submit" class="btn btn-danger" {% if not is_recording %}disabled{% endif %}>停止录制</button>
                </form>
                <form action="/shutdown" method="post" class="d-inline float-end">
                    <button type="submit" class="btn btn-warning">退出程序</button>
                </form>
                <p class="mt-3 text-muted">点击"开始录制"按钮后，系统会后台运行录制程序。录制过程中请勿关闭此页面。</p>
                <div class="mt-3">
                    <h6>录制状态</h6>
                    <p>当前状态: {% if is_recording %}正在录制中，请耐心等待...{% else %}已停止录制{% endif %}</p>
                    <p class="text-muted small">注：已开启分段录制功能，每1800秒生成一个视频文件。</p>
                </div>
            </div>
        </div>
        
        <!-- 使用说明卡片 -->
        <div class="card">
            <div class="card-header">使用说明</div>
            <div class="card-body">
                <ul class="list-unstyled">
                    <li class="mb-2"><strong>1. 添加直播地址</strong>：在上方输入框中输入抖音直播间地址，点击"添加"按钮。</li>
                    <li class="mb-2"><strong>2. 配置录制参数</strong>：选择合适的视频质量和保存格式。</li>
                    <li class="mb-2"><strong>3. 启动录制</strong>：点击"开始录制"按钮启动录制程序。</li>
                    <li class="mb-2"><strong>4. 停止录制</strong>：点击"停止录制"按钮停止录制。</li>
                    <li class="mb-2"><strong>5. 查看录制结果</strong>：录制的视频会保存在配置的保存路径中。</li>
                </ul>
            </div>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
'''

# 确保模板文件被创建
template_path = os.path.join(templates_dir, 'index.html')
if not os.path.exists(template_path):
    with open(template_path, 'w', encoding='utf-8') as f:
        f.write(template_content)

# 配置Flask的模板目录
app.template_folder = templates_dir


def read_config():
    """
    读取配置文件
    返回: configparser对象，包含所有配置信息
    """
    # 创建配置解析器对象
    config = configparser.RawConfigParser()
    # 读取配置文件，指定编码格式
    config.read(config_file, encoding=text_encoding)
    return config


def write_config(config):
    """
    写入配置文件
    参数: config - 要写入的configparser对象
    """
    # 以写入模式打开配置文件，指定编码格式
    with open(config_file, 'w', encoding=text_encoding) as f:
        # 将配置写入文件
        config.write(f)


def read_urls():
    """
    读取直播地址列表
    返回: 直播地址的列表
    """
    # 检查URL配置文件是否存在
    if not os.path.exists(url_config_file):
        return []
    # 以读取模式打开URL配置文件
    with open(url_config_file, 'r', encoding=text_encoding) as f:
        lines = f.readlines()
    urls = []
    # 遍历文件中的每一行
    for line in lines:
        line = line.strip()
        # 跳过空行和注释行
        if line and not line.startswith('#'):
            urls.append(line)
    return urls


def write_urls(urls):
    """
    写入直播地址列表
    参数: urls - 要写入的直播地址列表
    """
    # 以写入模式打开URL配置文件
    with open(url_config_file, 'w', encoding=text_encoding) as f:
        # 将每个URL写入文件，每行一个
        for url in urls:
            f.write(url + '\n')


@app.route('/')
def index():
    """
    首页路由 - 显示配置页面
    返回: 渲染后的HTML页面
    """
    # 读取配置文件和URL列表
    config = read_config()
    urls = read_urls()
    
    # 读取配置值
    video_record_quality = config.get('录制设置', '原画|超清|高清|标清|流畅', fallback='原画')
    video_save_type = config.get('录制设置', '视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', fallback='ts')
    dy_cookie = config.get('Cookie', '抖音cookie', fallback='')
    
    # 读取新增配置项
    split_video_by_time = config.get('录制设置', '分段录制是否开启', fallback='是')
    split_time = config.get('录制设置', '视频分段时间(秒)', fallback='1800')
    converts_to_mp4 = config.get('录制设置', '录制完成后自动转为mp4格式', fallback='是')
    converts_to_h264 = config.get('录制设置', 'mp4格式重新编码为h264', fallback='否')
    delete_origin_file = config.get('录制设置', '追加格式后删除原文件', fallback='是')
    create_time_file = config.get('录制设置', '生成时间字幕文件', fallback='否')
    
    # 检查录制状态
    is_recording = False
    if recording_process is not None:
        if hasattr(recording_process, 'poll'):
            # 是进程对象，检查进程是否还在运行
            is_recording = recording_process.poll() is None
        else:
            # 是线程对象，检查线程是否还存活
            is_recording = recording_process.is_alive()
    
    # 渲染HTML模板并返回，传递所有配置参数
    return render_template('index.html', 
                        urls=urls,
                        video_record_quality=video_record_quality,
                        video_save_type=video_save_type,
                        dy_cookie=dy_cookie,
                        split_video_by_time=split_video_by_time,
                        split_time=split_time,
                        converts_to_mp4=converts_to_mp4,
                        converts_to_h264=converts_to_h264,
                        delete_origin_file=delete_origin_file,
                        create_time_file=create_time_file,
                        is_recording=is_recording)


@app.route('/add_url', methods=['POST'])
def add_url():
    """
    添加直播地址路由
    返回: 重定向到首页
    """
    # 从表单中获取要添加的URL
    url = request.form.get('url')
    if url:
        # 读取现有URL列表
        urls = read_urls()
        # 添加新URL
        urls.append(url)
        # 写回URL配置文件
        write_urls(urls)
    # 重定向回首页
    return redirect(url_for('index'))


@app.route('/delete_url', methods=['POST'])
def delete_url():
    """
    删除直播地址路由
    返回: 重定向到首页
    """
    # 从表单中获取要删除的URL
    url = request.form.get('url')
    if url:
        # 读取现有URL列表
        urls = read_urls()
        # 过滤掉要删除的URL
        urls = [u for u in urls if u != url]
        # 写回URL配置文件
        write_urls(urls)
    # 重定向回首页
    return redirect(url_for('index'))


@app.route('/update_config', methods=['POST'])
def update_config():
    """
    更新配置路由
    返回: 重定向到首页
    """
    # 读取现有配置
    config = read_config()
    
    # 更新录制设置
    config.set('录制设置', '原画|超清|高清|标清|流畅', request.form.get('video_record_quality', '原画'))
    config.set('录制设置', '视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', request.form.get('video_save_type', 'ts'))
    
    # 更新新增配置项
    config.set('录制设置', '分段录制是否开启', request.form.get('split_video_by_time', '是'))
    config.set('录制设置', '视频分段时间(秒)', request.form.get('split_time', '1800'))
    config.set('录制设置', '录制完成后自动转为mp4格式', request.form.get('converts_to_mp4', '是'))
    config.set('录制设置', 'mp4格式重新编码为h264', request.form.get('converts_to_h264', '否'))
    config.set('录制设置', '追加格式后删除原文件', request.form.get('delete_origin_file', '是'))
    config.set('录制设置', '生成时间字幕文件', request.form.get('create_time_file', '否'))
    
    # 更新Cookie设置
    config.set('Cookie', '抖音cookie', request.form.get('dy_cookie', ''))
    
    # 写回配置文件
    write_config(config)
    # 重定向回首页
    return redirect(url_for('index'))


@app.route('/start_recording', methods=['POST'])
def start_recording():
    """
    开始录制路由
    返回: 重定向到首页
    """
    global recording_process
    # 检查是否可以启动录制：recording_process为None，或者进程已结束，或者不是进程对象
    if recording_process is None or (hasattr(recording_process, 'poll') and recording_process.poll() is not None) or not hasattr(recording_process, 'poll'):
        # 启动录制
        import sys
        import os
        import traceback
        import subprocess
        
        # 获取脚本路径
        script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]
        
        # 记录启动信息到debug.log
        with open(os.path.join(script_path, 'debug.log'), 'a', encoding='utf-8') as f:
            f.write(f"\n=== 开始录制 - {datetime.datetime.now()} ===\n")
            f.write(f"脚本路径: {script_path}\n")
        
        # 确保config文件夹和URL_config.ini存在
        config_folder = os.path.join(script_path, 'config')
        if not os.path.exists(config_folder):
            os.makedirs(config_folder)
        
        url_config_file = os.path.join(config_folder, 'URL_config.ini')
        if not os.path.exists(url_config_file):
            with open(url_config_file, 'w', encoding='utf-8') as f:
                f.write('')
        
        # 检查是否在打包环境中运行
        is_pyinstaller = getattr(sys, 'frozen', False)
        with open(os.path.join(script_path, 'debug.log'), 'a', encoding='utf-8') as f:
            f.write(f"是否在打包环境中运行: {is_pyinstaller}\n")
        
        try:
            # 统一在线程中运行录制，开发环境和打包环境一致，避免弹出第二个控制台窗口
            with open(os.path.join(script_path, 'debug.log'), 'a', encoding='utf-8') as f:
                f.write("启动新线程运行录制代码\n")

            # 在新线程中运行录制
            def run_recording():
                try:
                    # 用 exec 直接读取并执行 douyin_only.py，每次都是全新执行，避免模块缓存问题
                    # 打包环境中，douyin_only.py 在 sys._MEIPASS（_internal目录）下
                    if getattr(sys, 'frozen', False):
                        douyin_only_path = os.path.join(sys._MEIPASS, 'douyin_only.py')
                    else:
                        douyin_only_path = os.path.join(script_path, 'douyin_only.py')
                    with open(douyin_only_path, 'r', encoding='utf-8') as f:
                        code = f.read()
                    exec(code, {'__name__': '__main__', '__file__': douyin_only_path})
                except Exception as e:
                    with open(os.path.join(script_path, 'error.log'), 'a', encoding='utf-8') as f:
                        f.write(f"运行录制时出错: {str(e)}\n")
                        f.write(traceback.format_exc())
                        f.write("\n")

            # 创建并启动录制线程
            recording_process = threading.Thread(target=run_recording)
            recording_process.daemon = True
            recording_process.start()

            with open(os.path.join(script_path, 'debug.log'), 'a', encoding='utf-8') as f:
                f.write("录制线程已启动\n")
        
        except Exception as e:
            # 记录错误信息
            with open(os.path.join(script_path, 'error.log'), 'a', encoding='utf-8') as f:
                f.write(f"启动录制时出错: {str(e)}\n")
                f.write(traceback.format_exc())
                f.write("\n")
            with open(os.path.join(script_path, 'debug.log'), 'a', encoding='utf-8') as f:
                f.write(f"启动录制时出错: {str(e)}\n")
    
    # 重定向回首页
    return redirect(url_for('index'))


@app.route('/stop_recording', methods=['POST'])
def stop_recording():
    """
    停止录制路由
    返回: 重定向到首页
    """
    global recording_process
    if recording_process is not None:
        if hasattr(recording_process, 'poll'):
            # 是进程对象
            if recording_process.poll() is None:
                try:
                    # 先尝试正常终止进程
                    recording_process.terminate()
                    recording_process.wait(timeout=5)  # 等待进程终止，最多等待5秒
                except Exception as e:
                    # 如果失败，使用taskkill命令强制终止
                    try:
                        subprocess.run(['taskkill', '/F', '/PID', str(recording_process.pid)], capture_output=True, text=True)
                    except Exception as e:
                        pass
        # 无论是什么，都清空recording_process
        recording_process = None
    
    # 停止所有相关的ffmpeg进程
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'ffmpeg.exe'], capture_output=True, text=True)
    except Exception as e:
        pass
    
    # 重定向回首页
    return redirect(url_for('index'))


@app.route('/shutdown', methods=['POST'])
def shutdown():
    """
    退出程序路由
    返回: 服务器已关闭消息
    """
    # 停止所有相关的ffmpeg进程
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'ffmpeg.exe'], capture_output=True, text=True)
    except Exception as e:
        pass
    
    # 强制退出Python进程
    import os
    os._exit(0)  # 强制退出，不执行清理操作
    return '服务器已关闭'


if __name__ == '__main__':
    # 创建templates目录
    templates_dir = os.path.join(script_path, 'templates')
    if not os.path.exists(templates_dir):
        os.makedirs(templates_dir)
    
    
    # 启动Flask应用
    app.run(host='0.0.0.0', port=5000, debug=True)
