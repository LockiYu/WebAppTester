# Apple Silicon 上的本地 Discourse 测试环境

隔离测试环境使用 Discourse `v2025.12.0-latest`。其源代码、数据库、凭据、浏览器状态、截图和 Trace 均位于被 Git 忽略的 `runs/discourse-local/` 目录。请勿提交该目录。

官方 Docker 开发镜像面向 x86 架构，因此此环境使用原生 macOS 服务。可参考上游的 [macOS 配置指南](https://github.com/discourse/discourse/blob/main/docs/developer-guides/docs/02-development-environments/04-macos-setup.md) 和 [Docker 配置指南](https://github.com/discourse/discourse/blob/main/docs/developer-guides/docs/02-development-environments/03-docker-setup.md)。

## 已安装的服务

- Homebrew Ruby 3.3 和 Bundler 2.6.4；依赖包安装在私有目录 `runs/discourse-local/bundle`。
- PostgreSQL 16 运行在回环端口 5433 上，拥有专用的 `webapptester_discourse` 数据库以及为 PostgreSQL 16 构建的 `pgvector` 0.8.7。
- Redis 运行在回环端口 6381 上。
- Rails 应用程序位于 `http://127.0.0.1:3103/`，Ember 前端位于 `http://127.0.0.1:4203/`。

使用以下环境变量从 `runs/discourse-local/app` 运行后端命令：

```sh
export PATH="$PWD/../tools/node_modules/.bin:/opt/homebrew/opt/ruby@3.3/bin:/opt/homebrew/opt/postgresql@16/bin:$PATH"
export BUNDLE_PATH="$PWD/../bundle"
export PGHOST=127.0.0.1 PGPORT=5433
export DISCOURSE_DEV_DB=webapptester_discourse
export DISCOURSE_REDIS_PORT=6381 DISCOURSE_MESSAGE_BUS_REDIS_PORT=6381
export DISCOURSE_HOSTNAME=127.0.0.1 RAILS_ENV=development
```

后端以 `bundle exec rails server -b 127.0.0.1 -p 3103` 启动。
前端在单独的终端中启动，使用 `UNICORN_PORT=3103 bin/ember-cli --host 127.0.0.1 --port 4203` 和相同的 `PATH`。在启动任一应用程序进程之前，请先启动 PostgreSQL 和 Redis。

隔离的管理员已在本地创建。其凭据位于私有的 `runs/discourse-local/admin.env` 文件中。需要时刷新浏览器登录状态：

```sh
.venv/bin/python scripts/bootstrap_discourse_auth.py
```

该命令写入权限为 `0600` 的 `runs/discourse-local/auth.json`。运行需要登录的场景时，通过 `--storage-state` 加载。公开主题的验收报告位于 `runs/discourse-local/topic-heading-acceptance/20261019T085002912699Z/report.json`：
本地 Ollama `qwen3.5:9b` 打开了 `/t/welcome-to-discourse/5`，在 `heading[0]` 中找到了可见的页面标题，并在两次浏览器操作中返回了 PASS。截图和 Trace 位于报告旁边。

同一公开场景的 B0–B3 冒烟对照结果位于 `runs/discourse-local/ablation-smoke/`。它仅验证实验工具可以运行，不属于课程正式评测；工作区尚未收到正式场景集。
