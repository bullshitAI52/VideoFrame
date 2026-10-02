# VideoFrame · 智能查监控

面向 macOS 的 AI 监控视频检索工具。用自然语言描述要找的目标，从大量本地监控录像中筛选相关画面，并查看对应视频和视频内时间，减少逐段手动翻看监控的工作。

例如：“查找携带黑色提包经过走廊的人”“查找有人搬走门口纸箱的画面”。当前采用采样画面分析，能提供候选线索；涉及连续动作的事件仍需回看原视频确认。

抽帧、静态画面过滤和 16 宫格是降低分析量的处理手段。产品重点是：描述目标 → AI 查找 → 查看命中画面与时间 → 回看监控核实。

当前为初版：本地抽帧链路已验证，真实 AI 服务兼容性和识别准确率尚待验证。界面参照提供的截图实现，不是原软件源码。

## 核心功能：智能查监控

- 按人物可见外观、携带物品或画面中的事件描述检索目标。
- 批量筛选监控，返回候选画面、命中格子、说明和视频内时间。
- 收藏值得复核的线索，在 Finder 定位原视频进行人工回看。

## 支撑功能

- 递归扫描视频目录，支持 MP4、MOV、MKV、AVI、TS、M4V、WebM、MTS。
- FFmpeg 定时抽帧，可选过滤近似静态画面。
- 生成带编号和时间戳的 4 × 4 宫格，支持大图查看与收藏。
- 配置兼容 Chat Completions 的视觉模型服务，按意图批量分析。
- 最多 4 路 AI 并发，展示命中、置信度、原因及命中格子。
- 暂停、继续、失败项重试，按目录恢复收藏与分析记录。

## 快速开始

需要 macOS、Python 3.10+、FFmpeg（含 ffprobe）。

```bash
git clone https://github.com/bullshitAI52/VideoFrame.git
cd VideoFrame
```

双击 `启动.command`，或在终端运行：

```bash
./启动.command
```

首次启动会建立 `.venv` 并安装 Python 依赖，随后打开 [本地界面](http://127.0.0.1:8765)。保持终端窗口运行。选择视频目录 → 扫描与抽帧 → 查看宫格 → 配置接口 → 输入意图并分析。

手动启动：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

## 文档

- [使用说明与数据存储](docs/USAGE.md)
- [功能说明与当前限制](docs/FEATURES.md)
- [开发与验证说明](docs/DEVELOPMENT.md)
- [后续优化清单](ROADMAP.md)
- [更新记录](CHANGELOG.md)

## 数据处理

视频抽帧和拼图在本机完成，不修改原视频。点击 AI 分析后，宫格会发送给配置的 API 服务并可能产生费用。密钥保存在本机权限受限的配置文件中，目前未加密。程序仅监听 `127.0.0.1`，不适合直接部署为公网服务。

默认运行数据目录为 `~/Library/Application Support/VideoFrame`，不会上传到本仓库。AI 结果用于辅助筛选，需回看原视频核实。

## 项目结构

```text
server.py          本地 HTTP 服务、抽帧、宫格及 AI 任务
static/            原生 HTML / CSS / JavaScript 界面
启动.command        macOS 启动脚本
requirements.txt   Python 依赖
docs/             使用、功能与开发说明
ROADMAP.md         待办优化清单
CHANGELOG.md       更新记录
```

本仓库暂未指定开源许可证。
