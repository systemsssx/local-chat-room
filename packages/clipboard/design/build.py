"""把 _body*.html 与 skins/*.css 合成自包含的设计稿 HTML。

用法：python build.py
"""
import pathlib

HERE = pathlib.Path(__file__).parent

# 产物名 -> (骨架文件, 皮肤文件)
PAGES = {
    "v1-patch-panel":   ("_body.html",      "patch.css"),
    "v2-console":       ("_body.html",      "console.css"),
    "v3-teletype":      ("_body.html",      "teletype.css"),
    "v4-console-chat":  ("_body_chat.html", "console-chat.css"),
}

for out_name, (body_file, skin_file) in PAGES.items():
    body_path = HERE / body_file
    skin_path = HERE / "skins" / skin_file
    if not body_path.exists() or not skin_path.exists():
        print("skip:", out_name, "(缺文件)")
        continue
    html = body_path.read_text(encoding="utf-8").replace(
        "/*SKIN*/", skin_path.read_text(encoding="utf-8")
    )
    (HERE / f"{out_name}.html").write_text(html, encoding="utf-8")
    print("built ->", out_name + ".html")
