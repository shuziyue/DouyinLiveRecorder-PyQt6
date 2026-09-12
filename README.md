# 🎬 抖音直播录制工具 - PyQt6 桌面版

> **本项目基于 [ihmily/DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) 二次开发**
>
> 原项目是一个功能强大的多平台直播录制工具，本项目在其基础上进行了以下改进：
> - ✅ 使用 PyQt6 打造原生桌面 GUI 界面（紫色渐变风格）
> - ✅ 彻底解决窗口闪烁问题（修复 `os.system('cls')` 频繁启动 cmd.exe）
> - ✅ 无黑色控制台窗口（console=False 打包）
> - ✅ 持久化 Node.js 签名进程，提升性能
> - ✅ 双 exe 架构，停止录制后再次启动无模块缓存问题
> - ✅ 监测间隔默认 1 分钟，主播再开播响应更快
> - ✅ 增加更多可配置项（并发线程数、按主播名建文件夹、自动转MP4等）
> - ✅ 日志过滤，只显示关键信息，不被监控刷屏

---

## 📦 功能特性

- 🎥 **自动监测直播**：主播开播自动录制，下播自动停止，再开播自动续录
- 👥 **多主播同时录制**：支持添加多个主播地址，同时录制
- 🎨 **多种视频质量**：原画 / 超清 / 高清 / 标清 / 流畅
- 💾 **多种保存格式**：TS / MKV / FLV / MP4 / MP3 / M4A
- 🔄 **自动重连**：网络抖动时 ffmpeg 自动重连，不会断录
- 📁 **灵活的文件组织**：按主播名 / 日期 / 标题建文件夹
- ✂️ **分段录制**：支持按指定时长自动分割视频
- 🔄 **自动转MP4**：录制完成后自动转换为 MP4 格式
- 📝 **日志记录**：运行日志保存到文件，便于排查问题
- 🖥️ **桌面 GUI**：PyQt6 原生界面，无需浏览器，无黑框，不闪烁

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.10+
- Windows 10/11
- [ffmpeg](https://ffmpeg.org/download.html)（需自行下载）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 下载 ffmpeg

由于 ffmpeg 文件较大（约 156MB），本仓库不包含 ffmpeg。请自行下载：

1. 访问 [ffmpeg 官方下载页](https://ffmpeg.org/download.html#build-windows)
2. 下载 Windows 版本的 ffmpeg
3. 解压后，将 `ffmpeg.exe` 放到项目的 `ffmpeg/` 目录下

目录结构应如下：
```
DouyinLiveRecorder-PyQt6/
├── ffmpeg/
│   └── ffmpeg.exe      ← 放到这里
├── config/
│   ├── config.ini
│   └── URL_config.ini
├── src/
├── main_pyqt6.py
├── recorder.py
├── douyin_only.py
└── ...
```

### 4. 运行程序

```bash
python main_pyqt6.py
```

### 5. 使用方法

1. 在左侧"直播地址管理"中输入抖音直播间地址，点击"添加"
2. 在右侧"录制配置"中设置视频质量、保存格式、监测间隔等参数
3. 点击"💾 保存配置"保存设置
4. 点击"▶ 开始录制"开始录制
5. 点击"⏹ 停止录制"停止录制

> 支持的地址格式：
> - `https://live.douyin.com/xxxxxx`（推荐）
> - `https://v.douyin.com/xxxxxx/`（分享链接）

---

## 📦 打包成 exe

### 使用 PyInstaller 打包

```bash
pip install pyinstaller
pyinstaller pyqt6.spec --clean --noconfirm
```

打包完成后，产物在 `dist/抖音直播录制工具/` 目录下。

### 打包后需要手动复制的文件

由于 PyInstaller 不会自动包含所有文件，打包后需要手动复制：

```bash
# 复制配置文件
xcopy /E /I config dist\抖音直播录制工具\config

# 复制 ffmpeg
xcopy /E /I ffmpeg dist\抖音直播录制工具\ffmpeg
```

### 打包产物结构

```
抖音直播录制工具/
├── 抖音直播录制工具.exe    # 主程序（GUI）
├── recorder.exe            # 录制程序（后台运行）
├── config/                 # 配置文件
├── ffmpeg/                 # ffmpeg 工具
├── _internal/              # Python 运行时和依赖
└── 使用说明.md             # 使用说明
```

---

## ⚙️ 配置说明

配置文件位于 `config/config.ini`，主要配置项：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `原画\|超清\|高清\|标清\|流畅` | 视频质量 | 原画 |
| `视频保存格式ts\|mkv\|flv\|mp4\|mp3音频\|m4a音频` | 保存格式 | ts |
| `循环时间(秒)` | 直播状态监测间隔 | 60（1分钟） |
| `同一时间访问网络的线程数` | 并发线程数 | 3 |
| `保存文件夹是否以作者区分` | 按主播名建文件夹 | 是 |
| `分段录制是否开启` | 开启分段录制 | 否 |
| `视频分段时间(秒)` | 分段时长 | 1800（30分钟） |
| `录制完成后自动转为mp4格式` | 自动转MP4 | 否 |

> 也可以在 GUI 界面中直接修改配置，点击"保存配置"即可。

---

## 🐛 已知问题

1. **火绒报毒**：PyInstaller onefile 模式可能被火绒报 TrojanDropper，建议使用 onedir 模式（本项目默认 onedir）
2. **抖音 Cookie**：部分直播间需要登录 Cookie 才能获取高清流，可在 `config/config.ini` 的 `[Cookie]` 部分配置
3. **ffmpeg 路径**：确保 `ffmpeg.exe` 在 `ffmpeg/` 目录下，或已添加到系统 PATH

---

## 🤝 致谢

- **原项目**：[ihmily/DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) - 感谢原作者提供这么优秀的直播录制工具
- **ffmpeg**：[ffmpeg.org](https://ffmpeg.org/) - 强大的视频处理工具
- **PyQt6**：[Riverbank Computing](https://www.riverbankcomputing.com/software/pyqt/) - Python GUI 框架

---

## 📄 许可证

本项目遵循原项目的许可证。原项目使用 MIT 许可证，详见 [原项目 LICENSE](https://github.com/ihmily/DouyinLiveRecorder/blob/master/LICENSE)。

---

## ⭐ Star History

如果这个项目对你有帮助，欢迎给个 Star ⭐

同时也请支持原项目：[ihmily/DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder)
