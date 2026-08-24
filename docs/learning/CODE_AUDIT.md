# Livehouse Photography Agent — 基础设施代码审计

审计日期：2026-08-24  
审计口径：按 Staff Backend / AI Infrastructure Engineer 标准审查当前工作区；只记录能从代码、配置或测试直接证明的问题，不把个人项目规模本身当作缺陷。

## 审计结论

- 当前实现已经具备真正的基础设施骨架：SQLite 原子 claim、`claim_generation`、worker heartbeat、stuck-job scan、Celery late ack、有界推理队列、provider fallback ledger、结构化日志和 Prometheus 指标都不是纯文档包装。
- 最危险的缺口不在 `async/await`，而在 **任务恢复边界和副作用一致性**：数据库状态有 fencing，但文件产物、取消和 Go ingest 到 durable job 的交接没有相同强度的保证。
- “单机可靠任务系统”是代码支持的准确定位；“effectively-once”“全局推理背压”“可横向扩容的 cluster”“端到端 distributed tracing”则仍然说得过满。
- 已发现一个默认 Compose ingest profile 可稳定触发的 Critical 部署缺陷：runtime 镜像没有 Go 工具链或 preview-extractor 源码/二进制，但 Mode B 固定执行 `go run`。

## 修复状态（2026-08-24）

本轮已落地审计中可在当前单机架构内完成的代码修复。下方原始 findings 保留为审计证据，
不应再被当作当前代码状态：

- C1、H1、H3-H6、H9-H11 已修复；H2 增加 artifact 原子发布、session 文件锁及
  发布前后 claim fence，数据库与文件系统仍不宣称跨介质事务。
- H7 采用部署级短期约束：Compose worker 固定 `concurrency=1`、唯一容器 hostname，
  Ollama 不允许横向扩容；真正多 replica 只适用于具备服务端全局 admission 的 vLLM。
- H8 已在 API producer 启用 Celery instrumentation；当前依赖的 OpenTelemetry SDK
  负责 BatchSpanProcessor 的 fork reinitialization，业务 `trace_id` 仍只作为关联字段。
- M1-M6、M8-M11、M13-M22 的代码路径已修正或加上明确容量/降级边界；M7 保留为
  batching-ready abstraction，不再宣称客户端 production dynamic batching。
- 新增 Go 原子文件与命令选择测试、SQLite retry/seed/cancel invariant 测试，并让 CI
  执行 `go test -race ./...`。

仍然存在的架构边界不是可由局部补丁消除的问题：SQLite 只支持单机单 writer；
同步 HTTP provider 不能强制终止服务端已开始的 decode，只能通过 absolute deadline、
队列取消和 provider timeout 限制浪费；Ollama 的跨 replica 全局容量需要独立 inference
scheduler 或 provider 端 admission。

## 1. Critical Issues

### C1. Compose 的生产 ingest 路径无法执行 preview extractor

- **位置**：`cmd/ingest/ingest_sd_brain.go` — `runPreviewExtractor()` / `execCommandPreviewExtractor()`；`deploy/Dockerfile.ingest`；`deploy/docker-compose.yml` — `ingest`
- **证据**：
  - Mode B 在新照片归档后固定执行 `go run ./cmd/preview-extractor`。
  - build stage 只复制 `cmd/ingest/` 并只编译 `ingest_worker`。
  - runtime stage 是 `debian:bookworm-slim`，没有 Go 工具链，也没有 `cmd/preview-extractor` 源码；`--repo-root /app` 不能补齐这些文件。
- **触发路径**：按文档启动 Compose `ingest` profile，扫到任意新 ARW，完成 RAW 归档后进入 preview extraction；命令必然以 executable/file-not-found 失败。
- **底层原因**：开发环境用 `go run` 的调用约定被原样带进了只包含单个编译产物的 runtime image。
- **面试官可能问**：“多阶段构建最后复制了哪些 runtime artifacts？为什么生产容器还需要 `go run`？”
- **正确技术解释**：生产镜像应运行预编译 artifact，而不是依赖源码和编译器。当前 RAW 仍可能被安全归档，但 `Previews` 不会生成，后续 Gallery/分析链路无法按预期工作。
- **修改建议**：build stage 同时编译 `preview-extractor` 并复制到 `/usr/local/bin`；ingest 通过明确的 binary path/env 调用。增加 Compose ingest smoke test：挂载一张 fixture ARW，并断言生成有效 JPEG。

## 2. High Risk Issues

### H1. 连续 worker crash 会生成永远不可 claim 的 `QUEUED` 任务

- **位置**：`utils/luma_brain.py` — `claim_jobs()`、`requeue_stuck_jobs()`、`reconcile_exhausted_retryable_to_dead_letter()`；`services/job_lifecycle.py` — `JobLifecycle.claim()`
- **证据**：
  - claim 每成功一次执行 `attempt = attempt + 1`，且只允许 `attempt < max_attempts`。
  - stuck recovery 把 active job 改回 `QUEUED`，但不检查 claim budget。
  - dead-letter reconcile 只扫描 `status = 'FAILED_RETRYABLE'`，不扫描 `QUEUED`。
- **触发路径**：一个 `max_attempts=3` 的 job 被 claim 后 worker hard crash；重复三次后，第三次 stuck scan 会写成 `QUEUED, attempt=3`。它不再满足 claim/dispatch 的 `attempt < max_attempts`，也不满足 dead-letter reconcile 的状态条件。
- **底层原因**：attempt 的定义是“成功 claim 次数”，但 crash recovery 被描述成“不消耗 retry”；实际上 claim 已经消耗了 attempt。状态机缺少 `QUEUED + exhausted` 的归并规则。
- **面试官可能问**：“worker 连续在 ack 前崩溃三次后，这条任务最终是什么状态？为什么不会永远显示 QUEUED？”
- **正确技术解释**：at-least-once transport 只能重投 envelope；业务状态机仍必须保证每个 `(status, attempt)` 组合都能前进到 runnable 或 terminal。当前存在不可达终态的组合。
- **修改建议**：在 stuck requeue 的同一事务内，当 `attempt >= max_attempts` 时直接写 `DEAD_LETTERED`；或者扩展 reconcile 覆盖 exhausted `QUEUED`。补充“三次 claim 后每次 hard crash”的数据库级测试。

### H2. generation fence 只保护终态行，不保护文件副作用

- **位置**：`services/job_executor.py` — `JobExecutor._run_claimed()`；`services/processor/pipeline_stage_runner.py` — `run_write_artifact()`；`utils/luma_brain.py` — `update_job_status()`、`requeue_stuck_jobs()`
- **证据**：`runner.run_write_artifact()` 先写 `analysis_results.json`/gallery artifacts，之后 `lifecycle.succeed(..., fence_claim_generation=...)` 才检查 fence。
- **触发路径**：worker A 失联但仍在处理；stuck scan requeue 并增加 generation；worker B 接管；A 随后仍可覆盖共享路径上的产物，只是在最后写 `SUCCEEDED` 时被 `ClaimFenceError` 拒绝。
- **底层原因**：fencing token 约束了数据库 terminal write，却没有进入 artifact 命名、临时目录、publish/rename 或 manifest commit。数据库“旧 worker 不能赢”不等于外部副作用 exactly/effectively once。
- **面试官可能问**：“旧 worker 的 terminal update 被 fence 以后，它之前写出的 JSON 和图片怎么办？”
- **正确技术解释**：effectively-once 需要副作用本身幂等，或先写 generation-scoped 临时产物，再由持有最新 lease/fence 的事务发布 manifest。只 fence 状态行仍可能发生 stale side effect。
- **修改建议**：产物写入 `<job_id>/<claim_generation>/...` 临时命名空间；成功前再次校验 fence，再以原子 rename/manifest pointer 发布。对共享 cache 单独定义 content-addressed/idempotent 语义。

### H3. analyze 去重是 check-then-insert，不能防并发重复 job

- **位置**：`api/studio_routes.py` — `_enqueue_studio_analyze()`；`api/gallery_routes.py` — `enqueue_analysis()`；`utils/studio_sessions.py` — `find_runnable_analyze_job_id()`；`cmd/ingest/luma_brain_schema.sql` — `jobs`
- **证据**：API 先查询 runnable analyze job，再调用另一个 `BEGIN IMMEDIATE` 的 `create_job()`；schema 没有“同 session/path 只能有一个 active analyze job”的 unique constraint。
- **触发路径**：两个同步 HTTP handler 并发查询时都看到空结果，随后各自串行插入一条不同 job。两个 job 都能 claim，并发写同一 session 的 checkpoint 和最终 artifacts。
- **底层原因**：SQLite 的单 writer 只能串行写事务，不能让事务外的 read-check 与后续 insert 自动原子化。
- **面试官可能问**：“你说 API 幂等，唯一约束或 idempotency key 在哪里？”
- **正确技术解释**：应用层“先查再插”不是并发幂等。必须使用数据库唯一性、单事务 CAS/upsert，或 client idempotency key。
- **修改建议**：增加稳定的 `dedupe_key`/`idempotency_key` 列，并对 active request 使用可约束的独立表或 reservation row；在同一 `BEGIN IMMEDIATE` 中 get-or-create。不要依赖 JSON payload 路径扫描实现唯一性。

### H4. Go ingest 到 durable job 之间仍是一次性 HTTP 通知

- **位置**：`cmd/ingest/ingest_sd_brain.go` — `ingestSessionGroup()`、`triggerGalleryCheck()`；`celery_app.py` — beat schedule；`tasks/maintenance.py`
- **证据**：
  - ARW 成功归档并写成 `photos.status=INGESTED` 后，只启动一次 fire-and-forget goroutine 调 Gallery hook。
  - hook 只有 25 秒 timeout，没有 retry、outbox 或本地 pending notification 状态。
  - Celery beat 只重派已经存在的 runnable `jobs`，不会周期性把未建 job 的 `INGESTED` photos 转成 jobs。
- **触发路径**：API/网络在 hook 时短暂不可用；Go ledger 已经认为 ingest 成功，但没有 analyze job。之后扫描会按 fingerprint 跳过该照片，任务可以永久不进入 durable job 系统。
- **底层原因**：durability 从 job 创建以后才成立，Go→API 的交接仍是 best-effort edge trigger。
- **面试官可能问**：“Redis 和 API 同时重启时，已经复制成功的照片如何保证最终被分析？”
- **正确技术解释**：可靠交接需要 transactional outbox、可重放 inbox，或由周期 reconciler 从 SQLite 的 `INGESTED without job` 状态重新发现工作。
- **修改建议**：最小方案是在 beat/独立 reconciler 周期执行 `process_brain_ingested`；更完整方案是在 Go 的同一 SQLite 事务写 outbox/job seed，再由 dispatcher 标记 delivered。hook 只用于降低延迟，不应是唯一触发器。

### H5. preview extractor 会把失败或中断留下的文件当作成功缓存

- **位置**：`cmd/preview-extractor/main.go` — `copyFile()`、`extractPreviewJPEG()`、worker goroutine
- **证据**：
  - 目标存在时只做 `os.Stat(dstFile)`，不校验大小、JPEG 完整性或 source fingerprint。
  - JPEG copy 使用 `os.ReadFile` + `os.WriteFile` 直接覆盖最终路径。
  - RAW preview 先 `os.Create(dst)`，再把 `exiftool` stdout 直接写入最终路径；命令失败不会删除空文件或 partial file。
- **触发路径**：磁盘满、进程 kill、exiftool 失败；目标路径留下零字节/截断 JPEG。下一次运行看到文件存在，增加 `ok` 并永久跳过。
- **底层原因**：缓存命中条件只有 pathname existence，写路径没有 temp + fsync + atomic rename。
- **面试官可能问**：“preview extraction 崩溃恢复如何区分完整文件和 partial file？”
- **正确技术解释**：文件型幂等至少需要临时文件、成功校验和原子发布；仅 `Stat` 不是 completion marker。
- **修改建议**：写入同目录唯一 `.partial`，命令成功后验证 JPEG SOI/EOI 或 decode，再 `fsync + rename`；缓存命中至少检查 non-zero、decode 和可选 source metadata。

### H6. Compose 文档声称可 scale worker，但所有 replica 使用固定 hostname

- **位置**：`deploy/docker-compose.yml` — `celery-worker-general.hostname`、顶部 scale 示例；`deploy/docker-entrypoint.sh` — Celery `-n "${pool}@%h"`；`infra/worker_manager.py` — `brain_worker_hostname()`；`cmd/ingest/luma_brain_schema.sql` — unique `workers(worker_name)`
- **证据**：general worker 固定 `hostname: celery-general`；scale 多个 replica 后 `%h` 和 SSOT `worker_name` 相同，Celery node name 及 SQLite worker row 都冲突/合并。
- **影响**：
  - Celery inspect/ping 可能只看到同名节点中的一个响应。
  - 多容器 heartbeat、status 和 capacity 写到同一 worker row。
  - pause/drain 不能精确控制单 replica。
  - live inflight 和 capacity 被错误合并，扩容可能不增加实际 admission headroom。
- **面试官可能问**：“`docker compose --scale=2` 后 worker identity 如何保证唯一？监控看到几个 worker？”
- **正确技术解释**：worker identity 必须至少包含 replica/container identity；逻辑 pool name 与实例 identity 不能是同一个字段。
- **修改建议**：移除固定 `hostname`，使用 Compose/container hostname；Celery node name 使用 `${pool}@%h`；worker unique key 改为 `(pool, instance_id)` 或直接使用稳定 instance UUID。

### H7. 推理并发限制是进程内限制，不是 provider/主机级全局限制

- **位置**：`inference/queue.py` — `PrioritizedInferenceQueue.__init__()`；`inference/client.py` — `inference_client_from_model_config()`；`infra/worker_capacity.py` — `resolve_advertised_worker_capacity()`；`deploy/docker-compose.yml`
- **证据**：每个 Celery prefork child/进程各自创建 `BoundedSemaphore` 和 worker threads。YAML 的 `model.max_concurrent_requests` 没有共享 semaphore，也不会自动传入 `LIVEHOUSE_WORKER_INFERENCE_PROVIDER_SLOTS`。
- **触发路径**：提高 Celery concurrency 或增加 replica 后，每个进程都可向同一个 Ollama/vLLM endpoint 发起自己的上限数量。配置中已记录双实例 Qwen 在 24GB 主机发生过 OOM，说明资源超卖不是理论问题。
- **底层原因**：process-local backpressure 无法协调跨进程/跨容器的共享 GPU/provider capacity。
- **面试官可能问**：“四个 Celery process 都配置 `max_concurrent_requests=1`，Ollama 最多会同时收到几个请求？”
- **正确技术解释**：最多是活跃进程数，而不是 1。全局限制需要 provider 端调度、Redis semaphore、集中 inference service，或确保只有一个 inference executor process。
- **修改建议**：短期在部署配置强制 `--concurrency=1` 并设置 worker admission env；长期把 inference queue 做成独立服务，由所有 Celery worker RPC 到一个全局 scheduler/provider pool。

### H8. OpenTelemetry 的 API→Celery 链路和 prefork 初始化不完整

- **位置**：`gallery_server.py` — OTEL bootstrap；`infra/otel_bootstrap.py` — `configure_otel_from_env()`、`instrument_celery()`；`celery_app.py`；`api/gallery_routes.py`、`api/studio_routes.py` — 独立 Celery clients
- **证据**：
  - API 进程调用 `configure_otel_from_env()` 和 FastAPI instrumentation，但没有调用 `instrument_celery()`；producer `send_task()` 因而不能保证注入 W3C context。
  - Celery worker 在 app import 时、prefork 之前创建 `TracerProvider` 和 `BatchSpanProcessor`；代码没有在 `worker_process_init` 中重新初始化 exporter/processor。
  - `trace_id` 业务列会被加载并作为 span attribute，但 attribute 不会把新 span 连接到原 HTTP trace。
- **底层原因**：业务 correlation id 与 OTEL trace context 是两套机制；fork 后 exporter background thread 也不能假设仍然健康。
- **面试官可能问**：“Tempo 中如何从 POST analyze 点击到 Celery task 和 provider span？只是相同 attribute，还是同一 trace parent-child？”
- **正确技术解释**：同名 `trace_id` attribute 只支持搜索，不建立 trace parentage。需要 producer header propagation、consumer extraction，并在 prefork child 初始化 SDK/exporter。
- **修改建议**：API bootstrap 同时启用 Celery producer instrumentation；在 Celery process init signal 中配置 child-local provider，并在 shutdown force flush。增加集成测试，断言 producer/consumer span 共享 trace id 和 parent link。

### H9. 本地 “full stack” 启动脚本没有 Celery Beat

- **位置**：`start_all.sh` — Celery 启动段；`celery_app.py` — beat schedule；`tasks/maintenance.py`
- **证据**：脚本只启动 Redis、Celery worker、FastAPI 和 Web，没有启动 `celery beat`；Compose 则有独立 `celery-beat` service。
- **触发路径**：按 README 用 `./start_all.sh` 启动；worker crash 后 SSOT 停留 active，或 job 进入 `FAILED_RETRYABLE`/manual retry `QUEUED`。没有 Beat 时 stuck requeue 和 runnable redispatch 都不会执行。
- **底层原因**：可靠性设计依赖周期 reconciler，但本地默认运维拓扑没有部署它。
- **面试官可能问**：“为什么 Redis 重投一条 active job 消息仍不能恢复？”
- **正确技术解释**：重复 envelope 会被原子 claim 拒绝；必须先由 DB reconciler 把 stale active row 改回 runnable。
- **修改建议**：`start_all.sh` 启动并管理 beat，或明确标为非 durable dev mode；增加 smoke test 检查 maintenance schedule 实际运行。

### H10. `model_runs` 同时记录 provider call 和 Stage3 image result，指标可能双计

- **位置**：`inference/queue.py` — `_ledger_create_run_start()` / `_finalize_infer_payload()`；`services/processor/pipeline_stage_runner.py` — `_record_model_run_for_stage3()`
- **证据**：queue 在带 `job_id` 的每次 provider inference 前后写一条 `model_runs`；Stage3 runner 对同一图片结果又创建并完成一条 `model_runs`，两者没有 `run_kind`、parent id 或唯一关联。
- **影响**：请求数、成功率、token/cost、fallback rate 若直接按 `model_runs` 聚合，会把 HTTP attempt 级和 image outcome 级数据混为同一事实，可能重复计数。
- **面试官可能问**：“`model_runs` 一行代表 provider hop、一次 router request，还是一张图的 Stage3 outcome？”
- **正确技术解释**：这三种粒度不应复用一个无类型的事实表。attempt 应属于 request，image outcome 应属于 stage execution。
- **修改建议**：确定唯一 authority；或增加 `run_kind` 与 `parent_model_run_id`，查询/指标必须按粒度过滤。补一张图片成功时预期行数的回归测试。

### H11. 多个重 CPU/模型路由仍在 FastAPI 默认线程池执行

- **位置**：`api/gallery_routes.py` — `get_film_render()`、`export_images()`、`index_session_embeddings()`、`get_similar_photos()`；`api/agent_routes.py` — chat routes；`gallery_server.py` — Uvicorn 单进程
- **证据**：这些同步 `def` 不阻塞 event loop，但会在 Starlette/AnyIO 默认线程池中运行 OpenCV/PIL/NumPy/Torch、RAW develop 或等待同步模型 HTTP；Uvicorn 只启动一个 process。
- **影响**：Lab cache miss、同步 export、CLIP 索引和聊天并发时会耗尽 thread tokens；`limit_concurrency=48` 不能替代按 workload 的 CPU/RAM/GPU admission。
- **面试官可能问**：“全部写成 sync def 后 event loop 安全，为什么 API 仍会 head-of-line blocking？”
- **正确技术解释**：event loop safety 不等于 capacity isolation；有限线程池仍是共享资源，CPU-heavy native code还会竞争核心和内存带宽。
- **修改建议**：film render/cache miss 与 embeddings index 使用独立 bounded executor 或 Celery；同步 export 设置硬上限；分别记录 route queue wait 与 execution latency。

## 3. Medium Issues

### M1. `acks_late` 没有配套 worker-lost 与 task timeout 策略

- **位置**：`celery_app.py` — `celery_app.conf.update()`；`tasks/run_job.py` — `run_job()`
- **问题**：已配置 `task_acks_late=True` 和 `worker_prefetch_multiplier=1`，但没有 `task_reject_on_worker_lost`、soft/hard time limit、Redis `visibility_timeout` 的显式策略。
- **底层原因**：`acks_late` 不等价于“任何 worker crash 都一定重新投递”；Celery prefork child 异常退出时，parent 的默认处理仍可能 ack worker-lost task。当前可靠性主要来自 SQLite stuck scan，而不是 broker redelivery。
- **面试官可能问**：“SIGKILL child、OOM kill container、网络断开三种情况，消息分别会不会重投？”
- **正确解释**：传输语义与 SSOT recovery 是两层。当前即使 envelope 不重投，beat 理论上可重新发现 job；但恢复延迟和 exhausted bug 必须单独处理。
- **建议**：明确并测试 worker-lost policy；为任务定义按 job 类型的 hard/soft limit；文档不要把 `acks_late` 单独表述为 crash recovery 保证。

### M2. retry 没有 `next_run_at`、指数退避或 jitter

- **位置**：`utils/luma_brain.py` — `fail_job_retryable()`、runnable queries；`tasks/maintenance.py` — `dispatch_runnable_jobs()`
- **问题**：`FAILED_RETRYABLE` 立即属于 runnable；周期 dispatcher 每 60 秒扫描，没有 job-level backoff timestamp。
- **影响**：provider/磁盘等共享依赖故障时，大量 job 会同步重试，形成 retry storm；三次 attempt 很快耗尽，且无法区分“稍后恢复”与永久错误。
- **面试官可能问**：“Redis/Ollama 挂 10 分钟时，100 个 job 的 retry 时间线是什么？”
- **正确解释**：可靠 retry 需要 error classification + attempt budget + exponential backoff + jitter + deadline；周期轮询间隔不是完整 retry policy。
- **建议**：增加 `next_attempt_at`、backoff policy 和随机 jitter；dispatch SQL 只选择到期任务；对 429/transport/disk-full 使用不同策略。

### M3. cancel 是状态更新，不是执行取消

- **位置**：`api/infra_routes.py` — `infra_cancel_job()`；`services/job_executor.py`；`inference/queue.py` — `shutdown()`；provider `requests.post()`
- **问题**：cancel 不增加 generation、不通知正在运行的 pipeline、不取消 queued/inflight inference，也不终止 provider request。
- **影响**：用户看到 `CANCELLED` 后，CPU/GPU、文件写和 cache 写仍继续；如果 cancel 恰好发生在 claim 与未 fenced 的 progress update 之间，旧执行还可能把状态写回 active。
- **面试官可能问**：“取消以后多久释放 GPU？已经排队的模型请求如何移除？”
- **正确解释**：状态取消、cooperative cancellation 和强制 preemption 是三件事。同步 `requests` 只能靠 timeout，不能真正撤销服务端 decode。
- **建议**：cancel 事务增加 generation；pipeline stage 边界检查 cancellation token；queue future 支持 dequeue/cancel；provider 侧若支持 request id，则调用 abort API。

### M4. progress 状态更新没有 fence，状态机也不强制 transition

- **位置**：`services/job_lifecycle.py` — `update_status()`、`start_pipeline_*()`；`utils/luma_brain.py` — `update_job_status()`
- **问题**：只有 succeed/fail 调用传 fence；progress transition 不传。`update_job_status()` 明确允许任何 from→to。
- **影响**：cancel/requeue 与旧 worker 的 progress write 竞争时，状态可能倒退；测试/脚本也能绕过 claim 直接 `QUEUED→PREPROCESSING`。
- **面试官可能问**：“状态机在哪里被数据库或 CAS 强制执行？”
- **正确解释**：当前是 documented state machine，不是 enforced state machine。CHECK 只验证状态值，不验证边。
- **建议**：所有 executor transition 都传 generation/worker fence，并在 SQL `WHERE status IN (...) AND generation=?` 中 CAS；operator repair 使用单独显式接口。

### M5. `healthz`、`readyz` 和 provider health 语义不一致

- **位置**：`gallery_server.py` — `healthz()`、`readyz()`；`infra/health.py` — `provider_health()`、`health_report()`
- **问题**：
  - `readyz` 只检查 SQLite；Redis 不可用时仍 ready，但多数 mutation 无法立即 dispatch。
  - `provider_health()` 无论 provider 名称都请求 Ollama `/api/tags`；vLLM/OpenAI provider 会被误报 unhealthy。
  - `healthz` 做多个外部同步请求，适合 diagnostic health，不适合作为高频 liveness probe。
- **面试官可能问**：“provider 挂了 API 是否应该被摘流？ready 与 healthy 的契约是什么？”
- **正确解释**：liveness 只回答进程需不需要重启；readiness 回答是否能承担约定流量；dependency diagnostics 应独立且 provider-specific。
- **建议**：定义 `/livez`、`/readyz`、`/healthz` 契约；ready 至少检查 DB 写能力和 broker，provider 是否纳入取决于 API 是否允许只排队；vLLM 用 `/health` 或 `/v1/models`。

### M6. queue wait 超时且没有 fallback model 时，文档说报错，代码仍调用 primary

- **位置**：`configs/livehouse.yaml` — `fallback_model_name` 注释；`docs/OLLAMA_INTEGRATION_NOTES.md`；`inference/queue.py` — `_run_job_once()`；`inference/router.py` — `InferenceRouter.infer()`
- **问题**：queue 设置 `force_fallback=True`，但 router 只有在 `f_model` 非空时才走 fallback；否则继续 primary。
- **面试官可能问**：“等待超过 SLA 且没有降级模型时，系统是 fail-fast 还是继续排队？”
- **正确解释**：当前 admission acquire timeout 可以报错，但“已入队后 queue wait 超时”的路径不会按文档 fail-fast。
- **建议**：明确 policy：无 fallback 时返回 typed queue timeout；或修改文档承认继续 primary。增加 `max_queue_size > num_workers` 的测试。

### M7. dynamic batching 是框架能力，不是当前生产能力

- **位置**：`inference/queue.py` — `_collect_jobs_for_lane()`、`_run_multi_jobs()`；`inference/providers/ollama.py`、`inference/providers/vllm.py` — `supports_batch()`
- **问题**：两个实际 provider 都返回 `False`；vLLM 依赖服务端 continuous batching。客户端 `max_batch_size`/aggregate window 在当前 provider 配置中不会合批。
- **面试官可能问**：“你们 batch size=8 的吞吐提升数据在哪里？哪一个 provider 实现了 `generate_batch`？”
- **正确解释**：当前是 batching-ready abstraction；Ollama 单请求，vLLM 是 server-side continuous batching，不应宣称客户端 dynamic batching 已落地。
- **建议**：文档统一用“batching hook/dormant capability”；只有接入真实 batch provider并完成吞吐/延迟测试后再作为架构亮点。

### M8. 同步 export 仍可长时间占用 FastAPI thread token

- **位置**：`api/gallery_routes.py` — `ExportRequest.background`、`export_images()`、`_export_images_impl()`
- **问题**：route 是 `def`，所以不会阻塞 event loop，这是正确的；但 `background` 默认仍为 `False`，直接 API caller 会在 AnyIO threadpool 中做 RAW decode/film render，并为每个请求再创建两个线程。
- **影响**：并发长 export 会耗尽 FastAPI thread tokens、增加内存峰值；`limit_concurrency` 不是 CPU/线程池 admission。
- **面试官可能问**：“同步 def 不阻塞 event loop，为什么仍会拖垮 API？”
- **正确解释**：offload 只是把阻塞从 event loop 移到有限 threadpool；长 CPU/I/O 工作仍占资源并产生 head-of-line blocking。
- **建议**：服务端对大 export 强制 durable background job，或设置独立 executor/semaphore；同步路径只保留明确的小请求上限。

### M9. 已超时的 inference caller 不会取消实际工作

- **位置**：`inference/queue.py` — `submit()`、`submit_future()`、`_worker_loop()`；`inference/client.py` — `predict_async()`
- **问题**：`Future.result(timeout=cap)` 超时后只返回 error，job 仍可能在 queue/provider 中执行；async task cancellation 也只取消 `asyncio.to_thread` 的等待，不能停止底层线程。
- **面试官可能问**：“客户端断开或 coroutine cancel 后，模型 decode 会停止吗？”
- **正确解释**：Python thread cancellation 是 cooperative；`asyncio.to_thread` 不会杀线程。同步 provider request 需要 deadline 和服务端 abort 才能释放模型资源。
- **建议**：future 增加 cancellation flag并在 dequeue 前跳过；传播 absolute deadline；provider 支持时使用 request-id abort。指标区分 client-abandoned 与 provider-completed。

### M10. Go daemon 没有 graceful shutdown 或 subprocess cancellation

- **位置**：`cmd/ingest/main.go` — infinite loops；`cmd/ingest/scanner.go` — background `runSession()`；`cmd/ingest/executor.go`、`ingest_sd_brain.go` — `exec.Command()`
- **问题**：没有 `signal.NotifyContext`；goroutine、ticker、`go run`/Python subprocess 不接 context。容器停止时无法等待当前复制、保存 state 或终止 child process tree。
- **面试官可能问**：“SIGTERM 到来时，如何保证不留下 partial preview 和孤儿 Python process？”
- **正确解释**：goroutine 生命周期需要 owner、context 和 join；`exec.CommandContext` 只杀直接 child，shell pipeline 还需要 process-group policy。
- **建议**：main 建立 root context；scan/runSession 接收 context；使用 `exec.CommandContext` 和明确 process-group cleanup；shutdown 等待有限 grace period。

### M11. `arw-extractor` 对整张 RAW 做全量读取和二次扫描

- **位置**：`cmd/arw-extractor/main.go` — `ExtractJPEG()`、`findJPEGData()`
- **问题**：`io.ReadAll` 把整张 ARW 放入内存；对每个 SOI 再向后搜索 EOI，恶意/异常文件最坏可接近 O(n²)，且找到的第一个 JPEG 片段未必是最佳 preview。
- **面试官可能问**：“一张 120MB RAW 或 malformed RAW 的内存和时间复杂度是多少？”
- **正确解释**：当前工具适合小规模离线辅助，不是 streaming parser，也不是可靠 TIFF/EXIF parser。
- **建议**：优先复用 exiftool/libraw；若自实现，使用 buffered streaming marker parser并设置最大 preview size。

### M12. Go 路径没有自动化行为测试

- **位置**：`cmd/*`；`.github/workflows/ci.yml`；`docs/learning/13-testing-ci.md`
- **证据**：`go test ./...` 只报告四个 package `[no test files]`。当前 CI 的 build/vet 只能证明编译，不证明 ingest 幂等、partial 文件、hook retry、race 和 SQLite rollback。
- **面试官可能问**：“你如何证明 `--workers=32` 没有 race？如何测试拔卡、磁盘满和 hook timeout？”
- **正确解释**：编译和 vet 不是并发/故障恢复验证；至少需要 table tests、tempdir、`httptest.Server`、fake command runner 和 `go test -race`。
- **建议**：优先覆盖 H4/H5、atomic copy、重复扫描、SIGTERM；之后在 CI 增加 `go test -race ./...`。

### M13. 已存在 job 的 API 响应返回了错误的业务 trace id

- **位置**：`api/studio_routes.py` — `_enqueue_studio_analyze()`；`api/gallery_routes.py` — `enqueue_analysis()`
- **问题**：函数先生成新的 `trace_id`，发现 existing job 后返回这个新 id，而不是 existing job row 的 `trace_id`。
- **影响**：调用方拿到的 trace id 无法关联实际 worker logs/model_runs；去重请求反而制造虚假 correlation id。
- **面试官可能问**：“idempotent retry 返回的 trace id 是 request trace 还是 job trace？两者如何区分？”
- **正确解释**：request trace 与 durable job trace 是不同实体。返回 existing job 时应同时给 `request_trace_id` 和 canonical `job_trace_id`，不能混用。
- **建议**：查询 existing row 的 canonical trace；响应字段明确拆分。

### M14. Prometheus 部分指标的语义容易被误读

- **位置**：`infra/metrics.py` — inference histograms、`gpu_util_estimate_30s`；`infra/prometheus_server.py`
- **问题**：
  - inference latency buckets 最大普通 bucket 只有 12 秒，而配置允许单次 300 秒，多数 VLM 请求只进入 `+Inf`，难以计算有意义的 p95。
  - `gpu_util_estimate` 实际是 provider worker busy-time ratio，不是硬件 GPU utilization；代码虽带 `source=estimate`，面试或 dashboard 容易简称为 GPU 利用率。
  - multiprocess Gauge 需依赖查询端正确聚合各 pid/进程值。
- **面试官可能问**：“这个 GPU util 与 powermetrics/nvidia-smi 的定义一致吗？Prometheus p95 为什么全落在 +Inf？”
- **正确解释**：busy ratio 是 scheduler saturation proxy，不是 device telemetry；histogram bucket 必须覆盖真实分布。
- **建议**：VLM buckets 扩到 30/60/120/300/600 秒；指标名加入 `_worker_busy_ratio`；仅把真实 sampler 值称为 GPU utilization。

### M15. `requests` provider 每次新建连接，缺少统一 deadline/cancellation abstraction

- **位置**：`inference/providers/ollama.py`、`inference/providers/vllm.py`；`inference/client.py`
- **问题**：每次 `requests.post/get` 使用模块级 convenience API，没有复用 `Session` 连接池；timeout 是每 attempt connect/read，不是跨 queue、retry、fallback 的 absolute deadline。
- **面试官可能问**：“provider retry × router fallback × stage retry 的最坏总耗时是多少？”
- **正确解释**：per-hop timeout 不能限制端到端 latency；多层 retry 会相乘。当前文档已经指出该风险，但代码没有统一 attempt/deadline budget。
- **建议**：provider 实例持有可关闭的 `Session`；request metadata 传 absolute deadline；router 在每个 hop 前计算 remaining budget。

### M16. YAML 的 `inference_batch_size` 没有接到 queue batching

- **位置**：`configs/livehouse.yaml`、`configs/livehouse.docker.yaml`、`utils/config_loader.py`；`inference/client.py` — `inference_client_from_model_config()`
- **问题**：配置和 `engine/models/vlm_model.py` 使用 `inference_batch_size`，client 实际只读取 `max_batch_size`。修改现有 YAML 键不会改变 queue batching。
- **面试官可能问**：“配置 batch=8 后哪一行把它传给 provider？”
- **正确解释**：当前该配置键是 no-op；即便接线，生产 provider 也尚未支持 client-side native batch。
- **建议**：统一键名并对未知/未消费配置做启动校验；不要保留看似生效的 dead configuration。

### M17. Mode B 没有文件稳定窗口

- **位置**：`cmd/ingest/ingest_sd_brain.go` — `sdScanOnce()`；对比 `cmd/ingest/scanner.go` 的 Mode A stable window
- **问题**：Mode B 扫到 ARW 后立即按当前 size/mtime fingerprint 并复制，没有双采样或 stable-seconds。如果相机/读卡器仍在写文件，可能归档截断版本并把该 fingerprint 标成 INGESTED。
- **面试官可能问**：“轮询看到文件存在，如何证明写入已经结束？”
- **正确解释**：文件存在/当前 Stat 不是 completion signal。若产品约束是“拔卡后才运行”，必须明确写进 contract；否则要做两次 size+mtime 稳定检查。
- **建议**：支持 per-file stable window 或要求只读、已卸载写入方的介质；测试写入过程中扫描。

### M18. Gallery hook 不检查非 2xx HTTP 响应

- **位置**：`cmd/ingest/ingest_sd_brain.go` — `triggerGalleryCheck()`
- **问题**：只有 transport error 会记录 warning；401/500/503 都被当作成功关闭 body。它放大 H4 的 durable handoff 缺口。
- **面试官可能问**：“API 返回 401 时 ingest 日志和重试状态是什么？”
- **正确解释**：HTTP request 成功发送不等于业务接受。至少应检查 status code、记录有限响应摘要，并进入可重试 outbox。
- **建议**：非 2xx 视为失败；有限指数退避；最终依赖 reconciler 而非 goroutine 重试保证正确性。

### M19. health/readiness 在 body 报失败，但 HTTP 仍恒为 200

- **位置**：`gallery_server.py` — `healthz()`、`readyz()`；`infra/health.py`
- **问题**：`ok=false` 不转换为 503；编排器只看 HTTP code 时仍持续送流。`readyz` 又只检查 DB，且 0 个 worker 时 freshness 仍可判 healthy。
- **面试官可能问**：“Redis/provider/worker 全挂时负载均衡器看到什么状态码？”
- **正确解释**：当前是 diagnostic JSON，不是可靠 probe contract。liveness、readiness、capability degradation 必须分开。
- **建议**：定义生产 probe policy并返回对应 HTTP code；Compose 给 API 增 healthcheck；provider-specific 深检查保持低频。

### M20. JSON structured logger 会丢弃未登记的 `extra` 字段

- **位置**：`utils/logging_setup.py` — `STRUCTURED_KEYS`、`JsonLineFormatter`；调用方如 `tasks/maintenance.py` 的 `event`、`services/job_executor.py` 的 `claim_err`
- **问题**：formatter 只输出 canonical keys；调用方传入的 `event`、`claim_err` 等不会进入 JSON payload。
- **面试官可能问**：“为什么文本日志看到 dispatch event，Loki JSON 查询却没有该字段？”
- **正确解释**：Python `LogRecord` 持有 extra 不代表自定义 formatter 会序列化它。
- **建议**：扩充/版本化 schema，或让 formatter安全收集非标准 extra；增加日志 contract 单测或 lint。

### M21. curation job 的 `running` 状态不能阻止重复执行

- **位置**：`services/agent/curation_jobs.py` — `run_curation_job()`
- **问题**：只有 `queued→running` 使用条件更新；重复 Celery delivery 读到 `running` 后没有 early return，仍可再次执行 curation。
- **面试官可能问**：“为什么 pipeline job 能防重复，curation job 不能？”
- **正确解释**：两个 runtime 使用不同 SSOT/claim 实现；不能把主 jobs 表的原子 claim 保证外推到 agent store。
- **建议**：`running` 直接拒绝，或实现 lease/claim token；增加 duplicate-delivery 集成测试。

### M22. 推理降级计时不包含 semaphore admission 等待

- **位置**：`inference/queue.py` — `submit_future()`、`_run_job_once()`
- **问题**：`enqueued_mono` 在成功 acquire 之后才设置；等待 slot 的时间不会进入 `force_fallback` 的 queue wait。拿不到 slot 走 admission timeout，和文档描述的 queue fallback 是两条不同路径。
- **面试官可能问**：“用户实际等了 120 秒，为什么 ledger 的 queue_wait 接近 0？”
- **正确解释**：当前指标只统计 admitted-to-worker PQ wait，不统计 pre-admission backpressure。
- **建议**：分别记录 `admission_wait_ms` 与 `queue_wait_ms`，并用端到端 deadline决定 fail/fallback。

## 4. Interview Traps

### T1. “我们通过 Celery 实现 exactly/effectively once”

- **涉及文件**：`celery_app.py`、`utils/luma_brain.py`、`services/job_executor.py`
- **追问**：“late ack、duplicate delivery、artifact overwrite 分别由谁处理？”
- **应答**：Celery 提供 at-least-once 倾向；SQLite transaction claim 防同一 job row 并发执行；generation fence 防 stale terminal write；但 artifact side effect 尚未 fenced，所以不能无条件声称 effectively-once。

### T2. “SQLite WAL 支持并发写”

- **涉及文件**：`utils/luma_brain.py` — `brain_connect()`；`cmd/ingest/luma_brain.go` — `OpenLumaBrain()`
- **追问**：“WAL 有几个 writer？busy timeout 解决了什么？”
- **应答**：WAL 改善 reader/writer 并发，但仍只有一个 writer。`busy_timeout` 是等待锁，不是消除锁竞争；当前适合单机低写吞吐，不适合多节点共享 volume。

### T3. “async FastAPI 不会被 OpenCV 阻塞”

- **涉及文件**：`gallery_server.py`、`api/gallery_routes.py`
- **追问**：“这些 route 为什么大多是 `def`？RAW export 在哪里运行？”
- **应答**：sync route 由 FastAPI/Starlette 放入 threadpool，所以不直接阻塞 event loop；但长 export 会占 thread token 和 CPU/RAM，仍需 durable worker/admission。

### T4. “Python 线程能并行 OpenCV/NumPy”

- **涉及文件**：`services/processor/pipeline_stage_runner.py`
- **追问**：“GIL 是否让 ThreadPoolExecutor 完全无效？”
- **应答**：纯 Python CPU 代码受 GIL；OpenCV/NumPy/rawpy 的很多 native kernels 会释放 GIL，因此线程可能并行。但实际收益受内部线程、内存带宽和 decode I/O 限制，必须 benchmark；不是“线程天然绕过 GIL”。

### T5. “推理 queue 保证全局 concurrency=1”

- **涉及文件**：`inference/queue.py`、`deploy/docker-compose.yml`
- **追问**：“两个 prefork child、两个 Compose replica 时是多少？”
- **应答**：queue/semaphore 是进程内的；总并发是各进程之和，除非部署强制单 inference process 或 provider 前有共享调度。

### T6. “有 dynamic batching”

- **涉及文件**：`inference/queue.py`、`inference/providers/vllm.py`
- **追问**：“哪一个生产 provider 的 `supports_batch()` 返回 true？”
- **应答**：当前没有。vLLM 是服务端 continuous batching；客户端代码只是预留 native batch hook。

### T7. “有完整 distributed tracing”

- **涉及文件**：`infra/otel_bootstrap.py`、API Celery producer、`celery_app.py`
- **追问**：“业务 trace_id 与 W3C traceparent 的关系是什么？”
- **应答**：业务 id 是可搜索 correlation attribute；真正 parent-child trace 还依赖 producer/consumer context propagation 和 prefork-safe SDK 初始化，当前不完整。

### T8. “worker crash 会自动 dead-letter”

- **涉及文件**：`requeue_stuck_jobs()`、`reconcile_exhausted_retryable_to_dead_letter()`
- **追问**：“连续 hard crash 后为什么不是 FAILED_RETRYABLE？”
- **应答**：hard crash 不经过 failure classifier；由 stuck scan 回 QUEUED。当前 exhausted QUEUED 漏洞会把 job 卡住，修复前不能声称自动 DL 完整。

### T9. “Go ingest 是 durable 的”

- **涉及文件**：`cmd/ingest/ingest_sd_brain.go`
- **追问**：“hook 丢失后谁重建 job？”
- **应答**：RAW copy 和 photo ledger durable；从 INGESTED photo 到 analyze job 的通知不是 durable，需 reconciliation/outbox。

### T10. “health/readiness 已覆盖依赖”

- **涉及文件**：`infra/health.py`、`gallery_server.py`
- **追问**：“vLLM 为什么请求 `/api/tags`？Redis 挂了 `/readyz` 返回什么？”
- **应答**：当前 health 是混合 diagnostic，ready 仅 DB；需要先给 endpoints 定义稳定契约。

### T11. “model_runs 就是模型调用次数”

- **涉及文件**：`inference/queue.py`、`services/processor/pipeline_stage_runner.py`
- **追问**：“一张图一次成功推理会写几行？每行粒度相同吗？”
- **应答**：当前可能同时写 provider/request 级和 Stage3 image outcome 级行，直接 `COUNT(*)` 不能当调用次数或成本 authority。

### T12. “Compose ingest profile 是已经验收的生产路径”

- **涉及文件**：`deploy/Dockerfile.ingest`、`cmd/ingest/ingest_sd_brain.go`
- **追问**：“runtime image 里 preview-extractor binary 在哪？”
- **应答**：当前镜像只含 ingest_worker，preview 子进程无法启动；这是部署 artifact 闭环缺失，不应归因于环境偶发错误。

## 5. Architecture Claims Not Fully Supported by Code

### A1. “effectively-once executor”

- **声称位置**：`docs/learning/05-durable-jobs.md`、`docs/PROJECT_TECHNICAL_OVERVIEW.md`
- **缺口**：claim 和 terminal DB write 接近 effectively-once，但 artifacts、cache、preview 和部分 downstream task enqueue 不在 fence/事务内。
- **准确表述**：**at-least-once delivery + single-row atomic claim + fenced terminal state；external side effects 仍需幂等/publish fencing。**

### A2. “single-host cluster 可横向 scale”

- **声称位置**：`deploy/docker-compose.yml` 顶部注释与 scale 命令
- **缺口**：固定 hostname/worker_name 让 replicas 身份碰撞；SQLite/shared local artifacts 也限定为单机共享文件系统。
- **准确表述**：**单机 Compose 多进程拓扑演示，不是可无状态横向扩容的分布式平台。**

### A3. “统一推理层负责全局准入和背压”

- **声称位置**：`docs/learning/15-interview-question-bank.md`、`docs/learning/00-project-map.md`
- **缺口**：统一的是代码接口和单进程 queue；跨 Celery process/provider endpoint 的全局容量没有共享协调。
- **准确表述**：**per-process bounded inference queue；部署必须额外约束总 process 数或提供集中 scheduler。**

### A4. “动态 batching”

- **声称位置**：`inference/queue.py` 模块说明、技术概览相关描述
- **缺口**：生产 provider 无 native `generate_batch`；vLLM batching 发生在服务端。
- **准确表述**：**客户端预留 batching abstraction，当前有效吞吐能力来自 vLLM server-side continuous batching。**

### A5. “完整 trace propagation”

- **声称位置**：`docs/learning/12-observability-deployment-security.md`、`docs/PROJECT_TECHNICAL_OVERVIEW.md`
- **缺口**：API producer Celery instrumentation/prefork child exporter 生命周期没有闭环；业务 id 传播不等于 OTEL context propagation。
- **准确表述**：**有结构化 correlation ids 和可选 OTEL spans；跨 API/Celery 的同一 trace 仍需集成验证。**

### A6. “durable jobs 覆盖 ingest 主路径”

- **声称位置**：`docs/learning/00-project-map.md`、`docs/learning/05-durable-jobs.md`
- **缺口**：job 创建后的恢复较完整，但 Go `INGESTED` 到 job seed 依赖一次 hook。
- **准确表述**：**job runtime durable；ingest-to-job handoff 目前是 best-effort trigger。**

### A7. “状态机”

- **声称位置**：`services/job_lifecycle.py`、`docs/learning/05-durable-jobs.md`
- **缺口**：状态边主要靠调用约定，`update_job_status()` 明确不强制 transition，progress write 也不 fence。
- **准确表述**：**有 canonical documented transitions 和原子写，但不是数据库强制的完整 finite-state machine。**

### A8. “provider abstraction”

- **声称位置**：`inference/providers/vllm.py` — `generate_structured()` 等
- **缺口**：`generate_structured()` 没有任何调用方；router 主路径只使用 `generate()`。该方法还临时创建 OpenAI client，未体现统一 lifecycle/deadline。
- **准确表述**：**Ollama/vLLM 的基础 generate/fallback abstraction 已落地；structured provider path 是未接线能力。**

### A9. “E2E observability”

- **声称位置**：`README.md`、`docs/PROJECT_TECHNICAL_OVERVIEW.md`
- **缺口**：Python durable job 的 correlation 较强，但 Go ingest、Agent turn、OTEL trace context、业务 trace id 分属不同平面；JSON logger 还会丢未知 extra。
- **准确表述**：**job-centric observability 已落地；跨 Go/API/Celery/Agent 的单一端到端 trace 尚未闭环。**

## 6. README / Documentation Inconsistencies

### D1. fallback 为空时的 queue timeout 行为写错

- **文档**：`configs/livehouse.yaml` 注释、`docs/OLLAMA_INTEGRATION_NOTES.md`
- **代码**：`InferenceRouter.infer()`
- **不一致**：文档写“fallback 留空则排队超时直接报错”，代码在没有 fallback model 时继续调用 primary。
- **建议**：修代码或修文档，但必须选择一个明确 SLA policy。

### D2. worker scale 命令与固定 hostname 冲突

- **文档**：`deploy/docker-compose.yml` 顶部 `--scale celery-worker-general=2`
- **代码/配置**：同文件 `hostname: celery-general`，entrypoint 使用 `%h`，DB 对 worker name 唯一。
- **不一致**：命令可以启动容器，但 control-plane identity、capacity 和 inspect 语义不支持该扩容声明。

### D3. Go 默认 CLI 仍走被文档标为 legacy 的 mode A

- **文档**：`cmd/ingest/executor.go` 注释、`docs/GO_INGEST_NOTES.md`、技术概览把 SD brain + hook + jobs 作为推荐路径。
- **代码**：`cmd/ingest/main.go` 的默认 `pipeline-cmd` 仍是 `python run_pipeline.py ...`；只有提供 `--sd-mount` 才进入推荐路径。
- **不一致**：直接运行 ingest binary 的默认行为不是文档主架构。
- **建议**：文档明确“两种模式及默认值”，或让 production entrypoint 显式选择 main path；不要把 CLI 默认等同于推荐生产路径。

### D4. export 注释说 Gallery 使用 Celery，但 schema 默认同步

- **文档/注释**：`api/gallery_routes.py` — `ExportRequest.background` 上方注释
- **代码**：`background: bool = False`
- **不一致**：当前 Web UI 显式传 `background=true`，但 API contract 默认仍执行同步长任务。
- **建议**：注释改成“UI 主路径显式使用 Celery”；若要保证服务端语义则默认 true。

### D5. provider-neutral health 实际只实现 Ollama endpoint

- **文档**：技术概览将 Ollama/vLLM 都列为 provider runtime。
- **代码**：`infra/health.py:provider_health()` 固定访问 `/api/tags`。
- **不一致**：vLLM 配置下 healthz 会误报。

### D6. “旧 worker 不能覆盖新 worker 的结果”范围写得过宽

- **文档**：`docs/PROJECT_TECHNICAL_OVERVIEW.md` generation fence 段落
- **代码**：terminal DB update 会拒绝；文件 artifacts 在 fence 前写出。
- **建议**：改成“旧 worker 不能覆盖 job terminal state”，直到 artifact publish 也纳入 fencing。

### D7. docs 对 Go 测试缺口是诚实的，不应在面试中说成已完成

- **文档**：`docs/learning/13-testing-ci.md`、`docs/learning/15-interview-question-bank.md` 已明确没有 `*_test.go`。
- **代码验证**：`go test ./...` 全部为 `[no test files]`。
- **结论**：这不是文档不一致，而是容易口头表述过度的面试风险；应保留“已知缺口”的诚实说法。

### D8. FastAPI 文档引用不存在的拆分文件

- **文档**：`docs/learning/16-fastapi-project-guide.md` 的示例树包含 `api/infra/workers.py`
- **实际**：worker API 仍在 `api/infra_routes.py`。
- **建议**：明确标注 proposed structure，不能在面试中把它描述为当前模块边界。

### D9. `docs/RELIABILITY.md` 被脚本引用但文件不存在

- **位置**：`scripts/chaos_runtime.py`、`scripts/simulate_failures.py`
- **影响**：操作者按 CLI help 无法找到 scenario 说明；已有 `docs/learning/05-durable-jobs.md` 也没有被脚本指向。
- **建议**：改引用到现有文档，或按用户明确需求再补文档；不要保留悬空操作手册链接。

### D10. GO ingest token 文档已落后于代码

- **文档**：`docs/GO_INGEST_NOTES.md` 仍称 hook 不带 token
- **代码**：`triggerGalleryCheck()` 已从 `LIVEHOUSE_INGEST_TOKEN` 设置 `X-Luma-Token`。
- **建议**：文档更新为“token 已支持，但状态码检查、retry/outbox 尚未实现”。

## 7. Recommended Fixes

### P0 — 先修任务不变量

1. 修复 ingest runtime image：预编译并打包 preview-extractor，加入 Compose smoke test。
2. 修复 exhausted `QUEUED`：stuck requeue 或 reconciler 必须把 `attempt >= max_attempts` 归入 DL。
3. 所有 execution progress/terminal transition 使用 `(job_id, claim_generation, worker_id, expected_status)` CAS。
4. artifact 使用 generation-scoped temp output + fence check + atomic publish。
5. cancel 增加 generation，并在 pipeline/stage/inference queue 增加 cooperative cancellation check。

### P1 — 修复 durable handoff 与幂等

1. 所有启动拓扑必须部署 Beat；为 Go `INGESTED` photos 增加周期 reconciler 或 transactional outbox。
2. analyze 创建使用数据库级 idempotency/dedupe key，替代 check-then-insert。
3. preview extractor 改为 partial + validation + atomic rename，并补 Mode B stable window。
4. retry 增加 `next_attempt_at`、exponential backoff 和 jitter。
5. 修复 curation duplicate claim；明确 `model_runs` authority/粒度。

### P1 — 收紧部署资源模型

1. 移除 Compose 固定 worker hostname，拆分 pool 与 instance identity。
2. 默认明确 Celery concurrency；把 YAML inference capacity 与 worker admission 配置自动绑定。
3. 在仍共享单个 Ollama/GPU 时只运行一个 inference scheduler；扩展后迁移为独立 inference service。
4. 为 Celery task 设置按类型 time limits，并明确 worker-lost ack policy。

### P2 — 修复 observability 契约

1. API producer 启用 Celery OTEL instrumentation；worker child 中初始化/flush exporter。
2. 增加跨真实 broker 的 trace integration test。
3. 拆分 live/ready/dependency health，按 provider 实现 probe并返回正确 HTTP code。
4. 调整 VLM histogram buckets；重命名 busy-ratio 指标，避免冒充 GPU utilization。
5. existing job 响应返回 canonical job trace，并单列 request trace。
6. 修复 JSON logging extra schema；统一 `inference_batch_size` 配置键。

### P2 — 补测试而不是补更多抽象

1. 数据库 chaos：连续 hard crash 到 max attempts、cancel/requeue 与 stale writer、artifact publish race。
2. API concurrency：两个同时 analyze request 只能创建一个 job。
3. Go：partial preview、hook timeout 后 reconciliation、重复 scan、SQLite rollback、SIGTERM。
4. 部署：两个 Compose worker replica 必须产生不同 node/worker identity。
5. inference：跨两个 process 验证实际 provider concurrency，避免只测单例 semaphore。

## 8. Things That Are Already Correct

### FastAPI / Python

- `gallery_server.py` 和主要 routers 大量使用同步 `def`；FastAPI 会把这些 handler 放入 threadpool，因此 OpenCV/PIL/文件 I/O 没有直接运行在 event loop 上。项目没有为了“看起来异步”而把同步库包进假的 `async def`。
- health/metrics 等数据库连接普遍使用 `try/finally close()`；未发现明显的主路径 connection leak。
- `InferenceClient.predict_async()` 明确使用 `asyncio.to_thread()`，对同步 provider 的适配语义是诚实的；局限是 cancellation 不会杀线程，已在 M9 说明。
- OpenCV/NumPy/native 操作采用线程池是可以成立的：这些库的重计算常释放 GIL；项目也对 Stage 1/2 和 Stage 3 使用不同上限，而不是无界提交。

### Celery / Redis

- `worker_prefetch_multiplier=1` 对长 VLM job 是合理选择，能降低单 worker 预取大量长任务造成的不公平。
- `task_acks_late=True` 与数据库 claim 配合，方向正确；代码没有把 Celery result backend 当业务 SSOT。
- Celery envelope 主路径只带 `job_id`，参数从 SQLite payload 读取，避免大消息和重投时的陈旧 payload。
- broker publish 失败后 job row 已存在，周期 dispatcher 能恢复“DB commit 成功、send_task 失败”的窗口；这是一个有效的 reconciliation 设计。

### SQLite / Job Runtime

- `brain_connect()` 启用 foreign keys、WAL 和 busy timeout；Go 侧还限制 `SetMaxOpenConns(1)`。这些选择符合单机 SQLite 控制面。
- claim 在 `BEGIN IMMEDIATE` 内完成，并用 status/attempt/dependency 条件更新；并发 duplicate envelope 不能 claim 同一 job row。
- job row 与 `job_events` 在同一事务写入，避免“状态更新成功但 event 丢失”的普通崩溃窗口。
- `claim_generation` 在 claim 和 stuck requeue 时增加，terminal write 同时检查 generation/worker；数据库终态 fencing 是实质实现，不是注释。
- worker heartbeat 使用独立连接，context manager 在退出时 stop/join；operator 的 PAUSED/DRAINING/ERROR 不会被普通 ONLINE heartbeat 覆盖。
- 有 stuck scan、manual retry、dead-letter 状态、dependency-aware stage jobs 和 focused chaos scenarios，远好于只依赖 Celery task state。

### Inference

- `BoundedSemaphore` 在 enqueue 前获取，限制的是 queued + running 总数；不会先创建无界线程/请求再在内部等待。
- priority queue、per-model lane、queue wait、provider timeout、fallback degraded 标记和 attempt ledger 已实际接线。
- fallback 成功被标为 `DEGRADED`，不会伪装成 primary success；provider hop 与 end-to-end latency 分开记录。
- circuit breaker 和 fallback semaphore 有线程安全保护；Ollama 配置默认并发为 1，也与本机实测资源约束一致。
- Ollama/vLLM provider 均设置 connect/read timeout，未发现无限网络读取。

### Go ingest

- `listARWWithPool()` 在创建 goroutine 之前获取 semaphore，真正限制存活 goroutine 数，不会瞬间 spawn 全部文件任务。
- JPEG organizer 使用固定 worker pool、每 worker 独立 buffer、atomic counters，channel 生命周期清楚。
- RAW archive copy 使用同目录 partial、`Sync()`、close、atomic rename，且数据库写失败会清理 destination；这是正确的文件发布基础。
- HTTP hook 正确关闭 response body，并设置 client timeout。
- SQLite Go 连接启用 WAL/busy timeout/foreign keys，数据库连接池限制为 1，避免本进程制造多 writer 争用。

### Observability / Security

- 日志字段已统一 `trace_id/job_id/session_id/worker_id/provider/model`，异常主路径保留 server-side traceback，不把原始内部错误直接返回客户端。
- Prometheus jobs/workers gauge 从 SQLite SSOT 在 scrape 前刷新，避免只展示 API 进程自己的内存状态。
- inference ledger、job events、pipeline JSONL trace 和 OTEL spans 是不同层次的信号，代码没有只靠一类日志承担所有审计。
- ops mutation 使用 dependency 鉴权；文件路径敏感 workflow 使用 resolved-path allowlist，未在本次审计中发现明显的路径穿越主路径。

## 验证记录

- `python -m pytest tests/test_reliability_chaos.py tests/test_observability.py tests/test_inference_queue_shutdown.py tests/test_job_executor_claim.py`：17 passed。
- `go test ./...`：编译通过；四个 Go package 均显示 `[no test files]`。
- 以上通过结果证明现有 happy path/部分 chaos invariants 可运行，但没有覆盖 H1–H8 的跨进程、故障窗口和文件副作用问题。
