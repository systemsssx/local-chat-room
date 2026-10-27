# 贡献指南 · Contributing to dsh-tools

欢迎任何规模贡献：错别字、文档、Bug 修复、新功能。

---

## 🐛 报 Bug / 提需求

- 先搜一遍 [Issues](https://github.com/systemsssx/dsh-tools/issues) 和 [Discussions](https://github.com/systemsssx/dsh-tools/discussions)；
- Bug 用 [Bug 模板](https://github.com/systemsssx/dsh-tools/issues/new?template=bug_report.md)：Python 版本、OS、子包名（chatroom / clipboard）、复现步骤、报错日志；
- 需求用 [Feature 模板](https://github.com/systemsssx/dsh-tools/issues/new?template=feature_request.md)：讲使用场景和现在的不便；
- 用法讨论请去 Discussions，不走 Issue。

> 别贴 token、真实绝对路径、用户名，用 `<本机路径>` 占位。

---

## 🛠️ 提 PR

```bash
git checkout -b feat/your-idea
```

改动前先对齐方向；改完必须跑对应子包的测试：

```bash
cd packages/chatroom   && python -m unittest discover    # 131 tests
cd packages/clipboard  && python -m unittest discover    # 103 tests
```

PR 里请写清：**改了什么、为什么、哪个子包、测试输出**；UI 改动附截图。

### 代码风格

- **Python 3.10+**，只用 FastAPI + uvicorn + 标准库，不引重量级依赖；
- 前端保持**零构建**：原生 ES Module，不引打包器 / 框架；
- 中文注释解释"为什么"，错误提示必须能指导用户下一步；
- 不要把运行时数据写进仓库：`data/`、`*.db`、`.venv/`、`__pycache__/` 已在 `.gitignore`；
- 提交信息用 Conventional Commits（`feat:` / `fix:` / `docs:` / `test:` / `refactor:`）。

---

## 📄 License

提交即表示你同意以 [MIT License](./LICENSE) 授权你的贡献。
