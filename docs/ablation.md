# B0–B3 消融实验工具

正式评测场景由评测方另行提供。仓库有意不包含 36 条场景原文、标准答案，也不宣称已有正式成绩。取得场景后，请在私有位置创建清单，每个场景对应一个对象：

```json
[
  {
    "id": "scenario-01",
    "scenario": "the natural-language scenario supplied by the evaluator",
    "url": "http://127.0.0.1:3000/",
    "storage_state": "auth.json",
    "max_steps": 35
  }
]
```

`storage_state` 可省略；若提供，相对路径以私有清单所在目录为基准。每个需要登录的应用都应单独生成浏览器登录状态。清单和登录状态都应保留在 Git 之外；登录状态包含会话 Cookie。

然后用本地 Ollama 运行对照实验，输出目录也应放在私有位置：

```sh
.venv/bin/webapptester-ablation \
  /private/path/formal-scenarios.json \
  --output /private/path/webapptester-ablation \
  --model qwen2.5:3b
```

默认命令在四种条件下执行同一份清单，使用本地 Ollama，并生成逐场景报告及 `ablation-summary.json`；不会调用付费服务。正式场景发布后，如已配置 `DEEPSEEK_API_KEY`，可通过 `--provider deepseek --model deepseek-flash` 选择课程要求的 DeepSeek Flash 模型。此类运行共用 README 所述的人民币 20 元预算台账；保守预留额不足时会停止请求。付费评测前应重新核对 [DeepSeek 价格页](https://api-docs.deepseek.com/quick_start/pricing/)。台账给出的是保守估算，不是账单；每批付费运行后还需与服务商实际账单核对，不能重置台账以绕过预算。若评测方已有运行器或环境管理器，也可通过 `webapptester.ablation` 中的 `AblationHarness` 和 `AblationConfig` 调用。

## 条件

| 条件 | 证据契约 | 缺口反馈 | 预算仲裁 |
| --- | --- | --- | --- |
| B0 | 关闭 | 关闭 | 关闭 |
| B1 | 开启 | 关闭 | 关闭 |
| B2 | 开启 | 开启 | 关闭 |
| B3 | 开启 | 开启 | 开启 |

B0 保留普通的 Planner、Executor 和 Verifier 角色，并使用 Verifier 的结构化检查点状态，但不包含 B1 的溯源检查。B1 要求引用和检查点声明必须基于捕获的观察及其声明的区域。B2 通过从 Verifier 向 Executor 发送结构化的缺失证据请求。B3 应用现有的硬性操作上限，并在其声明的估计成本超过剩余预算时拒绝差距请求。这是一种可解释的启发式方法，并非声称可以精确预测未来的奖励。

摘要报告包含 PASS、FAIL、UNKNOWN、证据覆盖率、浏览器步骤、每种条件下的令牌计数和耗时。将 UNKNOWN 解释为不确定的执行结果。按场景报告失败情况，并连同实际模型、测试环境和版本一起报告任何课程结果；不要从操作或提供商失败中推断应用程序缺陷。

## 本地跨应用试点

在 2026-10-19，针对每个隔离的应用程序，在每种条件下各运行一次只读场景，使用本地 Ollama `qwen3.5:9b` 和五操作限制。应用程序包括 4gaBoards 3.2.16、Admidio 5.0.2 和 Discourse `v2025.12.0-latest`。4gaBoards 场景检查了仪表板和侧边栏中的现有项目；Admidio 和 Discourse 检查了目标页面标题。私有输入和证据位于 `runs/cross-app-pilot/` 下。

| 条件 | PASS | UNKNOWN | FAIL |
| --- | ---: | ---: | ---: |
| B0 | 3 | 0 | 0 |
| B1 | 2 | 1 | 0 |
| B2 | 2 | 1 | 0 |
| B3 | 3 | 0 | 0 |

B1 的 Discourse 运行和 B2 的 Admidio 运行均已到达目标页面，但 Verifier 未提供完整且有支撑的证据，之后的一次浏览器操作也失败了，因此两次结果保持为 UNKNOWN。结果看板保留了这些部分报告。这只是每个实验单元各运行一次的流程冒烟测试，不是正式 36 场景评测，也不能据此估计方法之间的效果差异。

### 复现本地试跑

启动三个隔离的应用程序并刷新其私有登录状态后，执行以下操作：

```sh
.venv/bin/python scripts/bootstrap_4gaboards_auth.py
.venv/bin/python scripts/bootstrap_admidio_auth.py
.venv/bin/python scripts/bootstrap_discourse_auth.py
.venv/bin/python scripts/run_cross_app_pilot.py
```

最后一条命令会在隔离的 4gaBoards 测试环境中创建一个唯一命名的项目，在仪表板和侧边栏中对其进行验证，并生成一个包含三个场景的私有清单。它使用相同的本地 Ollama 模型（默认为 `qwen3.5:9b`）运行所有四个条件，然后将清单、测试环境元数据、十二份报告和摘要存储到位于 `runs/cross-app-pilot/` 下的带时间戳且被 Git 忽略的目录中。每个条件在该运行中看到相同的项目和场景文本。使用 `--variant B3` 进行更简短的设置检查。该脚本不会联系付费模型提供商。

2026-10-19 完成的完整单命令复现，在十二次运行中产生了 11 个 PASS 和 1 个 UNKNOWN；其摘要位于 `runs/cross-app-pilot/20261019T145631612033Z/results/ablation-summary.json`。
所有十二份报告均已加载到结果仪表板中。两次重复中不同的 UNKNOWN 单元格展示了为何每个单元格仅运行一次仅具有诊断意义。
