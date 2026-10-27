"""静态自检：JS 里引用的元素 id，在两份页面里都得存在。

为什么值得单独写个脚本：前端最常见的低级 bug 就是**拼错 id** ——
页面不报错、按钮没反应，靠肉眼翻 3000 行代码找很费劲，跑一下这个脚本两秒钟就能定位。

跑法（项目根目录）：python dev/ui-check.py
"""

import re
import sys
from pathlib import Path

# 中文 Windows 的控制台默认是 cp936，输出中文/符号会炸 —— 统一成 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # pragma: no cover - 某些环境下 stdout 不支持 reconfigure
    pass

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "public" / "js"
PAGES = ["index.html", "mobile.html"]

ID_IN_JS = re.compile(r"""\$\(\s*['"]([A-Za-z0-9_]+)['"]\s*\)""")
ID_IN_HTML = re.compile(r"""id=["']([A-Za-z0-9_]+)["']""")


def js_ids() -> set[str]:
    found: set[str] = set()
    for path in sorted(JS_DIR.glob("*.js")):
        found |= set(ID_IN_JS.findall(path.read_text(encoding="utf-8")))
    return found


def html_ids(name: str) -> set[str]:
    return set(ID_IN_HTML.findall((ROOT / "public" / name).read_text(encoding="utf-8")))


def main() -> int:
    needed = js_ids()
    problems = 0

    print(f"JS 引用的 id：{len(needed)} 个")
    for page in PAGES:
        have = html_ids(page)
        missing = sorted(needed - have)
        if missing:
            problems += len(missing)
            print(f"  ✗ {page} 缺少：{', '.join(missing)}")
        else:
            print(f"  ✓ {page} 全部都有")

    # 两份页面应当有一模一样的 id 集合（同一套 JS 驱动）
    diff = html_ids(PAGES[0]) ^ html_ids(PAGES[1])
    if diff:
        problems += len(diff)
        print(f"  ✗ 两份页面的 id 不一致：{', '.join(sorted(diff))}")
    else:
        print("  ✓ 两份页面的 id 集合完全一致")

    unused = sorted((html_ids(PAGES[0]) | html_ids(PAGES[1])) - needed)
    if unused:
        print(f"  · 页面里没被 JS 引用的 id（正常，可能只给 CSS 用）：{', '.join(unused)}")

    if problems:
        print(f"\n发现 {problems} 处问题")
        return 1
    print("\n静态检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
