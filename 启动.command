#!/bin/zsh
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v python3 >/dev/null; then
  echo '请先安装 Python 3.10 或更新版本。'; read; exit 1
fi
if [ ! -d .venv ]; then python3 -m venv .venv || exit 1; fi
.venv/bin/python -c 'import PIL, requests' 2>/dev/null || .venv/bin/python -m pip install -r requirements.txt || exit 1
if ! command -v ffmpeg >/dev/null; then
  echo '请先通过 Homebrew 安装 FFmpeg：brew install ffmpeg'; read; exit 1
fi
(sleep 1; open http://127.0.0.1:8765) &
.venv/bin/python server.py
read
