#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音直播录制工具 - PyQt6 桌面版

在原版基础上新增：
  - 可配置的直播保存路径（并让内核真正生效）
  - 系统托盘常驻（最小化到托盘、托盘菜单、状态气泡）
  - 录制状态看板（主播 / 状态 / 流状态 / 已录时长 / 分段数 / 产出大小）
  - 界面细节打磨、窗口尺寸记忆
"""

import os
import sys
import json
import time
import re
import subprocess
import configparser
import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QComboBox, QCheckBox, QGroupBox, QTextEdit, QMessageBox,
    QSpinBox, QFormLayout, QSplitter, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSystemTrayIcon, QMenu, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QSettings, QSize, QEvent
from PyQt6.QtGui import QFont, QIcon, QPainter, QColor, QBrush, QAction

# ========== 路径配置 ==========
SCRIPT_PATH = os.path.split(os.path.realpath(sys.argv[0]))[0]
if not SCRIPT_PATH or not os.path.isdir(SCRIPT_PATH):
    SCRIPT_PATH = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(SCRIPT_PATH, 'config', 'config.ini')
URL_CONFIG_FILE = os.path.join(SCRIPT_PATH, 'config', 'URL_config.ini')
UI_STATE_FILE = os.path.join(SCRIPT_PATH, 'config', 'ui.ini')
DOUYIN_ONLY = os.path.join(SCRIPT_PATH, 'douyin_only.py')
LOG_DIR = os.path.join(SCRIPT_PATH, 'logs')
STATUS_FILE = os.path.join(LOG_DIR, 'runtime_status.json')
TEXT_ENCODING = 'utf-8-sig'

SAVE_PATH_KEY = '直播保存路径(不填则默认)'
PLATFORM_DIR_NAME = '抖音直播'  # 内核里 platform 的取值，录制时会在保存路径下自动建这一层
STATUS_STALE_SECONDS = 15  # 状态文件超过这个时长未更新则视为进程已退出

# ========== 直播流健康状态的外观映射 ==========
# 内核写出的状态码 -> (显示文字, 文字颜色, 整行底色)
# 底色只给"需要注意"的状态上色，正常/未检测保持表格原样，避免整块看板花掉
HEALTH_STYLES = {
    'good':     ('✅ 正常',      '#27ae60', None),
    'warn':     ('⚠️ 可疑',      '#c76a00', '#fdf3e6'),
    'bad':      ('❌ 坏流',      '#c0392b', '#fdeceb'),
    'switched': ('🔁 已换线路',  '#2471a3', None),
    '':         ('— 未检测',    '#95a5a6', None),
}
HEALTH_LEGEND = (
    '直播流健康状态（把鼠标停在单元格上看详情）\n'
    '✅ 正常：探测与体检都通过，画面能正常解码\n'
    '⚠️ 可疑：流信息不像正常直播（容器/编码异常），或录制中断\n'
    '❌ 坏流：录进去的是没有画面的空壳，本段已放弃\n'
    '🔁 已换线路：因坏流自动切到另一条线路在重试\n'
    '— 未检测：还没拿到结论（未开播 / 探测不可用）'
)

try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    pass  # 日志目录建不出来时不阻断启动，内核侧另有回退

APP_NAME = '抖音直播录制工具'
APP_VERSION = 'v4.0.7-PyQt6-Pro'


def is_drive_root(path: str) -> bool:
    """判断是否为盘根（F:\ 、C:\）。
    只认「盘符 + 冒号 + 斜杠」这种形式；UNC 网络共享（\\\\server\\share）不算盘根。
    """
    try:
        drive, tail = os.path.splitdrive(os.path.abspath(path))
    except Exception:
        return False
    return len(drive) == 2 and drive.endswith(':') and tail.strip('\\/') == ''


def probe_dir_writable(path: str):
    """真实写入探测目录是否可写，返回 (是否可用, 错误描述)。
    不用 os.access —— Windows 上它只检查只读属性、不检查 ACL，
    对「盘根存在但当前用户无写权限」会误判为可写。
    """
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        return False, f'无法创建目录（{type(e).__name__}: {e}）'
    if not os.path.isdir(path):
        return False, '该位置已存在同名文件，不是文件夹'
    probe = os.path.join(path, f'.__save_probe_{os.getpid()}')
    try:
        with open(probe, 'wb') as fp:
            fp.write(b'1')
        os.remove(probe)
        return True, ''
    except Exception as e:
        try:
            if os.path.exists(probe):
                os.remove(probe)
        except Exception:
            pass
        return False, f'没有写入权限（{type(e).__name__}: {e}）'


def suggest_save_path_hint(path: str) -> str:
    """针对不可写路径给出具体建议"""
    upper = os.path.abspath(path).upper()
    if upper.startswith(('C:\\WINDOWS', 'C:\\PROGRAM FILES', 'C:\\PROGRAM FILES (X86)')):
        return '该位置受系统保护，建议改到 D 盘或用户目录下。'
    if is_drive_root(path):
        return '盘根通常可以写，若被拒绝多半是权限或分区为只读，建议换一个普通文件夹。'
    return '建议换一个有写入权限的文件夹。'


def _pick_default_download_dir() -> str:
    """默认录播目录：优先程序目录下的 downloads，
    若程序目录不可写（例如打包后放在 C:\\Program Files 下）则回退到用户主目录。
    与内核 douyin_only.py 的判定规则保持一致。
    """
    primary = os.path.join(SCRIPT_PATH, 'downloads')
    if probe_dir_writable(primary)[0]:
        return primary
    fallback = os.path.join(os.path.expanduser('~'), 'DouyinLiveRecorder', 'downloads')
    if probe_dir_writable(fallback)[0]:
        return fallback
    return primary


DEFAULT_DOWNLOAD_DIR = _pick_default_download_dir()


def resource_path(*parts) -> str:
    """获取资源文件路径（兼容 PyInstaller 打包后的 _MEIPASS 目录）"""
    base = getattr(sys, '_MEIPASS', SCRIPT_PATH) if getattr(sys, 'frozen', False) else SCRIPT_PATH
    return os.path.join(base, *parts)


def format_duration(seconds) -> str:
    """秒 -> 1:02:03 / 02:03"""
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'


def format_size(num_bytes) -> str:
    """字节 -> 人类可读"""
    num = float(num_bytes or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if num < 1024 or unit == 'TB':
            return f'{num:.0f} {unit}' if unit == 'B' else f'{num:.1f} {unit}'
        num /= 1024
    return f'{num:.1f} TB'


def load_app_icon() -> QIcon:
    """加载应用图标，文件缺失时返回空图标"""
    for name in ('icon.ico', 'icon.png'):
        path = resource_path('assets', name)
        if os.path.exists(path):
            return QIcon(path)
    return QIcon()


# ========== 配置文件读写（适配原格式） ==========
def read_config():
    """读取 config.ini，返回配置字典"""
    config = configparser.RawConfigParser()
    config.read(CONFIG_FILE, encoding=TEXT_ENCODING)
    result = {}
    if config.has_section('录制设置'):
        for key, value in config.items('录制设置'):
            result[key] = value
    return result


def write_config_value(key, value):
    """修改 config.ini 中某个 key 的值，保持其他配置不变"""
    config = configparser.RawConfigParser()
    config.read(CONFIG_FILE, encoding=TEXT_ENCODING)
    if not config.has_section('录制设置'):
        config.add_section('录制设置')
    config.set('录制设置', key, value)
    with open(CONFIG_FILE, 'w', encoding=TEXT_ENCODING) as f:
        config.write(f)


def read_urls():
    """读取 URL_config.ini（纯文本格式，每行 URL,主播: 名字）"""
    urls = []
    if os.path.exists(URL_CONFIG_FILE):
        with open(URL_CONFIG_FILE, 'r', encoding=TEXT_ENCODING) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    urls.append(line)
    return urls


def write_urls(urls):
    """写入 URL_config.ini（纯文本格式）"""
    with open(URL_CONFIG_FILE, 'w', encoding=TEXT_ENCODING) as f:
        for url in urls:
            f.write(url + '\n')


# 内核认的档位（含“蓝光”）：少了任何一个，用户手写的配置行就会被当成“名字”处理，
# 一按「应用」就可能把名字改坏，所以两边必须一致
QUALITY_CHOICES = ['原画', '蓝光', '超清', '高清', '标清', '流畅']
DOUYIN_HOSTS = ('live.douyin.com', 'v.douyin.com', 'www.douyin.com')


def normalize_url_for_compare(url: str) -> str:
    """比较两个地址是不是同一个直播间时用。

    和内核保持一致：抖音地址会去掉 ? 后面的参数（内核写文件时就是这么截的），
    否则「同一直播间 + 同一画质」会一边拦一边放，出现两套判断。
    """
    text = (url or '').strip()
    if '?' in text:
        head = text.split('?', 1)[0]
        host = head.split('//')[-1].split('/')[0].lower()
        if host in DOUYIN_HOSTS:
            text = head
    text = text.lower()
    text = text.replace('https://', '').replace('http://', '')
    return text.rstrip('/')


def split_url_line(line: str):
    """把 URL_config.ini 的一行拆成 (画质, 地址, 其余字段)

    兼容三种写法（内核全都认）：
        https://live.douyin.com/xxx
        https://live.douyin.com/xxx,主播: 名字
        超清,https://live.douyin.com/xxx,主播: 名字
    """
    quality = ''
    url = ''
    extras = []
    for part in (p.strip() for p in re.split('[,，]', (line or '').strip())):
        if not part:
            continue
        looks_like_url = ('://' in part or 'live.douyin.com' in part
                          or 'douyin.com' in part or '.flv' in part or '.m3u8' in part)
        if not url and looks_like_url:
            url = part
        elif not quality and part in QUALITY_CHOICES:
            quality = part
        else:
            extras.append(part)
    return quality, url, extras


def make_url_line(quality: str, url: str, extras) -> str:
    """把 (画质, 地址, 其余字段) 拼回配置行；没有画质就按老格式写。

    内核按逗号切成最多三段（画质, 地址, 名字），所以这里把多余字段合并成一段，
    避免写出四段把内核的解析弄崩（名字里带逗号的情况）。
    """
    extra_text = ' '.join(str(e).strip() for e in extras if str(e).strip())
    parts = [url]
    if extra_text:
        parts.append(extra_text)
    if quality:
        parts = [quality] + parts
    return ','.join(parts)


def url_line_display(line: str) -> str:
    """列表里显示的友好写法：[画质] 名字 / 地址"""
    quality, url, extras = split_url_line(line)
    name = ''
    for extra in extras:
        if extra.startswith('主播'):
            name = extra.split(':', 1)[-1].strip()
    title = name or url or (line or '').strip()
    if not url:
        return (line or '').strip()
    return f"[{quality or '默认画质'}] {title}\n    {url}"


# ========== 录制进程管理 ==========
class RecorderManager(QObject):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.process = None
        self.is_recording = False
        self._stopping = False
        self._stop_deadline = 0.0

    def start_recording(self):
        if self.is_recording:
            return
        try:
            # 上次异常退出可能留下 stop_request，不清掉内核起来就会自己退出
            self.clear_leftover_stop_signal()
            creationflags = 0
            if os.name == 'nt':
                creationflags = subprocess.CREATE_NO_WINDOW

            # 根据是否打包环境，选择调用方式
            if getattr(sys, 'frozen', False):
                # 打包环境：调用 recorder.exe
                recorder_exe = os.path.join(SCRIPT_PATH, 'recorder.exe')
                self.process = subprocess.Popen(
                    [recorder_exe],
                    creationflags=creationflags,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=SCRIPT_PATH
                )
            else:
                # 开发环境：用 python 运行 douyin_only.py
                self.process = subprocess.Popen(
                    [sys.executable, DOUYIN_ONLY],
                    creationflags=creationflags,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=SCRIPT_PATH
                )
            self.is_recording = True
            self.status_signal.emit('录制中')
            self.log_signal.emit('开始录制...')

            import threading
            t = threading.Thread(target=self._read_output, daemon=True)
            t.start()
        except Exception as e:
            self.log_signal.emit(f'启动录制失败: {e}')

    @staticmethod
    def _safe_decode(raw_bytes: bytes) -> str:
        """按多种编码尝试解码一行输出"""
        for encoding in ('utf-8', 'gbk', 'gb2312', 'gb18030'):
            try:
                return raw_bytes.decode(encoding).strip()
            except (UnicodeDecodeError, UnicodeError):
                continue
        return raw_bytes.decode('utf-8', errors='ignore').strip()

    def _read_output(self):
        """读取录制进程输出。

        内核每 5 秒会打印一大块监控信息（以「共监测」开头、以两行 xxxx 结束），
        这里用状态机整块丢弃，而不是用关键词黑名单——避免吞掉含关键词的正常消息。
        """
        if not self.process:
            return
        in_display_block = False
        xxxx_lines = 0
        try:
            for raw_line in self.process.stdout:
                try:
                    text = self._safe_decode(raw_line)
                    if not text:
                        continue

                    if text.startswith('共监测'):
                        in_display_block = True
                        xxxx_lines = 0
                        continue

                    if in_display_block:
                        if text.startswith('xxxx'):
                            xxxx_lines += 1
                            if xxxx_lines >= 2:
                                in_display_block = False
                        continue

                    # 跳过纯数字、纯分隔符行
                    if text.isdigit() or not text.strip(' |-_.'):
                        continue

                    self.log_signal.emit(text)
                except Exception:
                    pass
        except Exception:
            pass
        self.is_recording = False
        self.status_signal.emit('已停止')
        self.log_signal.emit('录制已停止')

    def _stop_signal_path(self) -> str:
        """与内核约定的停止请求文件（内核看到后会把当前分段正常收尾）"""
        return os.path.join(SCRIPT_PATH, 'logs', 'stop_request')

    def _request_graceful_stop(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._stop_signal_path()), exist_ok=True)
            with open(self._stop_signal_path(), 'w', encoding='utf-8') as fh:
                fh.write(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        except Exception:
            pass

    def _clear_stop_signal(self) -> None:
        try:
            signal_path = self._stop_signal_path()
            if os.path.exists(signal_path):
                os.remove(signal_path)
        except OSError:
            pass

    def clear_leftover_stop_signal(self) -> None:
        """清掉上一次异常退出可能留下的停止请求。

        留着它的后果很坑：内核一启动就当成“收到退出请求”，立刻自己退出，
        表现就是“点了开始录制却什么都没发生”。
        """
        self._clear_stop_signal()

    def is_process_running(self) -> bool:
        """录制进程是否真的还活着（不看可能过期的 is_recording 标志）"""
        return bool(self.process) and self.process.poll() is None

    def begin_graceful_stop(self, timeout: float = 180.0) -> bool:
        """发停止请求但**不阻塞**：界面可以继续刷新“正在收尾”的提示。

        返回 False 表示录制进程本来就已经不在了。
        """
        process = self.process
        if process is None or process.poll() is not None:
            self.process = None
            self.is_recording = False
            self._stopping = False
            return False
        self._request_graceful_stop()
        self._stop_deadline = time.time() + timeout
        self._stopping = True
        return True

    def stop_wait_state(self):
        """收尾进度：返回 (state, 剩余秒数)，state 为 waiting / timeout / done。

        内核收尾完成后会把 stop_request 删掉，那就是“可以收了”的信号。
        """
        if not self._stopping:
            return 'done', 0.0
        process = self.process
        if process is None or process.poll() is not None:
            return 'done', 0.0
        if not os.path.exists(self._stop_signal_path()):
            return 'done', 0.0
        left = self._stop_deadline - time.time()
        if left <= 0:
            return 'timeout', 0.0
        return 'waiting', left

    def finish_stop(self):
        """收尾结束（或超时）后调用：强杀兜底 + 状态复位"""
        process = self.process
        try:
            if process is not None and process.poll() is None:
                killed = False
                if os.name == 'nt':
                    # 只结束这条录制进程树，不碰其它软件的 ffmpeg
                    try:
                        subprocess.run(
                            ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
                        )
                        killed = True
                    except Exception as e:
                        self.log_signal.emit(f'结束录制进程失败: {e}')
                if not killed:
                    try:
                        process.terminate()
                        process.wait(timeout=3)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                # 再确认一次：宁可多杀一次，也不要“关掉了还在录”
                try:
                    if process.poll() is None:
                        process.kill()
                except Exception:
                    pass
        except Exception as e:
            self.log_signal.emit(f'结束录制进程出错: {e}')
        finally:
            self._clear_stop_signal()
            self.process = None
            self.is_recording = False
            self._stopping = False
            self.status_signal.emit('已停止')
            self.log_signal.emit('停止录制')

    def stop_recording(self, graceful: bool = True):
        """结束录制进程及其子进程(ffmpeg)。

        graceful=True：先写停止请求让内核把当前分段收尾（修复时间戳 / 转 mp4），
        收尾完成或超时后再强杀兜底。这个方法会阻塞，界面上的“停止录制”按钮
        走的是 begin_graceful_stop + 定时器轮询（不阻塞），这里主要给退出程序用。
        这里刻意不再用 `if not self.is_recording: return` —— 那个标志会在子进程管道
        关闭时被置成 False，导致“以为已经停了，其实还在录”。
        """
        if self.process is None and not self.is_recording:
            return
        try:
            if graceful and self.begin_graceful_stop():
                self.log_signal.emit('已请求停止：正在收尾当前分段（修复时间戳 / 转 mp4）...')
                deadline = time.time() + 181
                while time.time() < deadline:
                    state, _ = self.stop_wait_state()
                    if state != 'waiting':
                        break
                    time.sleep(1)
                else:
                    self.log_signal.emit('⚠️ 收尾等待超过 180 秒，将强制结束录制进程；'
                                         '可能有个别分段没来得及转 mp4')
            self.finish_stop()
        except Exception as e:
            self.log_signal.emit(f'停止录制出错: {e}')


# ========== 主窗口 ==========
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.recorder = RecorderManager()
        self.recorder.log_signal.connect(self.append_log)
        self.recorder.status_signal.connect(self.update_status)
        self.effective_save_path = ''
        self.tray_hint_shown = False
        self._stop_timer = None      # 收尾进度轮询定时器（停止录制时用）
        self._stop_started = 0.0
        self.settings = QSettings(UI_STATE_FILE, QSettings.Format.IniFormat)

        self.init_ui()
        self.init_tray()
        self.load_config()
        self.load_urls()
        self.restore_geometry()
        self.update_save_path_hint()

        # 状态看板定时刷新
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(2000)
        self.status_timer.timeout.connect(self.refresh_status_board)
        self.status_timer.start()
        self.refresh_status_board()

    # ---------- 图标 / 托盘 ----------
    @staticmethod
    def arrow_style() -> str:
        """下拉框 / 数字框的箭头样式。

        用图片绘制，比默认箭头更清楚；图片缺失时返回空串，自动回退到系统默认样式。
        """
        down = resource_path('assets', 'arrow_down.png')
        up = resource_path('assets', 'arrow_up.png')
        if not (os.path.exists(down) and os.path.exists(up)):
            return ''
        down_url = down.replace('\\', '/')
        up_url = up.replace('\\', '/')
        return (
            f'QComboBox::down-arrow {{ image: url("{down_url}"); width: 14px; height: 14px; margin-right: 8px; }}'
            f'QSpinBox::up-arrow {{ image: url("{up_url}"); width: 12px; height: 12px; }}'
            f'QSpinBox::down-arrow {{ image: url("{down_url}"); width: 12px; height: 12px; }}'
        )

    def build_tray_icon(self, recording: bool) -> QIcon:
        """托盘图标：录制中在右下角叠加一个红点"""
        base = load_app_icon()
        if base.isNull():
            return base
        pixmap = base.pixmap(QSize(64, 64))
        if recording:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0xE7, 0x4C, 0x3C)))
            painter.drawEllipse(36, 36, 26, 26)
            painter.end()
        return QIcon(pixmap)

    def init_tray(self):
        self.tray_icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.append_log('未检测到系统托盘，托盘功能不可用')
            return

        self.tray_icon = QSystemTrayIcon(self.build_tray_icon(False), self)
        self.tray_icon.setToolTip(f'{APP_NAME} · 未录制')

        menu = QMenu(self)
        self.tray_show_action = QAction('显示主窗口', self)
        self.tray_show_action.triggered.connect(self.show_main_window)
        self.tray_start_action = QAction('▶ 开始录制', self)
        self.tray_start_action.triggered.connect(self.start_recording)
        self.tray_stop_action = QAction('⏹ 停止录制', self)
        self.tray_stop_action.triggered.connect(self.stop_recording)
        self.tray_stop_action.setEnabled(False)
        self.tray_open_action = QAction('📂 打开录播目录', self)
        self.tray_open_action.triggered.connect(self.open_download_folder)
        self.tray_log_action = QAction('📄 打开日志目录', self)
        self.tray_log_action.triggered.connect(self.open_log_folder)
        self.tray_quit_action = QAction('退出程序', self)
        # 托盘退出也走确认流程，避免录制中误点直接退出
        self.tray_quit_action.triggered.connect(self.confirm_quit)

        menu.addAction(self.tray_show_action)
        menu.addSeparator()
        menu.addAction(self.tray_start_action)
        menu.addAction(self.tray_stop_action)
        menu.addSeparator()
        menu.addAction(self.tray_open_action)
        menu.addAction(self.tray_log_action)
        menu.addSeparator()
        menu.addAction(self.tray_quit_action)
        self.tray_menu = menu

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()
        # 允许窗口隐藏后程序继续驻留托盘
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick,
                      QSystemTrayIcon.ActivationReason.Trigger):
            self.show_main_window()

    def show_main_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def notify_tray(self, title, message):
        if self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3000)

    def update_tray_state(self, recording: bool):
        if not self.tray_icon:
            return
        self.tray_icon.setIcon(self.build_tray_icon(recording))
        self.tray_icon.setToolTip(f'{APP_NAME} · {"录制中" if recording else "未录制"}')
        self.tray_start_action.setEnabled(not recording)
        self.tray_stop_action.setEnabled(recording)

    # ---------- 界面 ----------
    def init_ui(self):
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(1080, 800)
        self.resize(1240, 920)
        base_style = """
            QMainWindow { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #667eea, stop:1 #764ba2); }
            QWidget#central { background: transparent; }
            QGroupBox {
                background: rgba(255, 255, 255, 0.97);
                border: none;
                border-radius: 16px;
                margin-top: 18px;
                padding-top: 22px;
                padding-left: 18px;
                padding-right: 18px;
                padding-bottom: 16px;
                font-weight: bold;
                font-size: 15px;
                color: #2c3e50;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 20px;
                top: 8px;
                padding: 4px 12px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667eea, stop:1 #764ba2);
                color: white;
                border-radius: 8px;
                font-size: 13px;
            }
            QLineEdit, QComboBox, QSpinBox {
                padding: 6px 11px;
                border: 2px solid #e8ecf1;
                border-radius: 10px;
                font-size: 13px;
                background: #f8f9fc;
                color: #2c3e50;
                selection-background-color: #667eea;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border-color: #667eea;
                background: white;
            }
            QComboBox::drop-down { border: none; width: 30px; }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 22px; border: none; background: transparent;
            }
            QComboBox QAbstractItemView {
                background: white; color: #2c3e50;
                selection-background-color: #667eea; selection-color: white;
                border: 1px solid #e8ecf1; outline: none;
            }
            QPushButton {
                padding: 10px 20px;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: bold;
                color: white;
                background: #8e9bb3;
            }
            QPushButton:hover { background: #7d8aa3; }
            QPushButton:pressed { background: #6c7890; }
            QPushButton:disabled { background: #bdc3c7; color: #f2f4f6; }
            QPushButton#addBtn { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667eea, stop:1 #764ba2); }
            QPushButton#addBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5a70d8, stop:1 #6b4397); }
            QPushButton#delBtn { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e74c3c, stop:1 #c0392b); }
            QPushButton#delBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8402f, stop:1 #a93226); }
            QPushButton#startBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #27ae60, stop:1 #2ecc71);
                font-size: 16px; padding: 13px 30px; border-radius: 12px;
            }
            QPushButton#startBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #229954, stop:1 #29b866); }
            QPushButton#stopBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e74c3c, stop:1 #c0392b);
                font-size: 16px; padding: 13px 30px; border-radius: 12px;
            }
            QPushButton#stopBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8402f, stop:1 #a93226); }
            QPushButton#saveBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f39c12, stop:1 #e67e22);
                padding: 9px 20px;
            }
            QPushButton#saveBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e08e0b, stop:1 #d35400); }
            QPushButton#ghostBtn {
                background: #eef1f8; color: #4a5568; padding: 9px 14px;
            }
            QPushButton#ghostBtn:hover { background: #e2e7f2; }
            QListWidget {
                border: 2px solid #e8ecf1; border-radius: 12px; padding: 6px;
                font-size: 13px; background: #f8f9fc; color: #2c3e50; outline: none;
            }
            QListWidget::item { padding: 10px 12px; border-radius: 8px; margin: 2px 0; }
            QListWidget::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667eea, stop:1 #764ba2);
                color: white;
            }
            QListWidget::item:hover:!selected { background: #eef1f8; }
            QTextEdit {
                border: 2px solid #e8ecf1; border-radius: 12px; padding: 10px;
                font-family: Consolas, Courier New, monospace; font-size: 12px;
                background: #1e1e2e; color: #cdd6f4;
            }
            QTableWidget {
                border: 2px solid #e8ecf1; border-radius: 12px;
                background: #ffffff; alternate-background-color: #f7f9fd;
                gridline-color: #eef1f6; font-size: 12px; color: #2c3e50;
                outline: none;
            }
            QTableWidget::item { padding: 6px 8px; }
            QTableWidget::item:selected { background: #e8ecfd; color: #2c3e50; }
            QHeaderView::section {
                background: #f1f4fb; color: #5a6b85; border: none;
                border-bottom: 1px solid #e2e8f4; padding: 8px 6px;
                font-size: 12px; font-weight: bold;
            }
            QLabel { font-size: 13px; color: #2c3e50; }
            QLabel#titleLabel { font-size: 28px; font-weight: bold; color: white; }
            QLabel#hintLabel { font-size: 12px; color: rgba(255, 255, 255, 0.88); }
            QLabel#summaryLabel { font-size: 12px; font-weight: bold; color: #5a6b85; }
            QCheckBox { font-size: 13px; color: #2c3e50; spacing: 8px; }
            QCheckBox::indicator {
                width: 18px; height: 18px; border-radius: 5px; border: 2px solid #bdc3c7;
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667eea, stop:1 #764ba2);
                border-color: #667eea;
            }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
            QScrollBar::handle:vertical {
                background: rgba(102, 126, 234, 0.45); border-radius: 5px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(102, 126, 234, 0.75); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
            QScrollBar::handle:horizontal {
                background: rgba(102, 126, 234, 0.45); border-radius: 5px; min-width: 30px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QSplitter::handle { background: transparent; }
        """
        self.setStyleSheet(base_style + self.arrow_style())

        central = QWidget()
        central.setObjectName('central')
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 18, 20, 18)
        main_layout.setSpacing(12)

        # ---- 顶部标题与状态 ----
        header = QHBoxLayout()
        title = QLabel('🎬 抖音直播录制工具')
        title.setObjectName('titleLabel')
        self.statusLabel = QLabel('⏹ 未录制')
        self.statusLabel.setStyleSheet(
            'font-size: 14px; font-weight: bold; color: white;'
            'background: rgba(255,255,255,0.18); padding: 7px 16px; border-radius: 10px;'
        )
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.statusLabel)
        main_layout.addLayout(header)

        self.savePathHint = QLabel('保存路径：--')
        self.savePathHint.setObjectName('hintLabel')
        self.savePathHint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        main_layout.addWidget(self.savePathHint)

        # ---- 主体：上（左右分栏）+ 下（状态看板） ----
        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ===== 左上：地址管理 + 录制控制 =====
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        url_group = QGroupBox('📺 直播地址管理')
        url_layout = QVBoxLayout(url_group)
        url_layout.setSpacing(10)
        url_input_layout = QHBoxLayout()
        self.urlInput = QLineEdit()
        self.urlInput.setPlaceholderText('粘贴抖音直播间地址，如 https://live.douyin.com/xxxxxx')
        self.urlInput.returnPressed.connect(self.add_url)
        self.addBtn = QPushButton('➕ 添加')
        self.addBtn.setObjectName('addBtn')
        self.addBtn.clicked.connect(self.add_url)
        url_input_layout.addWidget(self.urlInput, 1)
        url_input_layout.addWidget(self.addBtn)
        url_layout.addLayout(url_input_layout)

        self.urlCountLabel = QLabel('已添加 0 个主播')
        self.urlCountLabel.setObjectName('summaryLabel')
        url_layout.addWidget(self.urlCountLabel)

        self.urlList = QListWidget()
        url_layout.addWidget(self.urlList, 1)

        # ---- 每个直播间单独选画质（同一个直播间可以挂两条不同画质做对比） ----
        quality_row = QHBoxLayout()
        quality_row.setSpacing(6)
        quality_row.addWidget(QLabel('选中项画质：'))
        self.itemQualityCombo = QComboBox()
        self.itemQualityCombo.addItems(QUALITY_CHOICES)
        self.itemQualityCombo.setToolTip(
            '给选中的直播间单独指定录制画质。\n\n'
            '想对比「原画 vs 超清」：把同一个直播间加两条，把它们的画质分别设成原画和超清，\n'
            '两条会同时录制、分别保存（文件名会自动带上画质）。\n'
            '同一个地址 + 同一个画质放两条没有意义，程序会提示并忽略重复的那条。')
        quality_row.addWidget(self.itemQualityCombo)
        self.applyQualityBtn = QPushButton('应用到选中')
        self.applyQualityBtn.setObjectName('ghostBtn')
        self.applyQualityBtn.clicked.connect(self.apply_quality_to_selected)
        quality_row.addWidget(self.applyQualityBtn)
        self.applyQualityAllBtn = QPushButton('应用到全部')
        self.applyQualityAllBtn.setObjectName('ghostBtn')
        self.applyQualityAllBtn.clicked.connect(self.apply_quality_to_all)
        quality_row.addWidget(self.applyQualityAllBtn)
        quality_row.addStretch()
        url_layout.addLayout(quality_row)
        self.urlList.itemSelectionChanged.connect(self.on_url_selection_changed)

        url_btn_layout = QHBoxLayout()
        self.delBtn = QPushButton('🗑 删除选中')
        self.delBtn.setObjectName('delBtn')
        self.delBtn.clicked.connect(self.del_url)
        self.clearUrlBtn = QPushButton('清空列表')
        self.clearUrlBtn.setObjectName('ghostBtn')
        self.clearUrlBtn.clicked.connect(self.clear_urls)
        url_btn_layout.addWidget(self.delBtn)
        url_btn_layout.addWidget(self.clearUrlBtn)
        url_layout.addLayout(url_btn_layout)
        left_layout.addWidget(url_group, 1)

        ctrl_group = QGroupBox('🎮 录制控制')
        ctrl_layout = QVBoxLayout(ctrl_group)
        ctrl_layout.setSpacing(10)
        btn_row1 = QHBoxLayout()
        self.startBtn = QPushButton('▶ 开始录制')
        self.startBtn.setObjectName('startBtn')
        self.startBtn.clicked.connect(self.start_recording)
        self.stopBtn = QPushButton('⏹ 停止录制')
        self.stopBtn.setObjectName('stopBtn')
        self.stopBtn.clicked.connect(self.stop_recording)
        self.stopBtn.setEnabled(False)
        btn_row1.addWidget(self.startBtn)
        btn_row1.addWidget(self.stopBtn)
        ctrl_layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.openDownloadBtn = QPushButton('📂 录播目录')
        self.openDownloadBtn.setObjectName('ghostBtn')
        self.openDownloadBtn.clicked.connect(self.open_download_folder)
        self.logBtn = QPushButton('📄 日志目录')
        self.logBtn.setObjectName('ghostBtn')
        self.logBtn.clicked.connect(self.open_log_folder)
        btn_row2.addWidget(self.openDownloadBtn)
        btn_row2.addWidget(self.logBtn)
        ctrl_layout.addLayout(btn_row2)
        left_layout.addWidget(ctrl_group)

        top_splitter.addWidget(left_widget)

        # ===== 中：录制配置 =====
        mid_widget = QWidget()
        mid_layout = QVBoxLayout(mid_widget)
        mid_layout.setContentsMargins(0, 0, 0, 0)
        mid_layout.setSpacing(12)

        config_group = QGroupBox('⚙️ 录制配置')
        config_layout = QFormLayout(config_group)
        config_layout.setSpacing(6)
        config_layout.setContentsMargins(4, 6, 4, 4)
        config_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.qualityCombo = QComboBox()
        self.qualityCombo.addItems(QUALITY_CHOICES)
        self.qualityCombo.setToolTip(
            '默认画质：新添加的直播间、以及地址行里没单独写画质的，都用这个档位。\n'
            '想给某个直播间单独设画质，在左边「直播地址管理」里选中它再点「应用到选中」。\n'
            '同一个直播间可以挂两条不同画质（例如原画 + 超清）同时录，用来做对比。')
        config_layout.addRow('默认画质：', self.qualityCombo)

        self.formatCombo = QComboBox()
        self.formatCombo.addItems(['ts', 'mkv', 'flv', 'mp4', 'mp3音频', 'm4a音频'])
        config_layout.addRow('保存格式：', self.formatCombo)

        self.delaySpin = QSpinBox()
        self.delaySpin.setRange(10, 3600)
        self.delaySpin.setValue(60)
        self.delaySpin.setSuffix(' 秒')
        config_layout.addRow('监测间隔：', self.delaySpin)

        self.threadSpin = QSpinBox()
        self.threadSpin.setRange(1, 10)
        self.threadSpin.setValue(3)
        config_layout.addRow('并发线程数：', self.threadSpin)

        # ---- 保存路径（新增，留空则用程序目录下的 downloads） ----
        save_path_row = QWidget()
        save_path_layout = QHBoxLayout(save_path_row)
        save_path_layout.setContentsMargins(0, 0, 0, 0)
        save_path_layout.setSpacing(6)
        self.savePathInput = QLineEdit()
        self.savePathInput.setPlaceholderText(f'留空 = 默认路径（{DEFAULT_DOWNLOAD_DIR}）')
        self.savePathInput.setToolTip(
            '留空 = 用默认路径（程序目录下的 downloads）。\n'
            '想换到别的盘，在这里填路径或点「浏览」选择；只有你填了/选了才会写进配置。\n'
            f'填盘根（如 F:\\）时，程序会在其下自动建「{PLATFORM_DIR_NAME}\\<主播名>」子目录，\n'
            '不会把文件直接堆在盘根上。\n'
            '填了具体路径之后才会去检测该目录能否创建、能否写入；不可写时自动回退到默认目录。'
        )
        self.savePathInput.editingFinished.connect(self.update_save_path_hint)
        browseBtn = QPushButton('浏览')
        browseBtn.setObjectName('ghostBtn')
        browseBtn.clicked.connect(self.browse_save_path)
        save_path_layout.addWidget(self.savePathInput, 1)
        save_path_layout.addWidget(browseBtn)
        config_layout.addRow('保存路径：', save_path_row)

        self.authorCheck = QCheckBox('按主播名建文件夹')
        config_layout.addRow('文件夹：', self.authorCheck)

        self.splitCheck = QCheckBox('开启分段录制')
        config_layout.addRow('分段录制：', self.splitCheck)

        self.splitTime = QSpinBox()
        self.splitTime.setRange(60, 7200)
        self.splitTime.setValue(1800)
        self.splitTime.setSuffix(' 秒')
        config_layout.addRow('分段时长：', self.splitTime)

        self.mp4Check = QCheckBox('录制完成自动转MP4')
        config_layout.addRow('自动转码：', self.mp4Check)

        self.detailCheck = QCheckBox('收尾时做源流详细体检')
        self.detailCheck.setChecked(True)
        self.detailCheck.setToolTip(
            '收尾时只读扫一遍刚录好的分段，把抖音推流 SDK 写在画面里的元数据抽出来，\n'
            '在日志里报告「主播端自报帧率 / 码率 / 时间轴漂移 / CDN 断流次数」。\n'
            '用来判断掉帧、音画不同步这类问题是不是主播端推流造成的。\n\n'
            '开销：不解码、不转码，只多读一遍文件，每段大约 2~3 秒（机械盘会更久）；\n'
            '只发生在“分段收尾”阶段，录制过程完全不受影响。\n'
            '不想多读一遍文件就取消勾选。'
        )
        config_layout.addRow('源流体检：', self.detailCheck)

        self.saveBtn = QPushButton('💾 保存配置')
        self.saveBtn.setObjectName('saveBtn')
        self.saveBtn.clicked.connect(self.save_config)
        config_layout.addRow(self.saveBtn)

        mid_layout.addWidget(config_group)
        mid_layout.addStretch()
        top_splitter.addWidget(mid_widget)

        # ===== 右：运行日志 =====
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        log_group = QGroupBox('📋 运行日志')
        log_layout = QVBoxLayout(log_group)
        log_layout.setSpacing(8)
        self.logText = QTextEdit()
        self.logText.setReadOnly(True)
        log_layout.addWidget(self.logText, 1)
        log_btn_row = QHBoxLayout()
        log_btn_row.addStretch()
        self.clearLogBtn = QPushButton('清空日志')
        self.clearLogBtn.setObjectName('ghostBtn')
        self.clearLogBtn.clicked.connect(self.clear_log)
        log_btn_row.addWidget(self.clearLogBtn)
        log_layout.addLayout(log_btn_row)
        right_layout.addWidget(log_group, 1)

        top_splitter.addWidget(right_widget)
        top_splitter.setSizes([330, 400, 470])
        top_splitter.setChildrenCollapsible(False)

        # ===== 下：录制状态看板 =====
        board_group = QGroupBox('📊 录制状态看板')
        board_layout = QVBoxLayout(board_group)
        board_layout.setSpacing(8)

        self.statusTable = QTableWidget(0, 8)
        self.statusTable.setHorizontalHeaderLabels(
            ['主播', '状态', '流状态', '质量', '已录时长', '分段', '产出大小', '保存目录']
        )
        self.statusTable.verticalHeader().setVisible(False)
        self.statusTable.setAlternatingRowColors(True)
        self.statusTable.setShowGrid(False)
        self.statusTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.statusTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.statusTable.setWordWrap(False)
        header_view = self.statusTable.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        header_view.setToolTip(HEALTH_LEGEND)
        self.statusTable.verticalHeader().setDefaultSectionSize(30)
        self.statusTable.setMinimumHeight(150)
        board_layout.addWidget(self.statusTable, 1)

        self.boardSummary = QLabel('录制进程未运行')
        self.boardSummary.setObjectName('summaryLabel')
        self.boardSummary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        board_layout.addWidget(self.boardSummary)

        board_group.setMinimumHeight(235)
        vertical_splitter.addWidget(top_splitter)
        vertical_splitter.addWidget(board_group)
        vertical_splitter.setSizes([520, 320])
        vertical_splitter.setChildrenCollapsible(False)
        main_layout.addWidget(vertical_splitter, 1)

        self.append_log('程序启动完成')
        self.append_log(f'工作目录: {SCRIPT_PATH}')
        self.append_log(f'版本: {APP_VERSION}')

    # ---------- 保存路径 ----------
    def current_save_path_input(self) -> str:
        return self.savePathInput.text().strip()

    def resolve_save_path(self) -> str:
        """把界面上的保存路径解析成绝对路径"""
        raw = self.current_save_path_input()
        if not raw or raw in ('不填则默认', '默认', 'default'):
            return DEFAULT_DOWNLOAD_DIR
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if not os.path.isabs(expanded):
            expanded = os.path.join(SCRIPT_PATH, expanded)
        return os.path.normpath(expanded)

    def is_using_default_save_path(self) -> bool:
        """当前是否就是默认路径。默认路径是程序自己的目录，不必再探测判断"""
        try:
            return os.path.normcase(self.resolve_save_path()) == os.path.normcase(DEFAULT_DOWNLOAD_DIR)
        except Exception:
            return False

    def update_save_path_hint(self):
        """刷新保存路径提示。默认路径直接标注「默认」，用户改过之后才做可用性判断"""
        resolved = self.resolve_save_path()
        record_dir = os.path.join(resolved, PLATFORM_DIR_NAME, '<主播名>')

        if self.is_using_default_save_path():
            self.savePathHint.setText(f'保存路径：{record_dir}\\（默认，输入框留空即可）')
            self.savePathHint.setToolTip(
                f'根目录：{resolved}\n录制目录：{record_dir}\n'
                '输入框留空就是这里（程序目录下的 downloads）；'
                '想换到别的盘，在输入框里填路径或点「浏览」选择。'
            )
            return

        ok, err = probe_dir_writable(resolved)
        if not ok:
            self.savePathHint.setText(
                f'⚠️ 保存路径不可用：{resolved}　（{err}）\n'
                f'录制时将自动回退到默认目录 {DEFAULT_DOWNLOAD_DIR}'
            )
            self.savePathHint.setToolTip(f'{err}\n{suggest_save_path_hint(resolved)}')
            return
        if is_drive_root(resolved):
            tip = f'保存路径：{resolved}（盘根）　→　实际录制到 {record_dir}\\'
        else:
            tip = f'保存路径：{record_dir}\\'
        self.savePathHint.setText(tip)
        self.savePathHint.setToolTip(f'根目录：{resolved}\n录制目录：{record_dir}')

    def browse_save_path(self):
        start_dir = self.resolve_save_path()
        if not os.path.isdir(start_dir):
            start_dir = SCRIPT_PATH
        chosen = QFileDialog.getExistingDirectory(self, '选择录播保存目录', start_dir)
        if chosen:
            self.savePathInput.setText(os.path.normpath(chosen))
            self.savePathInput.setCursorPosition(0)
            self.update_save_path_hint()
            resolved = self.resolve_save_path()
            if self.is_using_default_save_path():
                return
            ok, err = probe_dir_writable(resolved)
            if not ok:
                QMessageBox.warning(
                    self, '该文件夹不可写',
                    f'{err}\n\n路径：{resolved}\n\n{suggest_save_path_hint(resolved)}'
                )
            elif is_drive_root(resolved):
                QMessageBox.information(
                    self, '已选择盘根',
                    f'你选择的是盘根 {resolved}\n\n'
                    f'程序会在其下自动创建「{PLATFORM_DIR_NAME}\\<主播名>」子目录，\n'
                    f'不会把文件直接堆在盘根上。'
                )

    def open_download_folder(self):
        target = self.effective_save_path or self.resolve_save_path()
        platform_dir = os.path.join(target, PLATFORM_DIR_NAME)
        if os.path.isdir(platform_dir):
            target = platform_dir  # 已有录播目录时直接打开它，更方便
        try:
            os.makedirs(target, exist_ok=True)
            os.startfile(target)
            self.append_log(f'已打开录播目录: {target}')
        except Exception as e:
            QMessageBox.warning(self, '提示', f'打开录播目录失败: {e}')

    # ---------- 配置 ----------
    def load_config(self):
        try:
            config = read_config()
            quality = config.get('原画|超清|高清|标清|流畅', '原画')
            idx = self.qualityCombo.findText(quality)
            if idx >= 0:
                self.qualityCombo.setCurrentIndex(idx)

            fmt = config.get('视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', 'ts')
            idx = self.formatCombo.findText(fmt)
            if idx >= 0:
                self.formatCombo.setCurrentIndex(idx)

            try:
                self.delaySpin.setValue(int(config.get('循环时间(秒)', '60')))
            except Exception:
                pass
            try:
                self.threadSpin.setValue(int(config.get('同一时间访问网络的线程数', '3')))
            except Exception:
                pass

            save_path = str(config.get(SAVE_PATH_KEY, '') or '').strip()
            if save_path in ('不填则默认', '默认', 'default'):
                save_path = ''
            # 留空 = 使用默认路径（程序目录\downloads）。
            # 这里只做“显示”，不再把默认路径写进 config.ini：
            # 配置保持原样（发出去的包写的是什么就是什么），
            # 只有用户自己填了路径或点了「浏览」选择，才会写进配置。
            self.savePathInput.setText(save_path)
            # 路径较长时让输入框从开头显示，避免只看到结尾一截
            self.savePathInput.setCursorPosition(0)
            if not save_path:
                self.append_log(f'保存路径未设置，将使用默认目录: {DEFAULT_DOWNLOAD_DIR}')

            self.authorCheck.setChecked(config.get('保存文件夹是否以作者区分', '是') == '是')
            self.splitCheck.setChecked(config.get('分段录制是否开启', '否') == '是')
            try:
                self.splitTime.setValue(int(config.get('视频分段时间(秒)', '1800')))
            except Exception:
                pass
            self.mp4Check.setChecked(config.get('录制完成后自动转为mp4格式', '否') == '是')
            self.detailCheck.setChecked(config.get('源流详细体检(是/否)', '是') == '是')
        except Exception as e:
            self.append_log(f'读取配置失败: {e}')

    def save_config(self):
        try:
            # 输入框留空 = 用默认路径，这里不再回填成具体路径
            # （否则会把默认值写进配置，客户没自己选也会被改掉）
            self.save_config_quietly()
            resolved = self.resolve_save_path()

            if self.is_using_default_save_path():
                self.append_log(f'配置已保存，保存路径: {resolved}（默认）')
                QMessageBox.information(self, '提示', f'配置已保存！\n\n保存路径：{resolved}（默认路径）')
                self.update_save_path_hint()
                return

            ok, err = probe_dir_writable(resolved)
            if ok:
                self.append_log(f'配置已保存，保存路径: {resolved}')
                QMessageBox.information(self, '提示', f'配置已保存！\n\n保存路径：{resolved}')
            else:
                self.append_log(f'保存路径不可用: {resolved}（{err}）')
                QMessageBox.warning(
                    self, '保存路径不可用',
                    f'{err}\n\n路径：{resolved}\n\n'
                    f'{suggest_save_path_hint(resolved)}\n\n'
                    f'配置已保存，但录制时会自动回退到默认目录：\n{DEFAULT_DOWNLOAD_DIR}'
                )
            self.update_save_path_hint()
        except Exception as e:
            self.append_log(f'保存配置失败: {e}')

    def save_config_quietly(self):
        """把界面上的配置写入 config.ini（不弹提示框）"""
        try:
            write_config_value('原画|超清|高清|标清|流畅', self.qualityCombo.currentText())
            write_config_value('视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', self.formatCombo.currentText())
            write_config_value('循环时间(秒)', str(self.delaySpin.value()))
            write_config_value('同一时间访问网络的线程数', str(self.threadSpin.value()))
            # 保存路径：留空就写空值（内核运行时用默认目录），
            # 只有用户自己填了/选了路径才会写具体路径进配置。
            write_config_value(SAVE_PATH_KEY, self.current_save_path_input())
            write_config_value('保存文件夹是否以作者区分', '是' if self.authorCheck.isChecked() else '否')
            write_config_value('分段录制是否开启', '是' if self.splitCheck.isChecked() else '否')
            write_config_value('视频分段时间(秒)', str(self.splitTime.value()))
            write_config_value('录制完成后自动转为mp4格式', '是' if self.mp4Check.isChecked() else '否')
            write_config_value('源流详细体检(是/否)', '是' if self.detailCheck.isChecked() else '否')
        except Exception as e:
            self.append_log(f'写入配置失败: {e}')

    # ---------- 地址管理 ----------
    def add_url_item(self, url: str):
        """把配置里的一行地址加进列表（显示友好文本，原始行存在 UserRole 里）"""
        raw = (url or '').strip()
        item = QListWidgetItem()
        self.update_url_item(item, raw)
        self.urlList.addItem(item)

    def update_url_item(self, item, raw: str):
        """刷新一条列表项的显示和悬停提示；原始配置行存在 UserRole 里"""
        quality, url, _ = split_url_line(raw)
        item.setData(Qt.ItemDataRole.UserRole, raw)
        item.setData(Qt.ItemDataRole.DisplayRole, url_line_display(raw))
        item.setToolTip(f'{url}\n画质：{quality or "默认（用录制配置里的默认画质）"}')

    @staticmethod
    def item_raw_line(item) -> str:
        """取出一条列表项对应的配置原始行"""
        raw = item.data(Qt.ItemDataRole.UserRole)
        return str(raw) if raw else item.text()

    def on_url_selection_changed(self):
        """选中某一行时，把它的画质同步到画质选择框里"""
        item = self.urlList.currentItem()
        if item is None:
            return
        quality, _, _ = split_url_line(self.item_raw_line(item))
        if quality in QUALITY_CHOICES:
            self.itemQualityCombo.setCurrentText(quality)

    def _find_quality_conflict(self, target_rows, quality: str) -> str:
        """检查把 target_rows 改成 quality 之后，会不会出现“同地址+同画质”的重复"""
        target = set(target_rows)
        seen = {}
        for row in range(self.urlList.count()):
            line_quality, url, _ = split_url_line(self.item_raw_line(self.urlList.item(row)))
            if not url:
                continue
            if row in target:
                line_quality = quality
            key = (normalize_url_for_compare(url), line_quality or '默认画质')
            if key in seen:
                return f'{url} 会变成两条「{line_quality or "默认画质"}」'
            seen[key] = row
        return ''

    def find_duplicate_entries(self) -> str:
        """找出列表里“同一个地址 + 同一个画质”的重复项，返回说明文字（没有则空串）"""
        seen = {}
        for row in range(self.urlList.count()):
            quality, url, _ = split_url_line(self.item_raw_line(self.urlList.item(row)))
            if not url:
                continue
            key = (normalize_url_for_compare(url), quality or '默认画质')
            if key in seen:
                return f'{url}（{quality or "默认画质"}）'
            seen[key] = row
        return ''

    def apply_quality_to_selected(self):
        rows = [self.urlList.row(item) for item in self.urlList.selectedItems()]
        if not rows:
            QMessageBox.warning(self, '提示', '请先在列表里选中要改的直播间！')
            return
        self._apply_quality(rows, self.itemQualityCombo.currentText())

    def apply_quality_to_all(self):
        rows = list(range(self.urlList.count()))
        if not rows:
            return
        self._apply_quality(rows, self.itemQualityCombo.currentText())

    def _apply_quality(self, rows, quality: str):
        conflict = self._find_quality_conflict(rows, quality)
        if conflict:
            QMessageBox.warning(
                self, '画质重复',
                f'不能这样改：{conflict}。\n\n'
                '同一个直播间、同一个画质只需要挂一条；\n'
                '想同时录两个档位做对比，就把它们分别设成不同的画质（例如一条原画、一条超清）。')
            return
        changed = 0
        for row in rows:
            item = self.urlList.item(row)
            raw = self.item_raw_line(item)
            _, url, extras = split_url_line(raw)
            if not url:
                continue
            new_raw = make_url_line(quality, url, extras)
            if new_raw != raw:
                self.update_url_item(item, new_raw)
                changed += 1
        self.persist_url_list(f'已把 {changed} 个直播间的画质设为「{quality}」')

    def load_urls(self):
        try:
            self.urlList.clear()
            urls = read_urls()
            for url in urls:
                self.add_url_item(url)
            self.update_url_count()
            self.append_log(f'已加载 {len(urls)} 个直播地址')
        except Exception as e:
            self.append_log(f'读取地址失败: {e}')

    def update_url_count(self):
        self.urlCountLabel.setText(f'已添加 {self.urlList.count()} 个主播')

    def add_url(self):
        text = self.urlInput.text().strip()
        if not text:
            QMessageBox.warning(self, '提示', '请输入直播地址！')
            return
        try:
            quality, url, extras = split_url_line(text)
            if not url:
                QMessageBox.warning(self, '提示',
                                    '没认出直播地址，请检查格式（例如 https://live.douyin.com/123456）')
                return
            # 没单独指定画质时，用「录制配置」里的默认画质
            if not quality:
                quality = self.qualityCombo.currentText()
            for row in range(self.urlList.count()):
                line_quality, line_url, _ = split_url_line(
                    self.item_raw_line(self.urlList.item(row)))
                if normalize_url_for_compare(line_url) != normalize_url_for_compare(url):
                    continue
                if (line_quality or '') == quality:
                    QMessageBox.warning(
                        self, '地址重复',
                        f'这个直播间已经用「{quality}」在录了，重复添加不会生效。\n\n'
                        '同一个地址 + 同一个画质只需要一条；\n'
                        '想同时录两个画质做对比，请把其中一条的画质改成别的（例如「超清」）。')
                    return
                self.append_log(f'同一个直播间挂了多个画质：{url}'
                                f'（已有「{line_quality or "默认画质"}」，再挂「{quality}」）')
            raw = make_url_line(quality, url, extras)
            urls = read_urls()
            urls.append(raw)
            write_urls(urls)
            self.add_url_item(raw)
            self.urlInput.clear()
            self.update_url_count()
            self.append_log(f'添加地址[{quality}]: {url}')
        except Exception as e:
            self.append_log(f'添加地址失败: {e}')

    def del_url(self):
        current = self.urlList.currentRow()
        if current < 0:
            QMessageBox.warning(self, '提示', '请先选中要删除的地址！')
            return
        self.urlList.takeItem(current)
        self.persist_url_list('删除地址成功')

    def clear_urls(self):
        if self.urlList.count() == 0:
            return
        reply = QMessageBox.question(
            self, '提示', '确定清空全部直播地址吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.urlList.clear()
        self.persist_url_list('已清空地址列表')

    def persist_url_list(self, log_text):
        try:
            urls = [self.item_raw_line(self.urlList.item(i)) for i in range(self.urlList.count())]
            write_urls(urls)
            self.update_url_count()
            self.append_log(log_text)
        except Exception as e:
            self.append_log(f'保存地址列表失败: {e}')

    # ---------- 录制控制 ----------
    def start_recording(self):
        if self.urlList.count() == 0:
            QMessageBox.warning(self, '提示', '请先添加直播地址！')
            if not self.isVisible():
                self.show_main_window()
            return
        # 同一个直播间 + 同一个画质只能挂一条（重复的那条会被内核忽略）
        duplicate = self.find_duplicate_entries()
        if duplicate:
            reply = QMessageBox.question(
                self, '地址重复',
                f'列表里有重复项：{duplicate}\n\n'
                '同一个直播间、同一个画质只需要一条，重复的那条会被忽略。\n'
                '想同时录两个画质做对比，请把其中一条的画质改成别的。\n\n'
                '要忽略重复项，继续录制吗？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.save_config_quietly()
        self.recorder.start_recording()
        self.startBtn.setEnabled(False)
        self.stopBtn.setEnabled(True)
        self.update_tray_state(True)

    def stop_recording(self):
        """点「停止录制」：发请求后立刻返回，用定时器轮询收尾进度。

        以前是直接阻塞等内核把当前分段收尾（最长 180 秒），界面在这段时间里
        完全没反应，看着像卡死。现在改成一边收尾一边显示进度。
        """
        if self._stop_timer is not None:
            return
        stop_timeout = self._estimate_stop_timeout()
        if not self.recorder.begin_graceful_stop(timeout=stop_timeout):
            self.recorder.finish_stop()
            self._after_stop_ui()
            return
        self.startBtn.setEnabled(False)
        self.stopBtn.setEnabled(False)
        self.stopBtn.setText('⏳ 正在收尾…')
        self._stop_started = time.time()
        self.update_status('收尾中')
        self.append_log('已请求停止：正在收尾当前分段（修复时间戳 / 转 mp4），完成后会自动结束。')
        self.boardSummary.setText('⏳ 正在收尾当前分段（修复时间戳 / 转 mp4）…')
        self._stop_timer = QTimer(self)
        self._stop_timer.setInterval(500)
        self._stop_timer.timeout.connect(self._poll_stop)
        self._stop_timer.start()

    def _estimate_stop_timeout(self) -> float:
        """估算“停止录制后最多等多久收尾”。

        收尾要对每个分段做「修时间戳 + 源流体检 + 转 mp4」，实测 4GB 的段每个
        十几到二十秒：16 个分段就要 155 秒，旧的固定 180 秒上限已经贴着天花板，
        分段再多就会被强杀、留下没转的 .ts。
        这里按看板上的分段数估算，取 10~30 分钟；正常情况收尾一结束就立刻返回，
        给足上限不会让用户多等。
        """
        segments = 0
        try:
            payload = self.read_runtime_status() or {}
            for item in payload.get('items') or []:
                segments += int(item.get('segments') or 0)
        except Exception:
            segments = 0
        return max(600.0, min(1800.0, 60.0 + 20.0 * segments))

    def _poll_stop(self):
        """收尾进度轮询：还在收尾就刷新提示，结束/超时就走收尾收场"""
        state, left = self.recorder.stop_wait_state()
        if state == 'waiting':
            elapsed = int(time.time() - self._stop_started)
            self.update_status('收尾中')
            self.boardSummary.setText(
                f'⏳ 正在收尾当前分段（修复时间戳 / 转 mp4）：已等待 {elapsed} 秒，'
                f'最长再等 {int(left)} 秒；收尾期间请不要关闭程序。')
            return
        if state == 'timeout':
            self.append_log('⚠️ 收尾等待超过上限，强制结束录制进程；'
                            '可能有个别分段没来得及转 mp4（.ts 会保留在目录里）')
        else:
            self.append_log('收尾完成，录制已停止')
        self._stop_timer.stop()
        self._stop_timer.deleteLater()
        self._stop_timer = None
        self.recorder.finish_stop()
        self._after_stop_ui()

    def _after_stop_ui(self):
        """停止流程收场：按钮/状态/看板复位"""
        self.stopBtn.setText('⏹ 停止录制')
        self.startBtn.setEnabled(True)
        self.stopBtn.setEnabled(False)
        self.update_tray_state(False)
        self.update_status('已停止')
        self.refresh_status_board()

    def quit_app(self):
        """退出程序：先停录制（含 ffmpeg 子进程），再收托盘、退出 Qt"""
        try:
            # 退出等待期间把按钮锁住：不然用户还能点“开始录制”，
            # 又起一个录制进程，紧接着就被我们自己杀掉
            self.startBtn.setEnabled(False)
            self.stopBtn.setEnabled(False)
            stop_timeout = self._estimate_stop_timeout()
            if self.recorder.begin_graceful_stop(timeout=stop_timeout):
                self.update_status('收尾中')
                self.append_log('退出前正在收尾当前分段（修复时间戳 / 转 mp4）…')
                # 退出时是同步等待，但要一边等一边刷新界面，别让窗口卡成白板
                deadline = time.time() + stop_timeout + 1
                while time.time() < deadline:
                    state, _ = self.recorder.stop_wait_state()
                    if state != 'waiting':
                        break
                    QApplication.processEvents()
                    time.sleep(0.2)
            self.recorder.finish_stop()
        except Exception as e:
            self.append_log(f'退出前停止录制失败: {e}')
        self.save_geometry()
        if self.tray_icon:
            self.tray_icon.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def confirm_quit(self):
        """托盘菜单里的「退出程序」：录制中先确认一次"""
        if self.recorder.is_process_running():
            reply = QMessageBox.question(
                self, '退出程序',
                '正在录制中，确定要退出程序吗？\n\n退出会先停止录制并收尾当前分段。',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.quit_app()

    # ---------- 文件夹 ----------
    def open_log_folder(self):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.startfile(LOG_DIR)
            self.append_log(f'已打开日志文件夹: {LOG_DIR}')
        except Exception as e:
            QMessageBox.warning(self, '提示', f'打开日志文件夹失败: {e}')

    # ---------- 状态看板 ----------
    def read_runtime_status(self):
        """读取内核写出的运行状态；数据过期则返回 None"""
        if not os.path.exists(STATUS_FILE):
            return None
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception:
            return None
        if time.time() - float(payload.get('ts', 0)) > STATUS_STALE_SECONDS:
            return None
        return payload

    def build_health_tooltip(self, item):
        """把流健康状态拼成悬浮提示：详情 + 坏流次数 + 线路 + 更新时间"""
        detail = str(item.get('health_detail') or '').strip()
        source = str(item.get('health_source') or '').strip()
        live_type = str(item.get('live_type') or '').strip()
        bad_count = int(item.get('health_bad_count') or 0)
        updated = item.get('health_updated') or 0

        lines = []
        if detail:
            lines.append(detail)
        if live_type:
            lines.append(f'直播间形态：{live_type}')
        if bad_count:
            lines.append(f'连续坏流 {bad_count} 次')
        if source:
            lines.append(f'线路：{source}')
        if updated:
            try:
                lines.append('更新于 ' + datetime.datetime.fromtimestamp(float(updated)).strftime('%H:%M:%S'))
            except Exception:
                pass
        return '\n'.join(lines) if lines else HEALTH_LEGEND

    def refresh_status_board(self):
        payload = self.read_runtime_status()
        if payload is None:
            self.statusTable.setRowCount(0)
            if self.recorder.is_recording:
                self.boardSummary.setText('正在等待录制进程上报状态…')
            else:
                self.boardSummary.setText('录制进程未运行')
            return

        items = payload.get('items') or []
        self.effective_save_path = payload.get('save_path') or self.resolve_save_path()

        self.statusTable.setRowCount(len(items))
        recording_count = 0
        live_count = 0
        total_size = 0
        health_counts = {'good': 0, 'warn': 0, 'bad': 0, 'switched': 0}
        for row, item in enumerate(items):
            is_recording = bool(item.get('recording'))
            is_live = bool(item.get('is_live'))
            if is_recording:
                recording_count += 1
                state_text, state_color = '● 录制中', '#27ae60'
            elif is_live:
                live_count += 1
                state_text, state_color = '● 直播中', '#2980b9'
            else:
                state_text, state_color = '○ 等待开播', '#e67e22'

            # 团播 / 个播：内核探一次写进状态，这里直接跟在状态后面显示，
            # 一眼就能看出这次录的是团播还是个播
            live_type = str(item.get('live_type') or '').strip()
            if live_type:
                state_text = f'{state_text} · {live_type}'

            # ---- 流健康：一眼看出这次取到的流有没有画面 ----
            health_key = str(item.get('health') or '').strip()
            health_text, health_color, health_bg = HEALTH_STYLES.get(
                health_key, HEALTH_STYLES[''])
            if health_key in health_counts:
                health_counts[health_key] += 1
            health_tip = self.build_health_tooltip(item)

            size_bytes = int(item.get('total_size') or 0)
            total_size += size_bytes

            values = [
                str(item.get('anchor') or item.get('name') or ''),
                state_text,
                health_text,
                str(item.get('quality') or ''),
                format_duration(item.get('elapsed')) if is_recording else '--',
                str(int(item.get('segments') or 0)),
                format_size(size_bytes),
                str(item.get('save_dir') or ''),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col in (1, 2, 3, 4, 5, 6):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 1:
                    cell.setForeground(QBrush(QColor(state_color)))
                if col == 2:
                    cell.setForeground(QBrush(QColor(health_color)))
                    cell.setToolTip(health_tip)
                    if health_key in ('bad', 'warn'):
                        font = cell.font()
                        font.setBold(True)
                        cell.setFont(font)
                if col == 7:
                    cell.setToolTip(value)
                # 需要留意的流状态：给整行铺一层浅底色，扫一眼就能定位到是哪一行
                if health_bg:
                    cell.setBackground(QBrush(QColor(health_bg)))
                self.statusTable.setItem(row, col, cell)

        waiting = len(items) - recording_count - live_count
        health_summary = ' · '.join([
            f'✅ 正常 {health_counts["good"]}',
            f'⚠️ 可疑 {health_counts["warn"]}',
            f'❌ 坏流 {health_counts["bad"]}',
        ])
        if health_counts['switched']:
            health_summary += f' · 🔁 已换线路 {health_counts["switched"]}'

        self.boardSummary.setText(
            f'录制中 {recording_count} 个 · 直播中 {live_count} 个 · 等待开播 {waiting} 个 '
            f'· 累计产出 {format_size(total_size)}'
            f'　|　流状态：{health_summary}'
            f'　|　状态更新：{payload.get("time", "--")}'
        )
        # 有坏流时把提示条标红，提醒去看具体是哪个主播
        self.boardSummary.setStyleSheet('color: #c0392b;' if health_counts['bad'] else '')

    # ---------- 日志 ----------
    def clear_log(self):
        self.logText.clear()
        self.append_log('日志已清空')

    def append_log(self, text):
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
        self.logText.append(f'[{timestamp}] {text}')
        scrollbar = self.logText.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_status(self, status):
        if status == '录制中':
            self.statusLabel.setText('▶ 录制中')
            self.statusLabel.setStyleSheet(
                'font-size: 14px; font-weight: bold; color: white;'
                'background: rgba(39, 174, 96, 0.88); padding: 7px 16px; border-radius: 10px;'
            )
            self.update_tray_state(True)
            self.notify_tray(APP_NAME, '已开始监测直播间，主播开播后自动录制')
        elif status == '收尾中':
            # 停止录制后内核还在“修复时间戳 / 转 mp4”，这段时间给个明确提示，
            # 否则用户会以为程序卡住了（以前界面是真的没反应）
            waited = int(time.time() - self._stop_started) if self._stop_started else 0
            self.statusLabel.setText(f'⏳ 正在收尾 {waited}s…' if waited else '⏳ 正在收尾…')
            self.statusLabel.setStyleSheet(
                'font-size: 14px; font-weight: bold; color: white;'
                'background: rgba(230, 126, 34, 0.92); padding: 7px 16px; border-radius: 10px;'
            )
        else:
            self.statusLabel.setText('⏹ 未录制')
            self.statusLabel.setStyleSheet(
                'font-size: 14px; font-weight: bold; color: white;'
                'background: rgba(255,255,255,0.18); padding: 7px 16px; border-radius: 10px;'
            )
            self.update_tray_state(False)

    # ---------- 窗口行为 ----------
    def restore_geometry(self):
        geometry = self.settings.value('window/geometry')
        if geometry is not None:
            self.restoreGeometry(geometry)

    def save_geometry(self):
        self.settings.setValue('window/geometry', self.saveGeometry())

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() and self.tray_icon:
            # 最小化时收进托盘，不在任务栏留占位
            QTimer.singleShot(0, self.hide)
            if not self.tray_hint_shown:
                self.tray_hint_shown = True
                self.notify_tray(APP_NAME, '已最小化到托盘，录制会在后台继续')
        super().changeEvent(event)

    def closeEvent(self, event):
        """点关闭时明确询问：退出程序 / 最小化到托盘继续录制 / 取消。

        以前这里问的是“要如何处理”，且「是」= 收进托盘继续录，
        很容易让人以为已经退出了，其实直播还在后台录制。
        """
        recording = self.recorder.is_process_running()
        box = QMessageBox(self)
        box.setWindowTitle('退出程序')
        box.setIcon(QMessageBox.Icon.Question)
        box.setText('确定要退出程序吗？')
        if recording:
            box.setInformativeText(
                '当前正在录制中。\n'
                '· 退出程序：停止录制（会先收尾当前分段）并关闭程序\n'
                '· 最小化到托盘：程序留在后台，录制继续'
            )
        else:
            box.setInformativeText('退出后程序（含托盘图标）将关闭。')

        exit_btn = box.addButton('退出程序', QMessageBox.ButtonRole.AcceptRole)
        tray_btn = None
        if self.tray_icon is not None:
            tray_btn = box.addButton('最小化到托盘', QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is exit_btn:
            self.quit_app()
            event.accept()
            return
        if tray_btn is not None and clicked is tray_btn:
            self.save_geometry()
            self.hide()
            self.notify_tray(APP_NAME, '已最小化到托盘，录制会在后台继续')
            event.ignore()
            return
        event.ignore()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # 主字体走微软雅黑，并带上 emoji 回退字体，保证界面里的符号正常显示
    ui_font = QFont('Microsoft YaHei UI', 10)
    ui_font.setFamilies(['Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI Emoji'])
    app.setFont(ui_font)
    app.setWindowIcon(load_app_icon())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
