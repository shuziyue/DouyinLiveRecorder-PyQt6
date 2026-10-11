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
> - ✅ 新增「保存路径」可配置，并让内核真正读取生效
> - ✅ 默认保存路径直接写入界面与配置（不再是"留空表示默认"），改过之后才做可用性判断
> - ✅ 保存路径支持盘根（填 `F:\` 自动在其下建 `抖音直播\主播名\`），目录创建带真实写入校验与权限回退
> - ✅ 系统托盘常驻：最小化到托盘、托盘菜单、录制中图标红点、状态气泡
> - ✅ 录制状态看板：实时展示每个主播的录制时长 / 分段数 / 产出大小
> - ✅ 自定义程序图标，打包时自动写入 exe
> - ✅ 停止录制只结束录制进程树，不再误杀全系统 ffmpeg
> - ✅ **录前探测直播源**：开录前探明真实地址与容器/编码/分辨率并写入日志，出问题 5 分钟定位
> - ✅ **录制中体检**：分段写入满 60 秒后检查有没有画面，体检通过也留一行证据
> - ✅ **坏流自动换线路**：判定这次取到的是坏流后 5 秒重连，并临时改用另一条线路（FLV ↔ HLS）
> - ✅ **坏段隔离**：没有画面的分段改名 `.broken.ts`，不转码、不参与分段编号，保留作证据
> - ✅ **看板显示流状态**：看板新增「流状态」列（✅正常 / ⚠️可疑 / ❌坏流 / 🔁已换线路 / —未检测），
>   坏流整行标红、悬停看原因与坏流次数，不必等录完试播才知道流坏了
> - ✅ ffmpeg 升级到 N-127142（2026-10-03 构建），并修正 `-thread_queue_size` 在新版中被打成「输出侧选项」导致的启动失败

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
- 🗂️ **自定义保存路径**：默认已填好程序目录下的 downloads（不再是留空表示默认），想换到任意磁盘目录（含盘根，如 `F:\`）直接改即可，程序自动在其下建 `抖音直播\主播名\`；改过之后才做可用性判断，目录不可写时自动回退不中断
- 🔔 **系统托盘**：最小化到托盘后台录制，托盘图标红点提示录制中
- 📊 **录制状态看板**：实时显示主播状态、已录时长、分段数、产出大小，以及**这次取到的直播流好不好**
  （✅正常 / ⚠️可疑 / ❌坏流 / 🔁已换线路 / —未检测，坏流整行标红）
- 🛡️ **坏流兜底**：录前探测真实地址与容器/编码并写进日志；录制中体检分段有没有画面；
  判定为坏流时 5 秒重连并临时换线路（FLV ↔ HLS）；没有画面的分段隔离成 `.broken.ts`，
  不再送进转码 —— 专治"文件在涨、程序不报错、打开却提示编码格式不支持"

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.10+
- Windows 10/11
- ffmpeg（**已内置**，无需自行下载；见下一节）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 关于 ffmpeg

本项目**已内置 ffmpeg**，开箱即用。内置版本为
**N-127142-g12b7b9891b（2026-10-03 构建 / libavformat 63 · libavcodec 63，GPL shared 构建）**。

```
DouyinLiveRecorder-PyQt6-Pro/
├── ffmpeg/             ← 内置：ffmpeg.exe / ffprobe.exe + 所需 DLL，整目录随包分发
├── assets/
│   └── icon.ico        ← 程序图标（打包时写入 exe）
├── config/
│   ├── config.ini
│   └── URL_config.ini
├── src/
├── tools/
│   └── make_icon.py    ← 重新生成图标用
├── main_pyqt6.py
├── recorder.py
├── douyin_only.py
└── ...
```

如需自行更换 ffmpeg：从 [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases) 下载
`ffmpeg-master-latest-win64-gpl-**shared**.zip`，把压缩包 `bin/` 下的**全部文件**（含所有 `.dll`）
复制进项目的 `ffmpeg/` 覆盖旧文件。

> ⚠️ 必须是 **shared** 版。只放一个 `ffmpeg.exe` 会因缺少 DLL 无法启动。
> 另外 `-thread_queue_size` 等参数的归属在不同 ffmpeg 版本间有过调整，换版本后建议先试录一次确认。

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

> `assets/`（含程序图标）已写入 `pyqt6.spec` 的 `datas`，无需手动复制。

### 程序图标

- 图标文件为 `assets/icon.ico`（含 16/32/48/64/128/256 六档尺寸），已在 `pyqt6.spec` 中通过 `icon='assets/icon.ico'` 指定，打包时自动写入 exe，同时用于窗口与系统托盘。
- 想换图标：修改 `tools/make_icon.py` 里的配色或图形，然后重新生成：

```bash
python tools/make_icon.py
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
| `同一时间访问网络的线程数` | 同时访问网络的线程数（只影响获取直播地址的并发，不影响能同时录几个主播） | 3 |
| `直播保存路径(不填则默认)` | 录播保存目录。**默认已自动填好程序目录下的 downloads**，想改直接改；支持绝对/相对路径与盘根（如 `F:\`），程序会在其下建 `抖音直播\主播名\` | 程序目录下的 downloads |
| `保存文件夹是否以作者区分` | 按主播名建文件夹 | 是 |
| `分段录制是否开启` | 开启分段录制 | 否 |
| `视频分段时间(秒)` | 分段时长：每段视频的长度只由这个值决定，与线程数无关 | 1800（30分钟） |
| `录制完成后自动转为mp4格式` | 自动转MP4 | 否 |
| `录前探测直播源(是/否)` | 开录前用 ffprobe 探一次真实地址，把容器/编码/分辨率/码率与完整地址写进日志 | 是 |
| `直播流健康体检(是/否)` | 录制中检查分段里有没有有效画面（时长是否为 0） | 是 |
| `坏流体检判定秒数` | 分段写入满多少秒后开始体检 | 60 |
| `坏流自动切换线路(是/否)` | 判定坏流后临时改用另一条线路（FLV ↔ HLS）重试 | 是 |

> 老配置缺少这四个新项时，程序会在首次运行时自动补上默认值。
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
