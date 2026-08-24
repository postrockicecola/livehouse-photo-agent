# 13｜测试、CI 与质量门禁

## 学习目标

- 掌握 pytest、fixture、mock、marker 和参数化；
- 区分单元、集成、契约、端到端、评估与 chaos 测试；
- 读懂 Python/Web/Go CI；
- 能为高风险修改选择合适测试；
- 能识别当前测试缺口。

## 1. 测试金字塔

### 单元测试

纯函数、parser、route、score、state transition。快且定位清晰。

### 契约测试

Schema、artifact、API、prompt response、Agent branch。

### 集成测试

SQLite + JobLifecycle、provider mock、FastAPI routes、index。

### E2E

Go ingest → Job → pipeline → Gallery/Agent。成本高，应选择少量关键路径。

### Eval

测试模型/检索/Agent 质量，不应与普通 deterministic pytest 混为一类。

### Chaos/Reliability

重复投递、worker crash、fallback、stale claim。

## 2. pytest 基础

需要掌握：

- `assert`；
- fixture；
- `tmp_path`；
- monkeypatch；
- parametrize；
- marker；
- exception assertion；
- caplog；
- mock provider。

测试应避免：

- 依赖个人绝对路径；
- 调真实付费 API；
- 非固定随机数；
- 共享可变数据库；
- sleep 等待异步结果。

## 3. 本项目高价值测试

- `test_stage3_parsers.py`
- `test_stage3_prompt_builder.py`
- `test_semantic_gate.py`
- `test_stage3_checkpoint.py`
- `test_agent_conversation_graph.py`
- `test_agent_groundedness.py`
- `test_agent_guardrails.py`
- `test_agent_tool_protocol.py`
- `test_agent_eval_*`
- durable job/claim/fence 相关测试。

`ANSWER_BRANCH_CHECKLIST` 与专门测试类同步，是“代码内测试清单”的良好模式。

## 4. Mock 的边界

Mock 适合验证：

- 请求格式；
- timeout/fallback；
- parser；
- route；
- ledger；
- state transition。

Mock 不能证明：

- 真实模型遵循 schema；
- 真实吞吐；
- GPU 内存；
- VLM 质量；
- 云端限流行为。

因此 PR CI 用 mock，nightly/manual 用真实模型或固定 recorded run。

## 5. CI

典型 pipeline：

### Python

- 关键 lint；
- release gate；
- pytest；
- chaos smoke。

### Web

- TypeScript `tsc --noEmit`；
- Next build。

### Go

- `go vet`；
- `go build ./...`。

CI 应快速阻止确定性回归；重型 VLM eval 放 nightly 或手工门禁。

## 6. Release Gate

门禁可按风险分层：

- PR：schema/parser/router/fixtures；
- main/nightly：更大 eval；
- release：真实模型、关键 E2E、性能与安全；
- production observation：trace review。

每个 gate 必须定义：

- 输入集；
- 阈值；
- baseline；
- 允许波动；
- 失败处理；
- 结果 provenance。

## 7. 测试外部依赖

Redis、Ollama、LangGraph 等使用 marker。

关键生产依赖不能“缺包就 skip”而导致 CI 假绿。项目对 `requires_langgraph` 采用缺失即失败，符合默认生产 runtime。

## 8. 当前缺口

- Web 无 Jest/RTL；
- 无 Playwright；
- Go 已覆盖 preview 原子发布和生产 extractor 命令选择；ingest 幂等、事务回滚、
  Hook retry、race 与 malformed RAW 仍缺少系统测试；
- CI 不运行真实 VLM；
- 多容器完整 E2E 有限；
- 真实云端 provider 限流/安全依赖手工；
- 大页面使 UI 单测困难。

优先补：

1. Gallery load/selection/export E2E；
2. Studio submit→Job timeline；
3. Agent chat grounded refs；
4. fixture/live contract；
5. ingest duplicate/restart。

## 9. 动手实验

1. 为 parser 添加截断 JSON 参数化测试。
2. 为同一 job_id 重复 claim 添加并发测试。
3. 为 Agent 新增一个 router paraphrase fixture。
4. 用 FastAPI TestClient 验证 error 不泄露内部信息。
5. 建立 Playwright showcase smoke，不依赖真实 VLM。
6. CI 已运行 `go test -race ./...`；继续补 Go ingest 表驱动测试与
   `httptest.Server` Hook retry 测试。

## 10. 面试问题

- AI Eval 和单元测试有什么区别？
- 何时使用 mock，何时必须真实模型？
- 如何测试 at-least-once 消费者？
- 为什么缺 LangGraph 不应该 skip？
- 如何降低 flaky test？

## 11. 自测清单

- [ ] 能为纯函数/集成/E2E 选择测试层；
- [ ] 能使用 fixture/monkeypatch/parametrize；
- [ ] 能解释 mock 的证明边界；
- [ ] 能读懂 CI 三语言流程；
- [ ] 能设计 release gate；
- [ ] 能列出当前前端和真实模型测试缺口。

## 延伸资料

- [pytest Documentation](https://docs.pytest.org/)
- [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)
- [GitHub Actions](https://docs.github.com/actions)
- [Go Testing](https://go.dev/doc/tutorial/add-a-test)
- [React Testing Library](https://testing-library.com/docs/react-testing-library/intro/)
- [Playwright](https://playwright.dev/)

