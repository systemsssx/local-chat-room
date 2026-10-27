"""造两个示例文件：一张图片、一个 py 文件（给开发桩灌数据用）。"""
import pathlib
from PIL import Image, ImageDraw

OUT = pathlib.Path(__file__).parent / "tmp"
OUT.mkdir(exist_ok=True)

# 一张"截图样"的图：深色底 + 几个面板块
img = Image.new("RGB", (880, 520), (24, 26, 30))
d = ImageDraw.Draw(img)
d.rectangle([0, 0, 880, 44], fill=(33, 35, 40))
for i, x in enumerate((16, 34, 52)):
    d.ellipse([x, 17, x + 9, 26], fill=(70, 73, 80))
d.rectangle([40, 90, 300, 470], fill=(38, 40, 46), outline=(58, 61, 68))
d.rectangle([330, 90, 560, 250], fill=(58, 48, 28), outline=(120, 92, 40))
d.rectangle([330, 280, 560, 470], fill=(38, 40, 46), outline=(58, 61, 68))
d.rectangle([590, 90, 840, 470], fill=(32, 34, 39), outline=(58, 61, 68))
for i in range(7):
    y = 120 + i * 34
    d.rectangle([610, y, 610 + (200 - i * 14), y + 12], fill=(72, 66, 48))
img.save(OUT / "sample-shot.png")
print("written", OUT / "sample-shot.png", img.size)

(OUT / "clipboard.py").write_text(
    'import json\n'
    'from http.server import BaseHTTPRequestHandler\n'
    '\n'
    'ROOMS = {}   # { room: {"text": str, "rev": int, "at": float} }\n'
    '\n'
    'def set_text(room, text):\n'
    '    """写房间内容并让版本号自增 —— 前端靠 rev 判断"变了没有"。"\n'
    '    st = ROOMS.setdefault(room, {"text": "", "rev": 0, "at": 0})\n'
    '    st["text"] = text\n'
    '    st["rev"] += 1\n'
    '    st["at"] = time.time()\n'
    '    return st\n',
    encoding="utf-8",
)
print("written", OUT / "clipboard.py")
