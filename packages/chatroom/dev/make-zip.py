"""dev/make-zip.py —— 把项目打成交付压缩包（规则和上一个项目一致）。

规则（对照 C:\\work\\collab-clipboard.zip 定下来的）：

  · 整包放 **`C:\\work\\<项目名>.zip`**，包里带**一层同名根目录**（解压出来就是一个文件夹）；
  · **进包**：源码、测试、文档、设计稿、工具脚本，以及 `data/app.db`、`uploads/` 这些运行数据
    —— 上一个交付包里就有 demo 数据库和上传的示例文件，这样对方解压即用；
  · **不进包**：`__pycache__` / `*.pyc`（编译缓存）、`*.bak`（换 schema 时留的旧库备份）、
    `*.log` / `*.tmp`、`.git`、`node_modules`、以及包里再套包。
  · **数据库要一致快照**：服务可能正在跑（有 -wal / -shm 侧车文件），
    直接拷文件可能拷到一个半成品。所以 `data/app.db` 用 sqlite3 的备份接口取一份
    **一致的快照**，侧车文件不单独进包 —— 上一个包里也只有一个干净的 app.db。

用法（项目根目录）：
    python dev/make-zip.py            # 打包
    python dev/make-zip.py --check    # 只列出会打进去的文件，不写包
"""

import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent          # 项目根目录
OUT = ROOT.parent / f"{ROOT.name}.zip"                 # C:\work\<项目名>.zip

SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".idea", ".vscode"}
SKIP_SUFFIX = (".pyc", ".pyo", ".bak", ".log", ".tmp", ".zip")
SKIP_NAMES = {"app.db-wal", "app.db-shm"}              # SQLite 侧车文件（用快照代替）


def package_files() -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if name in SKIP_NAMES or name.endswith(("-wal", "-shm")) and name.startswith("app.db"):
                continue
            if path.suffix.lower() in SKIP_SUFFIX:
                continue
            out.append(path)
    return out


def snapshot_db(source: Path, target: Path) -> None:
    """用 sqlite3 备份接口取一致快照（服务在跑也不会拷到半成品）"""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def main() -> int:
    check_only = "--check" in sys.argv
    files = package_files()
    total = sum(p.stat().st_size for p in files)
    print(f"项目      {ROOT}")
    print(f"输出      {OUT}")
    print(f"待打包    {len(files)} 个文件 · {total / 1024 / 1024:.2f} MB（压缩前）")
    db = ROOT / "data" / "app.db"
    print(f"数据库    {'有，将取一致快照' if db.exists() else '还没有（首次启动时自动创建）'}")

    if check_only:
        for p in files:
            print("   ", p.relative_to(ROOT))
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="chatroom-pack-"))
    try:
        with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in files:
                rel = path.relative_to(ROOT)
                if path == db:
                    snap = tmp / "app.db"
                    snapshot_db(path, snap)
                    zf.write(snap, f"{ROOT.name}/{rel.as_posix()}")
                else:
                    zf.write(path, f"{ROOT.name}/{rel.as_posix()}")
    finally:
        for leftover in tmp.glob("*"):
            leftover.unlink()
        tmp.rmdir()

    size = OUT.stat().st_size
    print(f"\n已写出    {OUT}  ({size / 1024 / 1024:.2f} MB)")

    # ── 自检：重新打开包，确认该有的在、不该有的不在、库能读 ──
    with zipfile.ZipFile(OUT) as zf:
        names = zf.namelist()
        bad = [n for n in names
               if "__pycache__" in n or n.endswith((".pyc", ".bak", ".log", ".tmp"))
               or n.endswith(("-wal", "-shm")) or "/node_modules/" in n or n.endswith(".zip")]
        roots = sorted({n.split("/")[0] for n in names})
        print(f"自检      条目 {len(names)} · 根目录 {roots} · 违规条目 {len(bad)}")
        if bad:
            print("   ❌", bad[:6])
            return 1
        for must in ("README.md", "server.py", "docs/接口契约.md", "public/index.html",
                     "tests/test_auth.py", "启动服务.cmd", "data/app.db"):
            mark = "✓" if f"{ROOT.name}/{must}" in names else "✗"
            print(f"   {mark} {must}")
            if mark == "✗":
                return 1
        if f"{ROOT.name}/data/app.db" in names:
            with tempfile.TemporaryDirectory(prefix="chatroom-probe-") as probe_dir:
                probe = Path(probe_dir) / "probe.db"
                probe.write_bytes(zf.read(f"{ROOT.name}/data/app.db"))
                conn = sqlite3.connect(f"file:{probe}?mode=ro", uri=True)
                try:
                    tables = sorted(r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"))
                    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                              for t in ("users", "rooms", "messages") if t in tables}
                finally:
                    conn.close()
            print(f"   库自检    表 {len(tables)} 张 · {counts}")
    print("\n打包完成 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
