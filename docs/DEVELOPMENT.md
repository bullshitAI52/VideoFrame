# 开发说明

## 架构

后端使用 Python 标准库 `ThreadingHTTPServer`，依赖 Pillow 拼图、requests 访问模型接口，以及外部 ffmpeg / ffprobe。前端使用原生 HTML、CSS 和 JavaScript，每 3 秒查询任务状态。

`server.py` 通过后台线程运行抽帧或分析任务，同一实例不同时运行这两类任务。AI 使用最多 4 个工作线程，JSON 文件保存进度。

## 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `VIDEOFRAME_PORT` | `8765` | 本地监听端口 |
| `VIDEOFRAME_DATA` | `~/Library/Application Support/VideoFrame` | 运行数据目录 |

使用自定义端口时请手动启动服务并访问对应地址；启动脚本的自动打开地址固定为 8765。

## 最小检查

```bash
python3 -m py_compile server.py
node --check static/app.js
python3 -m unittest discover -s tests -v
node tests/test_frontend.cjs
```

Node.js 仅用于 JavaScript 语法检查，运行程序不需要 Node.js。

首次实现时已使用合成视频验证扫描、FFmpeg 抽帧、16+2 帧宫格、图片读取、收藏、目录切换恢复及跨域请求拒绝。使用本地模拟接口验证图像请求载荷、并发分析及 JSON 解析。回归测试已整理到 `tests/`，使用独立临时数据目录，不读取真实视频和密钥。覆盖短片兜底、常规宫格、目录恢复、任务互斥、异常提示、非法模型返回值，以及前端延迟刷新和配置失败。

后续修改应在独立的 `VIDEOFRAME_DATA` 目录中测试，避免影响日常使用记录。测试素材应使用合成图像或获授权的内容。

## 配置和运行数据

不要提交 API Key、实际视频、生成的监控宫格、目录索引或本地日志。`.gitignore` 包含常用运行数据和视频文件排除项；提交前仍应检查暂存区。

当前不使用 Keychain，密钥配置文件未加密。服务面向单机使用，公网部署需要另行设计鉴权及隔离。

## 后续维护

待办见 [ROADMAP.md](../ROADMAP.md)。实现功能后同步更新功能说明、使用说明和更新记录，明确新增能力、行为变化和验证范围。
