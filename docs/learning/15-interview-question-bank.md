# 15｜LumaKernel 全模块面试题库

使用方法：

1. 先用 60–90 秒回答，不要直接背长答案；
2. 回答结构优先采用“问题 → 设计 → 证据 → 取舍 → 边界”；
3. 带 `进阶` 的问题应结合代码和实验；
4. 带 `系统设计` 的问题没有唯一答案，重点是澄清规模和约束；
5. 所有数字只能使用自己重新跑过的结果。

---

## A. 项目定位与整体架构

### A1｜请用一分钟介绍这个项目

回答要点：

- 真实问题：Livehouse 一场演出产生大量相似照片；
- 产品闭环：摄取、分析、审片、调色、导出；
- 技术主线：Go ingest → durable jobs → OpenCV → VLM → Gallery/Agent；
- 工程亮点：SQLite SSOT、有界推理、fallback、评估；
- 真实边界：单节点个人工作流，不是多租户云平台。

追问：这个项目最核心的创新是什么？

### A2｜为什么把它称为 job-centric AI runtime，而不是图片评分应用？

回答要点：

- 模型调用只是数据面的一环；
- Job 状态机负责恢复、重试、取消和审计；
- 推理层负责背压与 fallback；
- Infra 和 eval 让系统可运维、可验证。

### A3｜请画出完整数据流

至少包括：

```text
SD/Archive → Go ingest → sessions/photos → ingest hook
→ Celery run_job(job_id) → SQLite claim
→ Stage1 → Stage2 → Stage3/4
→ audit/artifact/model_runs
→ Gallery / Infra / Agent / Export
```

### A4｜控制面、数据面、推理面、产品面分别是什么？

- 控制面：jobs、workers、retry、dispatch；
- 数据面：图片和 Stage pipeline；
- 推理面：queue/router/provider/ledger；
- 产品面：Studio/Gallery/Infra/Eval/ChatDock。

### A5｜项目中有哪些“真相源”？

- 执行状态：SQLite `jobs/job_events`；
- 摄取账本：`sessions/photos`；
- 图片分析结果：audit/artifact；
- 消息状态：Redis/Celery，仅 transport；
- 模型尝试：`model_runs/model_run_attempts`。

### A6｜生产主路径和兼容路径有什么区别？

- 主路径：Brain + hook + durable Job；
- `run_pipeline.py`：调试/兼容，绕过 Job timeline；
- Go Mode A：直接子进程，恢复与可观测较弱；
- Showcase：fixture 演示，不是实时生产。

### A7｜如果只能保留三个工程亮点，你选择什么？

推荐：

1. 数据库 SSOT + fencing 的 durable jobs；
2. bounded inference + fallback ledger；
3. 固定评估集 + Agent groundedness。

根据目标岗位调整。

### A8｜这个项目最大的技术债是什么？

可选角度：

- SQLite/本地 volume 单节点；
- 双 ingest/双 Agent legacy 路径；
- Gallery/API 大文件；
- 前端与 Go 测试不足；
- Agent 无 checkpointer、非真流式；
- 多层 retry 预算分散。

回答时说明优先级依据，而不是罗列。

---

## B. Python 与工程基础

### B1｜`TypedDict`、Pydantic 和普通 `dict` 如何选择？

- `dict`：灵活边界数据；
- `TypedDict`：静态类型提示，不做运行时验证；
- Pydantic：外部输入、模型输出和稳定契约；
- 核心 schema 应避免无限传播 `Any`。

### B2｜为什么 Stage1/2 使用线程池，而不是 multiprocessing？

- OpenCV/NumPy C 代码可能释放 GIL；
- 同时包含文件 I/O；
- 线程共享已解码数据成本低；
- multiprocessing 有序列化和进程内存开销；
- 最终选择应由基准决定。

### B3｜GIL 会让线程池完全无效吗？

不会。纯 Python CPU loop 受限制，但 I/O 和释放 GIL 的 C 扩展仍可并发。

### B4｜为什么 JSONL 适合 checkpoint？

- 可逐行追加；
- 崩溃保留已完成记录；
- 可流式读取；
- 单行损坏局部化；
- 需要定义重复 key 的合并语义。

### B5｜NumPy 为什么经常无法直接 JSON 序列化？

NumPy scalar/ndarray 不是标准 JSON 类型；需要转换为 Python `int/float/list`，并处理 NaN/Infinity。

### B6｜如何设计配置优先级？

推荐：

```text
代码默认 < YAML < CLI/runtime override < secret/env
```

阈值与策略适合版本化 YAML；secret 只放环境变量。

### B7｜什么时候应该延迟 import？

- 避免可选重依赖影响基础 import；
- 打破合理但暂时存在的循环依赖；
- 仅在真正使用功能时加载；
- 不应被用来掩盖混乱模块边界。

### B8｜如何保证文件写入的原子性？

- 同目录临时文件；
- write + flush/fsync；
- atomic rename；
- artifact 内带 version/hash；
- 数据库与文件跨系统时需要补偿或 outbox。

---

## C. OpenCV 与 Stage1/2

### C1｜Laplacian variance 为什么能检测模糊？

清晰图含更多高频边缘，二阶导响应变化更大。局限：噪声、尺寸、低纹理、烟雾和有意拖影都会影响结果。

### C2｜Sobel 与 Laplacian 的区别是什么？

- Sobel：一阶方向梯度；
- Laplacian：二阶、无方向高频响应；
- Sobel 方向比可提示运动方向；
- 两者都不是语义清晰度。

### C3｜为什么项目不能把运动模糊全部硬拒绝？

Livehouse 中拖影可能表达速度和氛围；应结合结构、主体可读性和 VLM 语义。

### C4｜为什么曝光分析使用分位数而不只用均值？

舞台射灯和大面积暗背景会严重影响均值；p1/p50/p99 更能描述分布和动态范围。

### C5｜欠曝 salvage 的原理是什么？

对暗但有结构的图片生成临时 EV 提升预览，重新计算边缘/对比度并返还部分技术分。它只改变门控证据，不替换原始 VLM 输入。

### C6｜Stage1 为什么偏软门控？

早期误杀不可恢复。只硬拒绝确定性废片，把主观/困难样本作为 ambiguous 交给后续模型。

### C7｜Stage2 快速美学分是不是 AI 模型？

不是。它是颜色、对比度、构图代理等启发式低成本评分，用于候选排序和压缩。

### C8｜dHash 与 pHash 有什么区别？

- dHash：相邻像素差，快；
- pHash：低频 DCT 结构，对轻微缩放/压缩更稳；
- 二者用 Hamming distance；
- 都不理解表情和语义。

### C9｜为什么 Stage3 cache 的 pHash 阈值使用 0？

连拍中轻微差异可能代表不同表情/瞬间。Route B 选择 fail-closed，只对完全一致哈希复用语义结果。

### C10｜如何评估 Stage1/2？

重点：

- keeper recall/coverage；
- reject precision；
- Stage3 admission rate；
- 分错误类型；
- VLM 成本节省；
- 不只看整体 accuracy。

### C11｜外层线程池为什么要限制 OpenCV 内部线程？

嵌套并行会造成线程过量、上下文切换和内存压力，吞吐反而下降。

### C12｜如果换了相机或预览尺寸，阈值还能用吗？

不一定。Laplacian、噪声、曝光和压缩分布都会漂移，需要分设备/尺寸评估或归一化。

---

## D. VLM、Prompt、Schema 与 Stage3/4

### D1｜VLM 与文本 LLM 有什么差别？

VLM 增加 vision encoder/projector/视觉 token；图像分辨率影响上下文和延迟，且模型需要同时理解视觉与语言。

### D2｜为什么采用 fast-first？

短 Prompt 先覆盖全部候选，完整八维分析只给高价值或不确定图片，降低 token 和延迟。风险是 FAST routing 误杀。

### D3｜FAST 和 FULL 输出分别承担什么职责？

- FAST：overall、verdict、tags、语义观察；
- FULL：八维评分、强弱项和更深解释；
- FULL 受规则和 Top-K 预算限制。

### D4｜为什么要求 JSON 仍然需要 Parser？

生成模型仍可能输出 code fence、解释、截断、缺字段和错误类型。JSON mode 不等于业务 schema 合法。

### D5｜Pydantic 与手工/regex fallback 为什么共存？

Pydantic 提供严格主路径；手工与 regex 兼容历史模型和截断输出。必须记录 fallback，防止默认值伪装成成功。

### D6｜Schema 如何安全演进？

- 新字段先 optional；
- consumer-first；
- 保存 schema/prompt version；
- 兼容旧顶层/嵌套结构；
- 删除或改尺度必须显式迁移。

### D7｜为什么 Prompt 要版本化？

Prompt 是模型行为代码。版本化后才能复现实验、关联 artifact、比较 A/B 和回滚。

### D8｜为什么示例最好使用占位符而不是具体分数？

具体分数会产生锚定和复制；占位符展示结构而不污染输出分布。

### D9｜Semantic observation 与 semantic gate 有什么区别？

模型输出观察证据；确定性 policy 根据 severity/confidence 决定 off/review/hard。当前生产 gate 为 off。

### D10｜Route B 是什么？

本地 Stage1/2 压缩候选，Stage3 使用 OpenAI-compatible 云端 VLM；开启时强制走 `inference/` 层。

### D11｜为什么保存 raw dimensions 和 calibrated dimensions？

便于审计后处理影响、模型漂移分析和重新计算，不让规则修改覆盖原始模型证据。

### D12｜Stage4 为什么不并入 Stage3 Prompt？

- 避免编辑建议干扰审美评分；
- 减少 Stage3 token/schema 复杂度；
- 只对需要的 FULL 图片调用；
- 数值 recipe 可单独验证范围。

### D13｜如何评估结构化输出系统？

- JSON 合法率；
- schema 合法率；
- 缺字段/越界；
- parse retry；
- fallback defaults；
- 最终任务质量；
- 静默错误率。

### D14｜如何防图片中的 Prompt Injection？

Prompt 明确图片文字是数据；限制输出 schema；模型不直接决定高风险副作用；后续 policy 与引用校验。

---

## E. 推理 Queue、Router、Provider 与 Ledger

### E1｜为什么需要独立 InferenceQueue？

统一管理准入、并发、优先级、背压、等待超时、fallback 和指标，防止业务模块各自无限请求模型。

### E2｜Concurrency、parallelism、throughput 有什么区别？

- concurrency：多个任务进行中；
- parallelism：同时实际执行；
- throughput：单位时间完成量；
- Ollama 多 HTTP 请求不代表并行 decode。

### E3｜BoundedSemaphore 和 Queue size 有什么区别？

队列通常只限制等待项；Semaphore 可限制等待 + 执行中的总准入数。本项目槽位在任务完成后释放。

### E4｜Admission timeout、queue timeout、read timeout 有何区别？

- admission：进不了系统；
- queue：已准入但没开始；
- connect/read：provider 网络/生成；
- hard deadline：整次业务预算。

### E5｜为什么无界并发会降低吞吐？

服务率有限；只会增加排队、内存、尾延迟和重试，甚至 OOM。

### E6｜Queue fallback 与 error fallback 有何区别？

- Queue fallback：主模型未必故障，为等待时间主动降级；
- Error fallback：主模型已调用失败；
- ledger 应区分容量和可靠性问题。

### E7｜为什么 fallback 成功标记为 degraded？

结果可用，但模型、质量、成本或时延承诺已改变。

### E8｜为什么要记录每个 attempt？

最终成功可能掩盖 primary timeout。Attempt 才能分析故障率、fallback、成本和真实 provider hops。

### E9｜需要拆分哪些延迟？

- admission；
- queue wait；
- provider hops；
- parse/postprocess；
- end-to-end；
- p50/p95/p99。

### E10｜Ollama 与 vLLM 的批处理差别？

- Ollama 视觉模型常实际单并行；
- vLLM 服务端 continuous batching；
- 客户端不应为 vLLM 盲目等待固定拼批。

### E11｜Round Robin 的缺陷是什么？

不感知健康、延迟和错误率；坏 endpoint 仍会收到请求。可升级熔断、EWMA、健康权重。

### E12｜多层重试如何造成调用放大？

Provider retry × Stage retry + fallback + parse retry。需要总 deadline/attempt budget 和 ledger。

### E13｜Ledger 写失败时是否应丢弃成功推理？

当前选择业务结果优先并标记审计失败。这是 best-effort ledger，不是严格 exactly-once。

### E14｜如何做容量规划？

固定模型/图片/token，测不同并发下 throughput、p95、queue wait、内存和错误率，找稳定拐点。

---

## F. Durable Jobs、Celery、Redis 与 SQLite

### F1｜为什么 Celery 状态不是业务 SSOT？

消息可能重复/丢失，AsyncResult 不包含完整业务事务、artifact、人工 retry/cancel 和审计语义。

### F2｜为什么 Celery 消息只传 `job_id`？

Worker 执行前读取最新数据库状态和 payload，避免消息陈旧、过大，并支持重新调度。

### F3｜`acks_late` 是否实现 exactly-once？

没有。它提高崩溃后重投能力，形成 at-least-once；业务仍需幂等 claim。

### F4｜如何实现原子 claim？

事务内条件更新 runnable 状态并检查 affected rows；只有一个 worker 成功。

### F5｜`attempt` 和 `claim_generation` 有什么区别？

- attempt：成功 claim/业务尝试次数；
- generation：当前所有权世代；
- generation 用于阻止 zombie writer。

### F6｜什么是 fencing token？

每次重新授权递增 token；写操作必须携带当前 token，旧 owner 即使恢复也不能写。

### F7｜为什么 stuck requeue 不一定增加 attempt？

它可能只是释放幽灵 claim，尚未形成新的有效执行；attempt 应在下一次成功 claim 时增加。

### F8｜Retryable、Permanent、Dead Letter 如何区分？

- 临时网络/provider：retryable；
- 无效输入/不支持格式：permanent；
- 重试耗尽：dead letter；
- 分类错误会导致浪费或丢任务。

### F9｜PAUSED 和 DRAINING 有什么区别？

- PAUSED：不接新任务；
- DRAINING：不接新任务但允许当前任务完成，用于维护/下线。

### F10｜SQLite WAL 解决了什么，没解决什么？

提高读写并发，但仍是单写者、单机文件，不支持多节点多写。

### F11｜写 Job 成功、发 Celery 失败怎么办？

当前依赖周期 dispatcher 重新发现。更完整方案是 transactional outbox。

### F12｜Redis 全丢后哪些任务能恢复？

主分析 Job 可从 SQLite runnable 状态重新 dispatch；只依赖 Celery result 的后台导出状态可能无法恢复。

### F13｜为什么不能宣称所有异步任务都 durable？

主分析进入统一 `jobs`；后台 export 仍主要使用 Celery task/result，耐久级别不同。

### F14｜何时迁移 Postgres？

多节点多写、SQLite lock 成为瓶颈、需要 HA/在线迁移/更复杂查询时。迁移需重写 claim 事务和 fencing。

---

## G. FastAPI、Session、Artifact 与安全

### G1｜为什么分析 API 返回 job_id，而不是等待完成？

VLM batch 很长，HTTP 连接不适合承载恢复、取消和重试；用 202/job status 更可靠。

### G2｜`async def` 会自动解决 CPU 阻塞吗？

不会。同步 OpenCV/requests 仍会阻塞 event loop，应交给 worker 或线程池。

### G3｜四类 API 表面是什么？

Gallery、Studio、Infra、Agent；分别服务用户审片、场次控制、运维和对话。

### G4｜Active Session 如何解析？

环境变量 override → runtime `latest_session.json` → server base dir。它是全局本地指针，不是用户 session。

### G5｜如何防路径穿越？

- resolve/normalize；
- 限制允许 root；
- 拒绝 `..` 和外部绝对路径；
- 检查 symlink 后真实路径；
- 不信任客户端 basename。

### G6｜Preview 和 RAW 如何配对？

约定 sibling `Previews/` 与 `RAW/`，通过 basename/映射寻找；需处理扩展名、大小写、缺失和重复。

### G7｜如何演进 `analysis_results.json`？

稳定 join key、版本化、optional 新字段、consumer-first、fixture/TypeScript/eval 同步。

### G8｜CORS 是鉴权吗？

不是。它只限制浏览器跨域，curl/服务端不受保护。

### G9｜当前 Agent 的用户隔离可靠吗？

`anon:<session_id>` 适合本地个人产品，不是正式认证或多租户硬隔离。

### G10｜同步导出和后台导出如何选择？

小任务可同步；长 RAW/调色应后台。后台应固定提交时 base_dir，并最终迁入 durable jobs。

### G11｜202 Accepted 代表什么？

只代表请求被接收，不代表成功；客户端必须查询状态。

### G12｜如何避免异常信息泄露？

服务端记录完整 traceback，客户端返回稳定通用错误和有限 error type，不返回路径、token、堆栈。

---

## H. LangGraph Agent、Skills 与 Groundedness

### H1｜请画出 Agent Graph

```text
START → plan
  ├→ execute_plan → answer → END
  └→ decide → act → decide ... → answer → END
```

### H2｜为什么有 LangGraph 还需要 intent router？

LangGraph 负责流程，不保证模型决策稳定；高频可枚举意图用规则降低成本、延迟和轨迹方差。

### H3｜`plan` 和 `decide` 有什么区别？

- plan：确定性规则和结构化 selection goal；
- decide：模型对开放语义选择工具或回答。

### H4｜如何防 Agent 无限循环？

- max rounds；
- seen call keys；
- specialist allowlist；
- forced answer；
- timeout/budget；
- 重复工具检测。

### H5｜为什么需要统一 tool protocol？

屏蔽 Ollama 文本 JSON 和 OpenAI native tool calling 差异，复用同一 act、权限和测试。

### H6｜为什么只并行只读工具？

写工具存在顺序和副作用；并行 select/export/memory update 可能产生竞争和不可预测结果。

### H7｜Specialist 有什么价值？

缩小工具 action space，同时在 prompt 和 act 层硬限制 capability。

### H8｜有哪些 Memory？

- turn context；
- conversation messages；
- working memory；
- durable preference；
- experience RAG；
- 生命周期和信任程度不同。

### H9｜SQLite message store 是否等于 LangGraph checkpointer？

不是。当前图无 checkpointer；可恢复历史消息，不能从中间 node 恢复。

### H10｜Groundedness 如何防止虚构文件？

Tool metadata 建立允许文件集合，模型输出 `{ref_N}`，finalize 解析、删除越界/未知引用并回退模板。

### H11｜Guardrails 与 Groundedness 有什么区别？

- Guardrails：安全、注入、泄密、内容策略；
- Groundedness：事实/文件是否来自工具证据。

### H12｜为什么 SSE 不是真 token streaming？

系统先生成完整回答，完成引用和安全校验后再分块发送，只是打字效果。

### H13｜真正 token streaming 会遇到什么问题？

未校验内容可能提前发送；需要增量引用、安全策略、tool event、abort 和 partial state。

### H14｜为什么默认 guardrail observe 有风险？

检测到不一定阻断。适合收集数据，但高风险公开部署需要 soft/hard 和正式安全策略。

### H15｜如何评估一个 Skill？

输入 schema、工具选择、参数、结构化 metadata、副作用、错误、权限、task success 和 latency。

### H16｜如果新增一个工具，需要改哪些地方？

Skill 实现/registry、schema/protocol、specialist allowlist、prompt/native tools、tests、eval fixtures、UI/trace。

---

## I. RAG、BM25 与 CLIP

### I1｜本项目哪些检索使用 BM25？

Knowledge Search 明确使用 BM25；Gallery/Archive 主要是规则词法命中 + CLIP + 业务重排，不能全部称为 BM25。

### I2｜BM25 中 `k1` 和 `b` 是什么？

- `k1`：词频饱和；
- `b`：文档长度归一化；
- BM25 擅长精确词和稀有术语。

### I3｜CLIP 为什么能做 text-to-image？

对比学习将文本和图像映射到共享向量空间，使用 cosine 相似度检索。

### I4｜CLIP 不适合什么查询？

精确 EXIF、文件名、政策条款、细小技术缺陷。慢门等应使用结构化 metadata。

### I5｜如何融合 lexical 与 vector 分数？

需要归一化、候选合并、权重或 RRF；固定线性权重应在 dev 调参并冻结 test。

### I6｜为什么需要 rerank？

第一阶段追求召回；第二阶段结合质量、业务目标、偏好和去重提高 Top-K。

### I7｜为什么 ACL 必须在召回前过滤？

避免不可访问内容进入分数、缓存、日志和候选 metadata。

### I8｜什么时候需要向量数据库？

全量扫描的内存/延迟/并发不可接受时。小型本地索引保持简单更易维护。

### I9｜如何处理 embedding model 变更？

索引 identity 包含 model/version；重建向量；不要混合不同空间的向量。

### I10｜如何评估 Retrieval？

固定 query/relevance/hard negatives，报告 Recall@K、MRR、nDCG、latency、coverage 和 ACL correctness。

### I11｜什么是 hard negative？

语义很相似但不满足真实目标的负例，例如同一人物但动作错误、同场景但技术不可交付。

### I12｜为什么检索相关性不等于最终选片质量？

相关图可能重复、低质或不适合平台；最终 ranking 还结合 Stage3、diversity 和用户偏好。

---

## J. AI 评估与数据

### J1｜为什么不能只看 Accuracy？

类别不平衡、排序业务、不同错误成本都会让 Accuracy 失真。

### J2｜MAE 与 Spearman 分别衡量什么？

- MAE：绝对分数误差；
- Spearman：排序单调一致性；
- 摄影选片通常更重视排序，但仍需校准。

### J3｜Precision@K 和 Recall@K 的区别？

- P@K：Top-K 有多纯；
- Recall@K：人工 keeper 有多少进入 Top-K；
- 门控优先 keeper recall。

### J4｜为什么需要 nDCG？

支持分级 relevance，并对高位结果给予更大权重。

### J5｜如何建立 Golden Dataset？

稳定 content hash、label schema、标注规范、reviewer、版本、冲突处理、dev/test split 和 hard cases。

### J6｜如何降低标注锚定偏差？

盲标、不预填模型分数、随机顺序、独立复标、记录不确定性。

### J7｜Cohen's κ 解决什么问题？

扣除随机一致性，尤其适合类别不平衡时解释标注者 agreement。

### J8｜为什么测试集不能反复调参？

会将 test 变成训练集，指标乐观；使用 dev/canary 调参，holdout 最终评估。

### J9｜Agent 应评什么？

Outcome、route、tool/args、trajectory、groundedness、memory、副作用、budget、latency 和 cost。

### J10｜LLM-as-a-Judge 有什么风险？

偏好、位置、长度、模型同源偏差和不稳定；应结合硬合约与人工校准。

### J11｜CI 采集指标是否等于发布门禁？

不是。检查是否 blocking、阈值、失败传播、skip 和 live/fixture。

### J12｜当前 Agent release gate 的边界？

统一 gate 偏 smoke，可 skip；严格真实模型门槛需显式 `--live --strict`。

### J13｜如何联合评估质量和成本？

报告 keeper coverage/P@K 与 VLM calls/token/latency，寻找 Pareto frontier。

### J14｜为什么要区分 Recorded、Simulated 和 Fixture？

它们证明的东西不同；模拟故障/展示数据不能冒充生产 SLO。

### J15｜如何分析 Bad Case？

建立 taxonomy、按场次/类型分层、统计频率和影响，修复后提升为固定 fixture。

---

## K. Next.js、React 与产品工程

### K1｜Route Handler 和 rewrite 有何区别？

Route Handler 可校验、转换、fixture 和权限；rewrite 主要透明代理。

### K2｜为什么使用 Next BFF？

同源访问、隐藏内部 FastAPI origin、统一错误/数据源、服务端 token 和展示模式。

### K3｜`NEXT_PUBLIC_*` 有什么风险？

会进入浏览器 bundle，不能放 secret 或内部敏感配置。

### K4｜Server Component 与 Client Component 如何选择？

数据获取和无交互内容优先 Server；state/effect/browser API 使用 Client；减少客户端 bundle。

### K5｜如何防轮询竞态？

AbortController、请求序号、终态停止、卸载取消、页面隐藏降频，避免旧响应覆盖新状态。

### K6｜大 Gallery 如何优化？

分页、缩略图、lazy loading、尺寸预估、虚拟化、缓存、避免 O(n²) request-time clustering。

### K7｜如何避免 fixture 与 live schema 漂移？

共享 TypeScript schema/生成 OpenAPI type、contract tests、fixture validation、数据来源标记。

### K8｜为什么 `next build` 不能代替前端测试？

只证明可编译构建，不验证点击、轮询、错误、键盘、状态竞争和真实浏览器。

### K9｜当前 SSE UI 有什么限制？

后端完成后才分块，首 token 延迟未降低；断开也不一定取消 provider。

### K10｜为什么瀑布流需要稳定 key 和图片尺寸？

避免 React 错误复用和 layout shift，提高滚动与选择状态稳定性。

---

## L. Go Ingest、EXIF 与文件一致性

### L1｜为什么用 Go 做 ingest、Python 做 AI？

Go 适合长期文件扫描、并发 I/O 和单二进制；Python 拥有 CV/AI 生态。通过 SQLite/hook 解耦。

### L2｜如何防重复插卡重复导入？

文件 identity/fingerprint、DB unique/upsert、目标检查、幂等 hook 和可重放 Job。

### L3｜为什么需要文件稳定窗口？

避免处理仍在写入的 RAW，导致截断复制或 preview。

### L4｜`.partial + fsync + rename` 解决什么？

防止中断时把半文件暴露为成品；同文件系统 rename 原子。

### L5｜mtime/size 指纹有什么缺陷？

不是内容 hash，同名同大小同时间可能碰撞；跨文件系统时间精度和复制会变化。

### L6｜为什么先提取 Preview？

RAW 大且解码慢；嵌入 JPEG 足以供 Gallery、OpenCV 和 VLM 初筛，最终导出再处理 RAW。

### L7｜EXIF 有哪些坑？

缺时区、相机时间错误、orientation、厂商差异、DateTimeOriginal 缺失、批量输出行错位。

### L8｜goroutine semaphore 与固定 worker pool 如何选择？

大量任务优先固定 worker pool；信号量简单但应在创建 goroutine 前获取，防瞬间创建过多 goroutine。

### L9｜Hook 失败是否会丢任务？

Brain 中照片记录仍在，可由后续扫描/Beat 重新 seed。当前 Hook 应补 token、非 2xx 检查和有限重试。

### L10｜为什么 Legacy Mode A 不应扩展？

直接运行 pipeline 子进程，绕过统一 jobs、事件、恢复和控制面。

### L11｜文件和数据库如何保持一致？

无法单事务覆盖两者，采用操作顺序、原子 rename、DB transaction 和失败补偿；更复杂场景使用 saga/outbox。

### L12｜如何测试 Go ingest？

临时目录/SQLite、表驱动、`httptest.Server`、race detector、fuzz malformed RAW、故障注入。

---

## M. 可观测性、部署与安全

### M1｜Logs、Metrics、Traces、Events 有何区别？

- Log：离散上下文；
- Metric：聚合趋势；
- Trace：请求因果链；
- Job event/model attempt：业务审计。

### M2｜Golden Signals 是什么？

Latency、Traffic、Errors、Saturation；映射到 Job、queue、provider 和 worker。

### M3｜什么是 metrics authority？

说明指标真相来源：SQLite、Redis best-effort、进程内 queue 或外部 provider，避免错误聚合。

### M4｜Liveness、Readiness、Dependency Health 如何区分？

- liveness：进程是否应重启；
- readiness：是否接新流量；
- dependency：系统是否 degraded；
- 不应因外部模型短故障无限重启 API。

### M5｜Compose 多 worker 为什么不是分布式平台？

仍是单主机、SQLite、本地 volume、单 Redis 和进程内配额，没有跨节点控制面/HA。

### M6｜Redis 当前有什么耐久性风险？

Compose 未配置专用持久 volume/AOF，容器重建可能丢 broker/result。

### M7｜SQLite 迁 Postgres 最难的是什么？

保持 claim、fencing、retry 和状态转换语义；可采用 `FOR UPDATE SKIP LOCKED`，但需重新验证事务。

### M8｜本地 artifact 迁对象存储需要改什么？

路径变 URI/object key、上传原子性、content hash、权限、生命周期、缓存、下载 URL 和数据库元数据。

### M9｜Namespace 为什么不是多租户隔离？

只是查询/调度标签，没有身份、授权、存储隔离和资源硬配额。

### M10｜主要安全风险是什么？

路径穿越、未鉴权 ops、prompt injection、跨 session 泄露、SSRF、CORS 误解、云端图片隐私、secret 泄漏。

### M11｜OTel bootstrap 是否等于完整可观测平台？

不是。还需 Collector、存储、可视化、alert、全链路 propagation 和采样策略。

### M12｜平台化应该先上 Kubernetes 吗？

通常先明确负载/SLO，解决数据库、artifact、队列一致性和 auth；Kubernetes 不能自动修复状态架构。

---

## N. 测试、CI 与可靠性

### N1｜AI Eval 与普通单元测试的区别？

单元测试验证确定性代码；Eval 衡量概率模型质量，需要数据集、指标、baseline 和统计。

### N2｜什么时候使用 Mock，什么时候必须真实模型？

协议、状态、fallback 用 mock；输出遵循、质量、吞吐和 GPU 必须真实模型。

### N3｜如何测试 at-least-once 消费者？

重复投递、并发 claim、崩溃重放、重复副作用和 fence。

### N4｜为什么缺 LangGraph 要让 CI 失败而不是 skip？

它是默认生产 runtime。Skip 会出现测试绿但生产路径未执行。

### N5｜如何降低异步测试 flaky？

事件/条件等待、fake clock、可注入 scheduler、独立 DB、禁止固定 sleep、保存失败 trace。

### N6｜当前 Web CI 证明了什么？

TypeScript 和 production build；没有证明真实交互、浏览器行为和 E2E。

### N7｜当前 Go CI 缺什么？

已有少量 `*_test.go`，CI 运行 `go test -race ./...`、vet 和 build；仍缺
Hook retry、SQLite rollback、拔卡/磁盘满与 fuzz 覆盖。

### N8｜Release Gate 应如何分层？

- PR：快的 deterministic；
- nightly：较大 eval/集成；
- release：真实模型、关键 E2E、性能安全；
- production：shadow/review。

### N9｜什么是契约测试？

验证 producer/consumer 对 schema、字段、错误和版本的一致理解，不一定启动完整系统。

### N10｜如果只能补三类测试？

推荐：

1. Studio→Job→Gallery E2E；
2. Go ingest 幂等/崩溃；
3. Agent grounded tool trajectory。

需要结合风险解释。

---

## O. 系统设计开放题

### O1｜如果每天处理 100 万张图片，如何扩展？

先澄清：

- 图片大小、延迟目标、模型、GPU、租户和成本。

讨论：

- 对象存储；
- Postgres/分片 metadata；
- 独立 stage queue；
- 全局 GPU scheduler；
- batch/continuous batching；
- checkpoint；
- autoscaling；
- 数据局部性；
- SLO/成本。

### O2｜如何改造成多租户 SaaS？

- OIDC/RBAC；
- tenant id 进入所有主键/查询；
- 对象存储 prefix/policy；
- Postgres RLS；
- per-tenant quota/fair queue；
- secret/billing/audit；
- 删除/导出合规。

### O3｜如何实现真正的 Agent streaming？

- provider token stream；
- graph/tool event protocol；
- provisional buffer；
- citation incremental mapping；
- safety moderation；
- abort/cancel；
- reconnect/checkpoint；
- UI event reducer。

### O4｜如何统一所有后台任务的耐久性？

- export/curation 等都进入 jobs；
- typed payload；
- transactional outbox；
- idempotent executor；
- artifact registry；
- progress events；
- retry/DLQ；
- migration 旧 Celery task id。

### O5｜如何实现全局 GPU 公平调度？

- 独立 scheduler；
- worker heartbeat/capability；
- tenant weighted fair queue；
- model memory/affinity；
- admission token；
- preemption 边界；
- quotas；
- centralized metrics。

### O6｜如何升级 Router？

- health probe；
- circuit breaker；
- EWMA latency/error；
- weighted routing；
- retry budget；
- model capability/schema compatibility；
- shadow/canary。

### O7｜如何降低 VLM 成本而不降低 keeper recall？

- 改善 Stage1/2；
- uncertain/ambiguous reserve；
- dynamic top-k；
- FAST/FULL cascade；
- exact cache；
- smaller model；
- prompt token；
- eval Pareto；
- active learning。

### O8｜如何从用户反馈持续优化？

- 明确 feedback schema；
- 选择/取消作为弱信号；
- blind review；
- hard-case queue；
- taste profile；
- offline replay；
- guard against feedback loop；
- A/B 与回滚。

### O9｜如何保证图片隐私？

- 默认本地；
- 云推理显式同意；
- 缩略图/最小上传；
- encryption；
- retention；
- access audit；
- tenant isolation；
- signed URL；
- data deletion。

### O10｜如何设计灾难恢复？

- 定义 RPO/RTO；
- Postgres/SQLite backup；
- artifact checksum/replication；
- Redis 非真相；
- restore drill；
- rebuild index；
- replay runnable jobs；
- secret/config version。

---

## P. 行为面试与项目复盘

### P1｜讲一次你发现设计与文档不一致的经历

可讲：

- Stage1 “strict” 文案与软门控实现；
- inference “optional” 与 Route B 强制路径；
- SSE token 与真实 streaming；
- release gate 名称与实际阻断范围。

重点：如何用代码/实验验证并修正文档，而不是指责。

### P2｜讲一次质量与成本冲突

使用两阶段门控：说明人工 keeper、调用预算、实验、结果和误杀边界。

### P3｜讲一次可靠性问题

重复投递或 zombie writer：说明 at-least-once、claim、fencing 和 chaos 验证。

### P4｜讲一次你选择不做某项技术的经历

例如暂不迁 Kubernetes/Postgres：当前规模不足以抵消复杂度，先解决真实瓶颈。

### P5｜如果重新做这个项目，你会怎么改？

推荐优先：

1. 收敛 legacy 路径；
2. outbox + durable export；
3. 前端/Go E2E；
4. 统一 retry budget；
5. Agent checkpoint/真 streaming 是否由需求决定。

---

## 模拟面试评分表

每题 0–2 分：

| 维度 | 0 分 | 1 分 | 2 分 |
|---|---|---|---|
| 原理 | 只背名词 | 能解释概念 | 能结合机制和失败模式 |
| 项目 | 找不到代码 | 能指出模块 | 能追踪完整调用链 |
| 证据 | 无数据 | 有示例 | 有可复现实验/指标 |
| 取舍 | 只说优点 | 能说缺点 | 能说明适用条件与替代方案 |
| 边界 | 夸大能力 | 知道部分限制 | 能准确区分当前与演进 |

单题 8 分以上代表可以用于正式面试；6–7 分需要补实验；5 分以下应回到对应学习章节。

