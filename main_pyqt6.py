#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音直播录制工具 - PyQt6 桌面版
适配原项目的配置文件格式
"""

import os
import sys
import subprocess
import configparser
import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QComboBox, QCheckBox, QGroupBox, QTextEdit, QMessageBox,
    QSpinBox, QFormLayout, QSplitter
)
from PyQt6.QtCore import Qt, QProcess, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QPalette

# ========== 路径配置 ==========
SCRIPT_PATH = os.path.split(os.path.realpath(sys.argv[0]))[0]
CONFIG_FILE = os.path.join(SCRIPT_PATH, 'config', 'config.ini')
URL_CONFIG_FILE = os.path.join(SCRIPT_PATH, 'config', 'URL_config.ini')
DOUYIN_ONLY = os.path.join(SCRIPT_PATH, 'douyin_only.py')
LOG_DIR = os.path.join(SCRIPT_PATH, 'logs')
TEXT_ENCODING = 'utf-8-sig'

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)


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


# ========== 录制进程管理 ==========
class RecorderManager(QObject):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.process = None
        self.is_recording = False

    def start_recording(self):
        if self.is_recording:
            return
        try:
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

    def _read_output(self):
        if not self.process:
            return
        # 更全面的过滤关键词，彻底过滤掉监控刷屏输出
        skip_keywords = [
            '共监测', '正在录制', '当前时间', '瞬时错误数', '同一时间访问网络',
            'xxxxxxxxxx', '循环时间', '排队读取', '是否显示', '直播源地址',
            '录制视频质量', '录制视频格式', '录制分段', '生成时间字幕',
            '没有正在录制', '没有正在监测', '录制中', '监测中',
            '线程数', '秒后', '当前时间', '瞬时错误',
        ]
        try:
            for line in self.process.stdout:
                try:
                    # 尝试多种编码解码，解决乱码问题
                    text = None
                    for encoding in ['utf-8', 'gbk', 'gb2312', 'gb18030']:
                        try:
                            text = line.decode(encoding).strip()
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue
                    if text is None:
                        text = line.decode('utf-8', errors='ignore').strip()
                    if text:
                        # 过滤掉监控刷屏输出
                        if not any(k in text for k in skip_keywords):
                            # 过滤掉纯数字、纯符号的行
                            if len(text) > 3 and not text.isdigit():
                                self.log_signal.emit(text)
                except Exception:
                    pass
        except Exception:
            pass
        self.is_recording = False
        self.status_signal.emit('已停止')
        self.log_signal.emit('录制已停止')

    def stop_recording(self):
        if not self.is_recording:
            return
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/IM', 'ffmpeg.exe'],
                             capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if self.process:
                self.process.terminate()
                self.process.kill()
                self.process = None
            self.is_recording = False
            self.status_signal.emit('已停止')
            self.log_signal.emit('停止录制')
        except Exception as e:
            self.log_signal.emit(f'停止录制出错: {e}')


# ========== 主窗口 ==========
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.recorder = RecorderManager()
        self.recorder.log_signal.connect(self.append_log)
        self.recorder.status_signal.connect(self.update_status)
        self.init_ui()
        self.load_config()
        self.load_urls()

    def init_ui(self):
        self.setWindowTitle('抖音直播录制工具')
        self.setMinimumSize(900, 700)
        self.setStyleSheet("""
            QMainWindow { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #667eea, stop:1 #764ba2); }
            QGroupBox {
                background: rgba(255, 255, 255, 0.97);
                border: none;
                border-radius: 16px;
                margin-top: 18px;
                padding-top: 22px;
                padding-left: 18px;
                padding-right: 18px;
                padding-bottom: 18px;
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
                padding: 10px 14px;
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
            QPushButton {
                padding: 11px 24px;
                border: none;
                border-radius: 10px;
                font-size: 14px;
                font-weight: bold;
                color: white;
            }
            QPushButton:hover { opacity: 0.88; }
            QPushButton:pressed { opacity: 0.75; }
            QPushButton:disabled { background: #bdc3c7; color: #7f8c8d; }
            QPushButton#addBtn { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667eea, stop:1 #764ba2); }
            QPushButton#delBtn { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e74c3c, stop:1 #c0392b); }
            QPushButton#startBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #27ae60, stop:1 #2ecc71);
                font-size: 17px; padding: 14px 36px; border-radius: 12px;
            }
            QPushButton#stopBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e74c3c, stop:1 #c0392b);
                font-size: 17px; padding: 14px 36px; border-radius: 12px;
            }
            QPushButton#saveBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f39c12, stop:1 #e67e22);
                padding: 12px 24px; border-radius: 10px;
            }
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
                border: 2px solid #e8ecf1; border-radius: 12px; padding: 12px;
                font-family: Consolas, Courier New, monospace; font-size: 12px;
                background: #1e1e2e; color: #cdd6f4;
            }
            QLabel { font-size: 13px; color: #2c3e50; }
            QLabel#titleLabel { font-size: 32px; font-weight: bold; color: white; padding: 10px 0; }
            QLabel#statusLabel { font-size: 18px; font-weight: bold; }
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
                background: rgba(102, 126, 234, 0.5); border-radius: 5px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(102, 126, 234, 0.8); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 标题
        title = QLabel('🎬 抖音直播录制工具')
        title.setStyleSheet('font-size: 28px; font-weight: bold; color: white;')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)

        # 状态行
        status_layout = QHBoxLayout()
        self.statusLabel = QLabel('⏹ 状态：未录制')
        self.statusLabel.setObjectName('statusLabel')
        self.statusLabel.setStyleSheet('font-size: 18px; font-weight: bold; color: white;')
        status_layout.addWidget(self.statusLabel)
        status_layout.addStretch()
        main_layout.addLayout(status_layout)

        # 主体：左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：地址管理 + 控制按钮
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(15)

        # 直播地址管理
        url_group = QGroupBox('📺 直播地址管理')
        url_layout = QVBoxLayout(url_group)
        url_input_layout = QHBoxLayout()
        self.urlInput = QLineEdit()
        self.urlInput.setPlaceholderText('请输入抖音直播间地址，如 https://live.douyin.com/xxxxxx')
        self.addBtn = QPushButton('添加')
        self.addBtn.setObjectName('addBtn')
        self.addBtn.clicked.connect(self.add_url)
        url_input_layout.addWidget(self.urlInput)
        url_input_layout.addWidget(self.addBtn)
        url_layout.addLayout(url_input_layout)

        self.urlList = QListWidget()
        url_layout.addWidget(self.urlList)

        url_btn_layout = QHBoxLayout()
        self.delBtn = QPushButton('删除选中')
        self.delBtn.setObjectName('delBtn')
        self.delBtn.clicked.connect(self.del_url)
        url_btn_layout.addWidget(self.delBtn)
        url_layout.addLayout(url_btn_layout)
        left_layout.addWidget(url_group)

        # 控制按钮
        ctrl_group = QGroupBox('🎮 录制控制')
        ctrl_layout = QHBoxLayout(ctrl_group)
        self.startBtn = QPushButton('▶ 开始录制')
        self.startBtn.setObjectName('startBtn')
        self.startBtn.clicked.connect(self.start_recording)
        self.stopBtn = QPushButton('⏹ 停止录制')
        self.stopBtn.setObjectName('stopBtn')
        self.stopBtn.clicked.connect(self.stop_recording)
        self.stopBtn.setEnabled(False)
        ctrl_layout.addWidget(self.startBtn)
        ctrl_layout.addWidget(self.stopBtn)
        left_layout.addWidget(ctrl_group)

        left_layout.addStretch()
        splitter.addWidget(left_widget)

        # 右侧：录制配置 + 日志
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(15)

        # 录制配置
        config_group = QGroupBox('⚙️ 录制配置')
        config_layout = QFormLayout(config_group)

        self.qualityCombo = QComboBox()
        self.qualityCombo.addItems(['原画', '超清', '高清', '标清', '流畅'])
        config_layout.addRow('视频质量：', self.qualityCombo)

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

        self.saveBtn = QPushButton('💾 保存配置')
        self.saveBtn.setObjectName('saveBtn')
        self.saveBtn.clicked.connect(self.save_config)
        config_layout.addRow(self.saveBtn)

        right_layout.addWidget(config_group)

        # 日志
        log_group = QGroupBox('📋 运行日志')
        log_layout = QVBoxLayout(log_group)
        self.logText = QTextEdit()
        self.logText.setReadOnly(True)
        log_layout.addWidget(self.logText)
        right_layout.addWidget(log_group)

        splitter.addWidget(right_widget)
        splitter.setSizes([450, 450])
        main_layout.addWidget(splitter)

        self.append_log('程序启动完成')
        self.append_log(f'工作目录: {SCRIPT_PATH}')

    def load_config(self):
        try:
            config = read_config()
            # 视频质量
            quality = config.get('原画|超清|高清|标清|流畅', '原画')
            idx = self.qualityCombo.findText(quality)
            if idx >= 0:
                self.qualityCombo.setCurrentIndex(idx)
            # 保存格式
            fmt = config.get('视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', 'ts')
            idx = self.formatCombo.findText(fmt)
            if idx >= 0:
                self.formatCombo.setCurrentIndex(idx)
            # 监测间隔（默认60秒=1分钟）
            delay = config.get('循环时间(秒)', '60')
            try:
                self.delaySpin.setValue(int(delay))
            except Exception:
                pass
            # 并发线程数
            threads = config.get('同一时间访问网络的线程数', '3')
            try:
                self.threadSpin.setValue(int(threads))
            except Exception:
                pass
            # 按主播名建文件夹
            author = config.get('保存文件夹是否以作者区分', '是')
            self.authorCheck.setChecked(author == '是')
            # 分段录制
            split = config.get('分段录制是否开启', '否')
            self.splitCheck.setChecked(split == '是')
            # 分段时间
            split_time = config.get('视频分段时间(秒)', '1800')
            try:
                self.splitTime.setValue(int(split_time))
            except Exception:
                pass
            # 自动转MP4
            mp4 = config.get('录制完成后自动转为mp4格式', '否')
            self.mp4Check.setChecked(mp4 == '是')
        except Exception as e:
            self.append_log(f'读取配置失败: {e}')

    def save_config(self):
        try:
            # 保存视频质量
            write_config_value('原画|超清|高清|标清|流畅', self.qualityCombo.currentText())
            # 保存格式
            write_config_value('视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', self.formatCombo.currentText())
            # 监测间隔
            write_config_value('循环时间(秒)', str(self.delaySpin.value()))
            # 并发线程数
            write_config_value('同一时间访问网络的线程数', str(self.threadSpin.value()))
            # 按主播名建文件夹
            write_config_value('保存文件夹是否以作者区分', '是' if self.authorCheck.isChecked() else '否')
            # 分段录制
            write_config_value('分段录制是否开启', '是' if self.splitCheck.isChecked() else '否')
            # 分段时间
            write_config_value('视频分段时间(秒)', str(self.splitTime.value()))
            # 自动转MP4
            write_config_value('录制完成后自动转为mp4格式', '是' if self.mp4Check.isChecked() else '否')
            self.append_log('配置已保存')
            QMessageBox.information(self, '提示', '配置已保存！')
        except Exception as e:
            self.append_log(f'保存配置失败: {e}')

    def load_urls(self):
        try:
            self.urlList.clear()
            urls = read_urls()
            for url in urls:
                self.urlList.addItem(QListWidgetItem(url))
            self.append_log(f'已加载 {len(urls)} 个直播地址')
        except Exception as e:
            self.append_log(f'读取地址失败: {e}')

    def add_url(self):
        url = self.urlInput.text().strip()
        if not url:
            QMessageBox.warning(self, '提示', '请输入直播地址！')
            return
        try:
            urls = read_urls()
            urls.append(url)
            write_urls(urls)
            self.urlList.addItem(QListWidgetItem(url))
            self.urlInput.clear()
            self.append_log(f'添加地址: {url}')
        except Exception as e:
            self.append_log(f'添加地址失败: {e}')

    def del_url(self):
        current = self.urlList.currentRow()
        if current < 0:
            QMessageBox.warning(self, '提示', '请先选中要删除的地址！')
            return
        self.urlList.takeItem(current)
        try:
            urls = []
            for i in range(self.urlList.count()):
                urls.append(self.urlList.item(i).text())
            write_urls(urls)
            self.append_log('删除地址成功')
        except Exception as e:
            self.append_log(f'删除地址失败: {e}')

    def start_recording(self):
        if self.urlList.count() == 0:
            QMessageBox.warning(self, '提示', '请先添加直播地址！')
            return
        self.save_config()
        self.recorder.start_recording()
        self.startBtn.setEnabled(False)
        self.stopBtn.setEnabled(True)

    def stop_recording(self):
        self.recorder.stop_recording()
        self.startBtn.setEnabled(True)
        self.stopBtn.setEnabled(False)

    def append_log(self, text):
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
        self.logText.append(f'[{timestamp}] {text}')
        scrollbar = self.logText.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_status(self, status):
        if status == '录制中':
            self.statusLabel.setText('▶ 状态：录制中')
            self.statusLabel.setStyleSheet('font-size: 18px; font-weight: bold; color: #2ecc71;')
        else:
            self.statusLabel.setText('⏹ 状态：未录制')
            self.statusLabel.setStyleSheet('font-size: 18px; font-weight: bold; color: white;')

    def closeEvent(self, event):
        self.recorder.stop_recording()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setFont(QFont('Microsoft YaHei', 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
