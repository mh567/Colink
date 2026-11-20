# Colink 临时依赖补丁

两个 wheel 基于 PyPI 的 `octop-harness 1.0.7` 和 `octop-memory 1.0.3`，包含 PR #55 本地验证过的记忆隔离与彻底遗忘补丁。代码差异见同目录的 `.patch`，原发行包的 MIT 许可保留在 wheel 内。按 [T01 的发行规则](../docs/T01-源码许可与麒麟早期核验.md)，Harness wheel 构建时排除英文和中文 PowerPoint 技能材料。

源码 checkout 直接运行 `uv sync --locked`；`uv.lock` 使用仓库内的 wheel。纯 pip 安装本地源码或 Colink wheel 时，需要提供这个目录，例如 `pip install --find-links vendor .`。这两个 `+colink.1` 版本没有发布到 PyPI。含此补丁的 Colink 仅供源码和捆绑安装，不能作为仅依赖 PyPI 的公开发行版。

用 Git、uv 和 Python 3.12 从固定的官方源码包重建：

```sh
uv run --no-project --python 3.12 python vendor/rebuild.py
uv lock
```

脚本先校验原发行包的 SHA-256，再应用补丁并用固定版本的 Hatchling 构建。修改补丁时同步递增本地版本，更新 `pyproject.toml` 中的版本及 wheel 路径，然后更新 `uv.lock`。上游发行版包含并通过这些行为测试后，切回上游依赖并移除本目录及容器构建中的 wheel 拷贝。
