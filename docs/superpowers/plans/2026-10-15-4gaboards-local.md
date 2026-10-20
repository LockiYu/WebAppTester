# 隔离的 4gaBoards 集成计划

> **针对代理型工作节点：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 来按任务实施此计划。步骤使用勾选框 (`- [ ]`) 语法进行跟踪。

**目标：** 在不向模型暴露登录凭据且不更改用户现有实例的情况下，在本地隔离的 4gaBoards 3.2.16 实例上运行 WebAppTester。

**架构：** 一个已提交的 Compose 模板会在回环端口 3001 启动第二个带有独立 Docker 卷的 4gaBoards/PostgreSQL 对。本地设置命令会在被忽略的 `runs/4gaboards-local/` 下生成首次运行管理员和数据库密钥。Playwright 引导命令会登录并写入本地存储状态文件；单次运行的 CLI 和批量 CLI 会将该状态加载到每个新的浏览器上下文中。官方提供的场景文本保持为外部输入，绝不会被硬编码到角色逻辑中。

**技术栈：** Docker Compose/Colima, 4gaBoards 3.2.16, PostgreSQL 16 Alpine, Python 3.11+, Playwright, pytest。

## 全局约束

- 保留现有的 `127.0.0.1:3000` 4gaBoards 实例及其数据库。使用单独的 Compose 项目名称、卷名称和 `127.0.0.1:3001` 端口绑定。
- 在本地生成随机首次运行凭据。以模式 `0600` 存储它们，置于被忽略的 `runs/4gaboards-local/` 下；绝不要提交或打印秘密值、Cookie 或存储状态。
- 不要将凭据、Cookie 或存储状态发送给 Ollama 或 DeepSeek 提示、报告、事件或仪表板导出。
- 场景期间的所有应用程序交互仍通过 `BrowserGateway`；在场景之前的单独浏览器上下文中进行设置身份验证。
- 每个场景保持最大 35 次操作，包括初始导航。不要将模型故障误标为应用程序故障。
- 本地不可用确切的五个英文课程示例。使用唯一的明确 `Course Test` 示例进行集成，并将任何新场景识别为本地编写的冒烟测试；当用户提供时添加官方示例。

---

### 任务 1：可复现的隔离 4gaBoards 实例

**文件：** 创建 `examples/4gaboards/compose.yaml`, `scripts/setup_4gaboards_local.py`, `tests/test_4gaboards_setup.py`；修改 `README.md`。

**接口：** `.venv/bin/python scripts/setup_4gaboards_local.py` 在不存在时创建 `runs/4gaboards-local/instance.env` (0600) 并仅打印下一个 Docker 命令。Compose 模板从该环境文件中读取 `POSTGRES_PASSWORD`, `SECRET_KEY`, `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_EMAIL`, `DEFAULT_ADMIN_NAME` 和 `DEFAULT_ADMIN_PASSWORD`。4gaBoards 镜像为 `ghcr.io/rargames/4gaboards:3.2.16`；Postgres 为 `public.ecr.aws/docker/library/postgres:16-alpine`。
- [ ] **步骤 1：编写测试。** 断言 setup 创建 mode-0600 的环境文件，在重复调用时保留该文件，包含所需的变量名称且值为非空随机数，且不打印任何值。断言 Compose 模板仅绑定 `127.0.0.1:3001:1337` 并使用不同的命名卷。
- [ ] **步骤 2：验证红色。** 运行 `.venv/bin/python -m pytest tests/test_4gaboards_setup.py -q`；预期结果为缺少 setup 脚本/模板。
- [ ] **步骤 3：实现。** 使用 `secrets.token_urlsafe` 生成数据库、会话和管理员密钥。使用独占文件创建和 `os.chmod(path, 0o600)`。Compose 声明 `db` 和 `app`、健康检查 `BASE_URL=http://127.0.0.1:3001` 以及命名卷；不要包含明文密码。不要读取或修改 `/Users/loki/4gaBoards-test`。
- [ ] **步骤 4：验证绿色并启动。** 运行聚焦/完整测试；运行 setup 和 `docker compose --env-file runs/4gaboards-local/instance.env -p webapptester-4ga -f examples/4gaboards/compose.yaml up -d`；验证两个容器均健康且 `/login` 在端口 3001 上响应。不要打印环境变量内容。
- [ ] **步骤 5：提交。** 仅使用 `git commit -m "feat: provision isolated 4gaBoards test instance"` 提交命名的源/测试/文档文件。

---

### 任务 2：私有浏览器认证状态

**文件：** 创建 `scripts/bootstrap_4gaboards_auth.py`、`tests/test_4gaboards_auth.py`；修改 `src/webapptester/cli.py`、`src/webapptester/batch.py`、`tests/test_e2e.py`、`README.md`。

**接口：** 单次运行 CLI 添加 `--storage-state PATH`（可选）。仅在确认现有常规文件后，`run()` 才将 `storage_state=str(path)` 传递给 `browser.new_context()`；缺失或无效的状态会产生 UNKNOWN 报告。批量 JSON 项目可包含可选的 `storage_state` 并将其透传给单次运行 CLI。引导命令在本地读取 `runs/4gaboards-local/instance.env`，使用 Playwright 选择器登录 `http://127.0.0.1:3001/login`，并在确认已认证的仪表板后，以 mode 0600 保存 `runs/4gaboards-local/auth.json`。不要输出状态内容。

- [ ] **步骤 1：编写测试。** 模拟 `browser.new_context` 以断言存储状态路径被传递，且缺失/无效的路径在没有调用提供者的情况下变为 UNKNOWN。断言报告中或事件里不出现任何存储状态数据或路径内容。验证批量加载器接受可选路径并转发它。在不向跟踪文件写入真实凭据的情况下测试引导解析/权限行为。
- [ ] **步骤 2：验证红色。** 运行覆盖 CLI、批量和认证测试；预期结果为不支持 `--storage-state` 且缺少引导。
- [ ] **步骤 3：实现。** 在 Playwright 启动前验证存储状态文件。在引导中，使用将值视为本地密钥的解析器读取环境变量，使用 Playwright `get_by_role`/定位器填写登录表单，等待仪表板而不是仅假设重定向足以证明认证，使用 `context.storage_state(path=...)` 保存状态并 chmod 0600。保持登录上下文与场景运行分离。
- [ ] **步骤 4：验证绿色。** 运行聚焦/完整测试。针对隔离实例执行引导；仅验证 `auth.json` 存在、mode 0600，且使用它打开仪表板时不会看到 `/login`。
- [ ] **步骤 5：提交。** 仅使用 `git commit -m "feat: load private browser auth state"` 提交命名的文件。

---

### 任务 3：4gaBoards 集成冒烟测试和证据

**文件：** 创建 `examples/4gaboards/scenarios.json`、`tests/test_4gaboards_smoke.py`；修改 `README.md`。

**接口：** 场景 JSON 包含课程给出的原文示例：“In the dashboard click Add project, enter the project name Course Test and confirm, then check the new project appears on both dashboard and sidebar.”，以及明确标记的本地只读冒烟场景。每个场景指向 `http://127.0.0.1:3001/`，并使用被 Git 忽略的 `runs/4gaboards-local/auth.json` 登录状态。
- [ ] **步骤 1：编写浏览器冒烟测试。** 在隔离的服务和认证状态下，通过 `BrowserGateway` 打开已认证的仪表板，断言“添加项目”控件可用，并截取真实截图/追踪。当 Docker 或认证状态不可用时，保持此测试为可选参与（opt-in），以确保标准单元测试套件具有确定性。
- [ ] **步骤 2：验证红色。** 运行可选参与的冒烟命令；在任务 2 完成之前，预期缺少场景/认证管道。
- [ ] **步骤 3：运行脚本化和模型场景。** 使用唯一的本地项目名称执行一次确定性的 Playwright 创建/验证流程，并进行两次独立的仪表板/侧边栏断言；将证据保存至 `runs/4gaboards-local/`。运行一次显式的 `Course Test` 场景，并保留其真实的 PASS/FAIL/UNKNOWN 报告（使用 `qwen2.5:3b`）。不要预先植入目标答案或更改裁决规则。避免重复创建 `Course Test`；对于后续的冒烟运行，请使用新实例或唯一的本地名称。
- [ ] **步骤 4：验证工件。** 确认报告步骤计数 ≤35，截图和追踪文件存在，且证据引用映射到保存的观察结果。如果小型模型失败，请在报告/README 中区分模型执行失败与 4gaBoards 失败。运行完整的测试套件。
- [ ] **步骤 5：提交。** 仅提交场景清单、可选参与的冒烟测试和 README 的更改；不要提交生成的报告、凭据、Docker 卷或课程文件。

## 自我审查

- 设计的 4gaBoards 集成要求对应任务 1–3。确切的五个官方示例仍为外部输入；可用的单个示例在实现时不声称其他示例存在。
- 登录是本地测试环境；模型从未接收密码。浏览器上下文创建仅接收存储状态文件路径。
- 现有实例及其数据保持原样，因为 Compose 使用独立的项目和端口。
