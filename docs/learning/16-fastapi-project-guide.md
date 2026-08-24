# 16｜FastAPI 项目实战：结合 LumaKernel 的完整学习指南

本章不是通用 FastAPI API 罗列，而是从本项目的真实代码出发，说明：

- FastAPI 在系统中承担什么职责；
- 路由、参数、Schema、异常、鉴权和文件响应如何实现；
- 为什么长任务交给 Celery；
- Gallery、Studio、Infra、Agent 四类 API 的设计差异；
- 当前做法中哪些值得学习，哪些仍可改进；
- 面试时应该如何讲。

---

## 1. 学习目标

完成本章后，你应该能够：

- 从 `gallery_server.py` 解释 FastAPI 应用的启动和路由装配；
- 使用 `APIRouter` 拆分业务域；
- 区分 path、query、header 和 body 参数；
- 使用 Pydantic 做请求和响应校验；
- 正确返回 JSON、文件、HTML、Prometheus 和 SSE；
- 设计长任务的 `Job + 202 Accepted + status query` 模式；
- 使用 `Depends` 实现鉴权；
- 解释 CORS、路径安全、SSRF 和错误脱敏；
- 指出本项目 FastAPI 层的工程边界与优化方向。

---

## 2. FastAPI 在本项目中的位置

整体调用关系：

```text
Browser / Go ingest / Ops client
            │
            ▼
Next.js BFF / direct HTTP
            │
            ▼
FastAPI: gallery_server.py
  ├─ Gallery API
  ├─ Studio API
  ├─ Infra API
  └─ Agent API
            │
            ├─ SQLite Brain / Agent Store
            ├─ File artifacts / Preview / RAW
            ├─ Celery + Redis
            └─ LangGraph Agent
```

FastAPI 是 HTTP 控制面和产品数据入口，不应该直接承担长时间 OpenCV/VLM 批处理。

它主要负责：

1. 校验请求；
2. 解析活动 session；
3. 创建 Job；
4. 发送 Celery 唤醒消息；
5. 查询 SQLite/artifact；
6. 返回 Gallery、Infra 和 Agent 数据；
7. 执行鉴权和路径约束。

---

## 3. 应用启动：`gallery_server.py`

关键代码：

- `gallery_server.py:25-80`：解析 base directory、配置和端口；
- `gallery_server.py:83-85`：设置 Gallery base directory；
- `gallery_server.py:87-92`：可选 OpenTelemetry；
- `gallery_server.py:94-104`：创建 app、CORS、挂载 router；
- `gallery_server.py:107-121`：全局异常处理；
- `gallery_server.py:123-154`：健康与指标；
- `gallery_server.py:157-176`：Uvicorn 启动。

核心结构：

```python
app = FastAPI(title="Livehouse Gallery API", version="1.0.0")
app.add_middleware(CORSMiddleware, ...)
app.include_router(gallery_router)
app.include_router(infra_router)
app.include_router(agent_router)
```

### 3.1 `FastAPI()` 做了什么

- 创建 ASGI application；
- 自动生成 OpenAPI schema；
- 提供 `/docs` Swagger UI 和 `/redoc`；
- 注册 middleware、router、exception handler；
- 由 Uvicorn 承载 HTTP 连接。

### 3.2 ASGI 与 Uvicorn

FastAPI 是 ASGI framework，Uvicorn 是 ASGI server。

请求链：

```text
socket → Uvicorn → middleware → router → endpoint → response
```

项目设置：

- `limit_concurrency`：限制 API server 同时处理的连接/请求；
- `timeout_keep_alive=5`：Keep-Alive 空闲时间；
- `reload=False`：生产式运行。

注意：`limit_concurrency` 不是用户级 rate limit，也不能代替推理 queue 和 Job admission。

### 3.3 当前启动方式的特点

当前模块 import 时就会：

- 解析 CLI；
- 计算 `BASE_DIR`；
- 创建 Celery client；
- 配置 Gallery routes；
- 构建 app。

优点：启动简单。  
缺点：

- 测试时 import 带全局副作用；
- 不同配置实例难以同时创建；
- 资源初始化/关闭不集中；
- app factory 和 lifespan 不够明确。

更标准的演进方案：

```python
def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(...)
    ...
    return app
```

并使用 FastAPI lifespan 管理连接池、HTTP client、索引和 telemetry。

---

## 4. 路由拆分：`APIRouter`

项目 API 目录：

```text
api/
  gallery_routes.py
  studio_routes.py
  infra_routes.py
  agent_routes.py
```

### 4.1 Gallery Router

职责：

- `analysis_results.json`；
- 图片和 HTML；
- Gallery 分页；
- curation/taste；
- export；
- ingest hook；
- analyze task。

入口：`api/gallery_routes.py`。

### 4.2 Studio Router

职责：

- session 列表；
- 当前活动 session；
- 分析状态；
- 单场/批量分析；
- ingest 配置。

入口：`api/studio_routes.py`。

Studio router 不是直接在 `gallery_server.py` 挂载，而是在 Gallery router 尾部：

```python
router.include_router(studio_router)
```

### 4.3 Infra Router

职责：

- Job 列表和详情；
- timeline/stages；
- retry/cancel/dead letter；
- workers/pools；
- providers；
- metrics/cost；
- experiments。

入口：`api/infra_routes.py`。

Infra 与 JobExecutor 读取同一 SQLite SSOT，不是另一套平行状态。

### 4.4 Agent Router

职责：

- chat；
- SSE stream；
- history；
- trace；
- artifact。

入口：`api/agent_routes.py`。

### 4.5 为什么要拆 Router

- 按业务域组织；
- 避免一个入口文件无限增长；
- 可独立设置 prefix、tags、dependencies；
- 方便测试和文档；
- 减少跨模块耦合。

当前 `gallery_routes.py` 和 `infra_routes.py` 仍然较大。以下只是 proposed structure，
不是当前仓库中已经存在的模块，后续可继续拆成：

```text
api/gallery/results.py
api/gallery/export.py
api/gallery/images.py
api/infra/jobs.py
api/infra/workers.py
api/infra/metrics.py
```

但应保证 service 层不被复制。

---

## 5. 请求参数：Path、Query、Header、Body

FastAPI 根据函数签名自动解析参数。

## 5.1 Path Parameter

例：

```python
@router.get("/api/infra/jobs/{job_id}")
def infra_job_detail(job_id: int):
    ...
```

FastAPI 自动：

- 从 URL 提取 `job_id`；
- 转换为整数；
- 转换失败返回 422。

项目示例：

- `/api/infra/jobs/{job_id}`
- `/api/infra/workers/{worker_id}/pause`
- `/api/tasks/{task_id}`
- `/api/agent/artifacts/{session_id}/{name}`

## 5.2 Query Parameter

Gallery 分页：

```python
offset: int = Query(0, ge=0)
limit: int = Query(500, ge=1, le=5000)
```

价值：

- 自动类型转换；
- 自动边界校验；
- 自动进入 OpenAPI；
- 非法值自动返回 422。

项目示例：

- `sort`
- `offset/limit`
- `lite`
- `dedupe`
- `archive_root`
- `config_path`

### Query 适合什么

- 分页；
- 筛选；
- 排序；
- 可选行为开关；
- 资源定位的非敏感附加条件。

复杂结构、批量对象和写操作参数更适合 Body。

## 5.3 Header Parameter

Ingest：

```python
x_luma_token: str | None = Header(default=None, alias="X-Luma-Token")
```

Ops：

- `X-Livehouse-Ops-Token`
- `X-Luma-Token`
- `Authorization: Bearer ...`

Header 适合：

- token；
- request id；
- content negotiation；
- idempotency key；
- trace propagation。

## 5.4 Request Body

Studio：

```python
class AnalyzeBody(BaseModel):
    previews_dir: str = Field(..., min_length=1)
    config_path: str = "configs/livehouse.yaml"
    force_full_rerun: bool = True
```

Agent：

```python
class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
```

Body 适合：

- 多字段写请求；
- 嵌套对象；
- 批量列表；
- 需要 schema/version 的数据。

---

## 6. Pydantic：请求和响应契约

项目使用 Pydantic 做：

- 类型转换；
- 长度/数值范围；
- 默认值；
- 嵌套模型；
- response model；
- OpenAPI schema。

## 6.1 `Field`

示例：

```python
limit: int = Field(default=500, ge=1, le=500)
selected_files: list[str] | None = Field(default=None, max_length=200)
```

它能将业务边界前移到 API 层。

## 6.2 `default_factory`

正确写法：

```python
tool_calls: list[dict[str, Any]] = Field(default_factory=list)
```

避免把同一个可变 list 作为多个实例的默认值。

## 6.3 `field_validator`

`ExportImageSpec.rotate` 使用 `field_validator` 兼容 `None → 0`。

适合：

- 历史客户端兼容；
- 字段归一化；
- 简单跨格式转换。

复杂业务判断不应全部塞进 validator，应放 service 层。

## 6.4 `extra="ignore"`

Gallery 多个请求模型允许忽略额外字段。

优点：

- 旧后端兼容新前端多余字段；
- 渐进式升级简单。

风险：

- 拼错字段会被静默忽略；
- 客户端误以为字段生效。

高风险写接口可考虑 `extra="forbid"`；兼容接口则保留 ignore 并增加日志/契约测试。

## 6.5 `response_model`

Agent：

```python
@router.post("/api/agent/chat", response_model=ChatResponse)
```

Infra 大量使用：

```python
@router.get("/api/infra/jobs", response_model=InfraJobListResponse)
```

价值：

- 返回结构校验；
- 自动文档；
- 过滤未声明字段；
- 前后端契约明确。

当前 Gallery 部分 endpoint 返回裸 dict/`JSONResponse`，一致性弱于 Infra/Agent。后续可逐步为稳定 API 增加 response model。

---

## 7. Response 类型

## 7.1 普通 Dict / Pydantic

```python
return {"ok": True, "job_id": job_id}
```

FastAPI 自动 JSON 编码。

适合标准 JSON API。

## 7.2 `JSONResponse`

项目在需要自定义状态码时使用：

```python
return JSONResponse(status_code=202, content={...})
```

适合：

- 202 Accepted；
- 特定错误格式；
- 自定义 headers。

注意：直接使用 `JSONResponse` 时不会自动经过 response model 的完整校验。

## 7.3 `FileResponse`

用于：

- 图片；
- Agent artifact；
- 下载文件。

需要先做：

- 路径 allowlist；
- 文件存在性；
- MIME type；
- 下载文件名；
- cache header。

## 7.4 `HTMLResponse`

项目保留部分 Gallery HTML/legacy 页面。

动态 HTML 中必须避免直接插入未转义用户内容。

## 7.5 `Response`

Prometheus：

```python
return Response(content=payload, media_type=content_type)
```

因为 Prometheus exposition format 不是 JSON。

## 7.6 `StreamingResponse`

Agent SSE：

```python
return StreamingResponse(
    generator,
    media_type="text/event-stream",
    headers=headers,
)
```

项目设置：

- `Cache-Control: no-cache, no-transform`
- `X-Accel-Buffering: no`
- `Connection: keep-alive`

这些 header 用于减少代理缓存和缓冲。

重要边界：当前 `token` event 是 finalized reply 的打字机分块，不是真正 provider token streaming。

---

## 8. 同步与异步：`def`、`async def` 与 Worker

FastAPI 支持：

```python
def endpoint():
    ...

async def endpoint():
    ...
```

### 8.1 `async def` 不等于自动高性能

如果在 `async def` 内调用：

- OpenCV；
- 同步 `requests`；
- 大量文件复制；
- SQLite 长事务；
- CPU 密集计算；

仍会阻塞 event loop。

### 8.2 本项目为什么大多使用普通 `def`

大量操作是：

- 同步 SQLite；
- 文件系统；
- Celery `send_task`；
- 同步 service。

Starlette 会把普通同步 endpoint 放入 threadpool，避免直接阻塞 event loop。

### 8.3 什么应该交给 Celery

- 完整分析；
- 大批量 RAW/JPEG export；
- film prewarm；
- 长时间图像渲染；
- 可重试后台任务。

### 8.4 什么可以留在请求内

- 参数校验；
- 小型查询；
- Job 创建；
- session pointer 更新；
- 小文件 metadata；
- 分页读取。

### 8.5 当前需要注意的阻塞点

Gallery 同步 export 仍可在 HTTP 请求中执行。小批量方便，但大批量应使用 `background=true`。

部分 Gallery 图片渲染和 enrich 也可能较重，因此已有：

- `lite` 模式；
- pagination；
- cache；
- prewarm。

---

## 9. 长任务模式：Job + Celery

分析 endpoint：

```text
POST /api/tasks/analyze
  → 校验 source_dir
  → SQLite create_analyze_path_job
  → Celery send_task("tasks.run_job", [job_id])
  → 返回 job_id/status/trace_id
```

关键实现：`api/gallery_routes.py:1410-1451`。

### 9.1 为什么先写数据库，再发消息

数据库是 Job SSOT。即使 Celery 消息重复，worker 仍需原子 claim。

返回：

```json
{
  "ok": true,
  "job_id": 61,
  "status": "QUEUED",
  "trace_id": "job-...",
  "run_task_id": "...",
  "task_name": "tasks.run_job"
}
```

客户端应使用 `job_id` 查询 Infra/Studio 状态，而不是只依赖 Celery task id。

### 9.2 防重复提交

Studio `_enqueue_studio_analyze` 会查询同一 session 是否已有 runnable Job。

若存在，返回：

```json
{
  "status": "already_running",
  "job_id": 123
}
```

这是 API 级幂等/去重的一部分。最终可靠性仍由数据库 claim 保证。

### 9.3 单场与批量分析

- 单场：提交后设置 active session；
- 批量：每个 session 一个 Job，不改变全局 active pointer；
- 批量循环允许单个 session 失败而继续其他项；
- 响应区分 started/already_running/skipped/errors。

### 9.4 202 Accepted

后台 export 返回 202，表示“已接受”，不是“已完成”。

主分析接口当前返回普通 dict，语义上也属于异步 Job 创建，可进一步统一使用 202 和明确 response model。

### 9.5 两类异步状态

必须区分：

1. 主分析：SQLite `jobs` 是 SSOT；
2. 后台 export/prewarm：主要读取 Celery `AsyncResult`。

因此后台 export 的耐久性低于主分析 Job。不能把 `/api/tasks/{task_id}` 的 Celery 状态当成所有业务任务的统一真相。

---

## 10. Studio API：文件系统与数据库聚合

Studio session 状态不是单表查询，而是聚合：

- archive 文件系统；
- `latest_session.json`；
- Brain session；
- 最新 Job；
- Job events；
- `analysis_results.json`。

`studio_status` 流程：

```text
resolve archive
→ resolve active/explicit previews
→ validate directory
→ count previews
→ query brain session + latest job/events
→ build pipeline view
→ return public job fields
```

### 值得学习的做法

- 对外只返回必要 Job 字段；
- DB 查询失败时仍可返回文件系统状态；
- 连接在 `finally` 中关闭；
- active pointer 与 Gallery cache 同步失效；
- 批量接口按项目继续处理，不因一个错误全部失败。

### 需要注意

- Studio 当前接受绝对 `previews_dir`；
- `active-session` 检查目录存在且名称为 `Previews`，但多用户/公开部署还需要 archive root allowlist；
- 全局 active session 是单用户本地语义。

---

## 11. Gallery API：文件、分页与导出

### 11.1 Gallery 分页

`GET /api/gallery/results`：

- `offset >= 0`
- `1 <= limit <= 5000`
- `sort`
- `lite`
- `dedupe`

分析运行时会临时调整：

- sort 使用 default；
- dedupe 关闭；
- 防止处理中 artifact 不完整导致不稳定行为。

### 11.2 为什么需要 `lite`

完整 enrich 可能包含：

- PIL layout；
- RAW EXIF；
- 相似度；
- 额外路径解析。

Grid 首屏优先 `lite=true`，把重操作按需执行。

### 11.3 导出

`ExportRequest` 支持：

- 简单图片列表；
- 每图 rotate/film variant；
- session vibe；
- background。

后台任务提交时固定 `base_dir`：

```python
kwargs={
    "request_payload": req.model_dump(mode="json"),
    "base_dir": base_dir,
}
```

这样用户在任务执行中切换 active session，不会导出到错误场次。

### 11.4 文件系统也是 API 数据模型

```text
session/
  Previews/
  RAW/
  exported_images/export_<timestamp>/
```

路径约定需要像数据库 schema 一样被测试和版本化。

---

## 12. Infra API：Response Model 与 Dependency Injection

Infra 写操作采用：

```python
def infra_retry_job(
    job_id: int,
    _ops: None = Depends(require_ops_auth),
):
    ...
```

这是 FastAPI Dependency Injection。

## 12.1 `Depends` 的价值

- endpoint 不需要手动解析 token；
- 鉴权失败自动终止请求；
- OpenAPI 可表达 security/dependency；
- 依赖可测试覆盖；
- 可在 router 级统一应用。

适合抽取：

- DB connection；
- authenticated operator；
- active session；
- settings；
- pagination；
- trace context。

### 12.2 当前项目的两种鉴权写法

Infra：

```python
_ops: None = Depends(require_ops_auth)
```

Gallery ingest：

```python
require_ingest_auth(request, x_luma_token=x_luma_token)
```

两者都有效，但依赖注入更统一。Ingest 和专业 workflow 可进一步改为 `Depends`。

### 12.3 数据库连接

当前很多 endpoint 使用：

```python
conn = brain_connect()
try:
    ...
finally:
    conn.close()
```

这是正确的资源释放方式，但重复较多。可演进为：

```python
def get_brain_conn():
    conn = brain_connect()
    try:
        yield conn
    finally:
        conn.close()
```

然后：

```python
def endpoint(conn = Depends(get_brain_conn)):
    ...
```

注意：SQLite connection 的线程属性和事务范围必须验证，不能机械抽象。

---

## 13. Agent API 与 SSE

## 13.1 请求生命周期

```text
ChatRequest
→ resolve base_dir
→ owner = anon:<session_id>
→ build skill registry
→ build chat backend
→ load messages + working memory
→ AgentRunner
→ persist turn/events/memory
→ ChatResponse
```

### 13.2 为什么 registry 每次请求重建

注释给出的原因：

- 新分析结果及时生效；
- active previews directory 及时生效；
- session/owner scope 正确。

代价：

- 每请求重复构建对象；
- 需要确保构建成本受控；
- 大型索引应缓存或生命周期管理。

### 13.3 Response Model

`ChatResponse` 返回：

- reply；
- tool calls；
- guardrail events；
- memory turns；
- base dir；
- trace；
- structured final answer；
- error。

这让 UI 不只看到自然语言，还能展示 Agent 执行过程。

### 13.4 SSE 事件

```text
tool_call
token
done
error
```

当前行为：

1. Agent 生成并 finalize；
2. 解析 `{ref_N}`；
3. groundedness/guardrails；
4. 再分块发送 token。

优点：未校验内容不会提前输出。  
缺点：回答 TTFB 没有真正降低。

### 13.5 Streaming 错误处理

SSE 已经开始发送后，不能再把 HTTP 状态码改成 500，因此 generator 需要发送：

```json
{"type": "error", "error": "..."}
```

客户端必须同时处理：

- HTTP 层错误；
- SSE `error` event；
- 网络中断；
- 没有 terminal `done`；
- 重连/重复提交。

---

## 14. 异常处理与 HTTP 状态码

## 14.1 `HTTPException`

项目常用：

- 400：请求内容不合法；
- 401：token 无效/缺失；
- 403：路径不允许；
- 404：Job/Worker/文件不存在；
- 500：内部创建/写入失败；
- 503：服务未安全配置或依赖不可用。

### 400 与 422

- 422：Pydantic/类型校验自动产生；
- 400：类型合法，但不满足业务规则。

例如：

- `limit=-1` → 422；
- `previews_dir` 字符串合法但目录不存在 → 400。

### 401 与 403

- 401：未认证/认证失败；
- 403：已经识别请求，但无权限访问某资源。

项目中的路径 allowlist 使用 403。

### 404

用于资源不存在，例如 Job/Worker。

### 409 的使用机会

项目对 already running 通常返回 200 + `already_running`。另一种设计是：

- 幂等复用已有 Job：200；
- 明确冲突且不能复用：409 Conflict。

需要保持客户端契约一致。

## 14.2 全局异常处理

`gallery_server.py`：

```python
@app.exception_handler(Exception)
async def handler(...):
    traceback.print_exc()
    return JSONResponse(status_code=500, content={...})
```

值得学习：

- 不向客户端返回原始 exception；
- 返回稳定错误格式；
- 保留 `error_type`。

可改进：

- 使用结构化 logger，不直接 `traceback.print_exc()`；
- 加入 trace/request id；
- 统一 error code；
- 生产环境谨慎暴露 Python exception class；
- 对 expected domain errors 单独 handler。

## 14.3 错误泄露不一致

Ingest DB 检查中存在：

```python
detail=f"brain db: {e}"
```

这可能泄露内部路径或 SQLite 信息。更一致的做法：

- 服务端完整记录；
- 客户端返回 `"brain database unavailable"`；
- 附稳定 error code；
- 使用 `client_safe_error` 仅放行明确安全的输入错误。

---

## 15. CORS、鉴权、路径与 SSRF

## 15.1 CORS

项目明确 origins，不使用 `*` 配合 credentials：

```python
allow_origins=cors_allow_origins()
allow_credentials=True
```

默认允许：

- `LIVEHOUSE_LAB_URL`
- `127.0.0.1:3000`
- `localhost:3000`

CORS 不是鉴权。它只约束浏览器跨域读取。

## 15.2 Ops Auth

`require_ops_auth`：

- 配置 token 时必须匹配；
- 未配置 token 时仅允许 loopback；
- 可通过 `LIVEHOUSE_ALLOW_INSECURE_LOCAL=1` 显式放宽本地 demo。

### 当前不足

- 普通字符串比较不是专门 constant-time compare；
- 不是用户身份/RBAC；
- 部分产品写操作并未统一纳入 Ops Auth；
- 没有 token rotation/audit。

## 15.3 Ingest Auth

`require_ingest_auth` 默认 fail-closed：

- 有 token：检查 `X-Luma-Token`；
- 无 token：仅 loopback/显式 local demo；
- 非本地且未配置 token：503。

Go ingest 当前还需补齐 token 和非 2xx 处理，避免服务端安全配置后 hook 静默失败。

## 15.4 路径安全

`path_is_under_roots`：

- `expanduser`；
- `resolve(strict=False)`；
- `realpath`；
- `relative_to`；
- 处理 symlink 和未创建目标。

高风险 workflow 同时校验输入文件和输出目录。

### 仍需考虑

- 校验后到使用前的 TOCTOU；
- symlink 在请求处理中被替换；
- archive root 多租户隔离；
- 下载文件名注入；
- 超大文件和磁盘耗尽。

## 15.5 SSRF

`assert_public_http_url`：

- 只允许 HTTP/HTTPS；
- 拒绝 localhost/metadata host；
- 拒绝 private、loopback、link-local、reserved、multicast；
- DNS 解析后检查所有 A/AAAA。

高级风险：

- DNS rebinding；
- 重定向到私网；
- IPv4-mapped IPv6；
- 代理环境变量；
- 连接时再次解析。

如果后续允许用户配置 webhook/provider URL，需要同时限制 redirect 和实际连接目标。

---

## 16. Health、Readiness 与 Metrics

### `/healthz`

检查：

- DB；
- broker；
- provider；
- worker freshness。

它描述系统整体健康。

### `/readyz`

当前只检查 DB，表示 API 是否具备基本服务能力。

### `/metrics`

返回 Prometheus exposition format。

### 面试重点

不要把三者混淆：

- Liveness：进程是否需要重启；
- Readiness：是否接流量；
- Health：依赖和能力是否 degraded；
- Metrics：供监控系统采集，不用于单次请求业务判断。

未来容器编排中，不应因为外部 Ollama 短暂失败就把 API liveness 判死并无限重启。

---

## 17. Next.js 与 FastAPI 的边界

浏览器通常请求同源 Next `/api/*`：

```text
Browser → Next Route Handler/rewrite → FastAPI
```

好处：

- 避免浏览器直接知道内部 API origin；
- 减少 CORS；
- Showcase 可切 fixture；
- BFF 可统一错误和只读模式。

环境变量：

- `GALLERY_API_ORIGIN`：Next server；
- `NEXT_PUBLIC_API_BASE`：浏览器可见；
- FastAPI origin/secret 不应随意放进 `NEXT_PUBLIC_*`。

FastAPI 仍需自己的鉴权，不能因为请求经过 Next 就默认可信。

---

## 18. 测试 FastAPI

推荐层次：

## 18.1 Endpoint 单元/集成

使用：

```python
from fastapi.testclient import TestClient
```

测试：

- 422 schema；
- 400 业务校验；
- 401/403 auth；
- 404 resource；
- response schema；
- DB connection 关闭；
- path allowlist。

## 18.2 Dependency Override

若将 DB/auth/settings 抽为 `Depends`，测试可以：

```python
app.dependency_overrides[get_brain_conn] = fake_conn
```

这是引入 dependency injection 的重要收益。

## 18.3 Celery Dispatch

不要在普通 endpoint 测试中启动真实 VLM：

- mock `send_task`；
- 断言 task name、job_id；
- 检查先写 DB 再 dispatch；
- 单独集成测试 worker。

## 18.4 文件 API

使用 `tmp_path` 构造：

- archive；
- session；
- Previews/RAW；
- symlink；
- 非法路径；
- 缺文件；
- 大小写扩展名。

## 18.5 SSE

测试：

- content type；
- event 格式；
- tool_call 顺序；
- terminal done；
- generator exception → error event；
- disconnect；
- finalize 后才输出引用。

## 18.6 当前测试改进建议

优先增加：

1. OpenAPI schema snapshot/compatibility；
2. Next proxy ↔ FastAPI 契约；
3. Studio submit → Job status；
4. auth matrix；
5. path traversal/symlink；
6. Agent SSE terminal event；
7. background export 202/status。

---

## 19. 本项目 FastAPI 做得好的地方

1. **Router 按产品域拆分**；
2. **Pydantic 限制批量大小和字符串长度**；
3. **主分析先写 Job SSOT，再发 Celery**；
4. **Infra 写操作使用 `Depends(require_ops_auth)`**；
5. **CORS 使用显式 origins**；
6. **路径检查考虑 symlink**；
7. **全局未知异常不回显原始内容**；
8. **提供 health/readiness/metrics**；
9. **文件、JSON、HTML、SSE 等 response 类型按场景选择**；
10. **后台 export 固定提交时 base_dir，避免 session 切换竞态**；
11. **Agent response 同时暴露工具、trace 和 guardrail 信息**；
12. **Gallery 使用分页、lite 和 dedupe 控制请求成本**。

---

## 20. 可以改进的地方

按优先级：

### P0：安全与契约

- Studio/普通 Gallery 写操作建立统一 auth policy；
- Ingest 错误不回显原始 DB exception；
- 高风险 Pydantic 模型考虑 `extra="forbid"`；
- 增加正式 API version/error code；
- Go ingest 补 token 和非 2xx 处理。

### P1：可测试性与资源管理

- 引入 `create_app()`；
- 使用 lifespan 管理共享 client/index；
- DB connection/auth/settings 使用 dependencies；
- 统一结构化 logger/request id；
- 避免 import-time 全局配置副作用。

### P1：异步任务一致性

- 主分析 endpoint 统一返回 202；
- 后台 export 迁入 durable `jobs`；
- 使用 transactional outbox 缩小 DB commit 与 Celery dispatch 窗口；
- 增加 idempotency key。

### P2：模块拆分

- 拆分大 `gallery_routes.py`；
- endpoint 只做 HTTP adapter；
- 业务逻辑继续下沉 service；
- 统一 response models。

### P2：真实流式

- Provider token stream；
- typed SSE event；
- abort/cancel；
- groundedness 的增量/延迟提交策略；
- reconnect/checkpoint。

---

## 21. 动手练习

### 练习 1：新增只读 endpoint

新增：

```text
GET /api/studio/sessions/{session_key}/summary
```

要求：

- path 参数；
- response model；
- 404；
- 不泄露 archive 外路径；
- TestClient 测试。

### 练习 2：抽取 DB Dependency

将一个 Infra endpoint 改为：

```python
conn = Depends(get_brain_conn)
```

验证：

- 正常关闭；
- 异常关闭；
- dependency override；
- SQLite thread safety。

### 练习 3：统一 Error Model

设计：

```json
{
  "error": {
    "code": "JOB_NOT_FOUND",
    "message": "job not found",
    "trace_id": "..."
  }
}
```

兼容现有客户端，并为 400/401/403/404/500 写测试。

### 练习 4：分析接口幂等

支持 `Idempotency-Key`：

- 相同 key + 相同 payload 返回原 Job；
- 相同 key + 不同 payload 返回 409；
- key 写入 SQLite；
- 并发请求只有一个 Job。

### 练习 5：后台 Export Durable 化

把 export 建模为 Job：

- Job type；
- payload；
- progress；
- artifact；
- retry；
- cancel；
- 旧 Celery task status 兼容。

### 练习 6：SSE 客户端中断

研究：

- 浏览器断开时 generator 是否停止；
- provider 是否取消；
- turn 是否持久化；
- 如何定义 partial turn。

### 练习 7：安全测试

覆盖：

- `../`；
- symlink；
- private SSRF；
- DNS 返回多个地址；
- redirect 到 localhost；
- 缺 token；
- 错 token；
- 非 loopback 未配置 token。

---

## 22. FastAPI 高频面试题

### Q1｜FastAPI 为什么快？

回答要点：

- 基于 Starlette/ASGI；
- Pydantic 高效校验；
- 支持 async I/O；
- 性能仍取决于 endpoint 是否阻塞、DB 和业务。

不要回答“用了 async 所以一定快”。

### Q2｜`def` 和 `async def` 如何选择？

- async-compatible I/O client → `async def`；
- 同步库/短同步逻辑 → `def`；
- CPU/长任务 → worker；
- 不要在 async endpoint 调同步长阻塞。

### Q3｜FastAPI Dependency Injection 有什么价值？

- 复用 auth/DB/settings；
- 生命周期清理；
- 测试 override；
- router 级 policy；
- 减少 endpoint 样板代码。

结合项目：Infra ops auth 已使用 `Depends`。

### Q4｜Pydantic 的 validation error 为什么返回 422？

请求语法合法，但数据无法满足 endpoint schema。业务层目录不存在等通常返回 400。

### Q5｜如何处理长任务？

返回 202/job id，交给 Celery/队列；持久状态放数据库；支持查询、重试和取消。

结合项目：分析走 SQLite Job SSOT，Celery 只携带 job_id。

### Q6｜BackgroundTasks 和 Celery 如何选择？

- `BackgroundTasks`：同进程、短小、失败恢复要求低；
- Celery：跨进程、长任务、重试、独立 worker；
- Durable Job 还需要业务数据库状态，Celery 本身不够。

### Q7｜Middleware 与 Dependency 有何区别？

- Middleware：所有匹配请求的横切逻辑，位于路由前后；
- Dependency：路由/参数级，可访问解析结果并组合；
- CORS 用 middleware，ops auth 用 dependency。

### Q8｜如何做全局异常处理？

注册 exception handler，服务端记录完整错误，客户端返回稳定安全 schema；expected domain errors 使用 `HTTPException` 或专门 handler。

### Q9｜如何安全返回文件？

路径 allowlist、resolve realpath、权限、存在性、MIME、下载名、cache、Range/大文件，防 traversal/symlink。

### Q10｜如何做 API 版本管理？

- URL `/api/v1`、header 或媒体类型；
- schema 兼容；
- deprecation；
- consumer migration；
- contract test；
- 项目当前还没有正式版本前缀。

### Q11｜如何测试 FastAPI？

TestClient/HTTPX、dependency overrides、临时 DB/目录、mock external services、OpenAPI/response contract。

### Q12｜SSE 和 WebSocket 如何选择？

- SSE：服务端单向事件、HTTP 友好、自动重连；
- WebSocket：双向低延迟；
- Agent token/tool event 多数可用 SSE；
- 取消/交互复杂时可考虑 WebSocket，但成本更高。

### Q13｜如何实现 Rate Limit？

- reverse proxy/API gateway；
- Redis token bucket/sliding window；
- identity/IP/tenant key；
- 429 + Retry-After；
- FastAPI `limit_concurrency` 不是业务 rate limit。

### Q14｜如何防 SSRF？

- scheme/host allowlist；
- DNS 解析检查；
- 拒绝 private/link-local/metadata；
- 限制 redirect；
- 实际连接时复验；
- egress network policy。

### Q15｜如何保证创建 Job 的接口幂等？

- client idempotency key；
- DB unique constraint；
- payload hash；
- 并发事务；
- 返回已有资源；
- worker 再做原子 claim。

---

## 23. 结合项目的面试回答模板

### 题目：你在项目中如何使用 FastAPI？

参考结构：

> FastAPI 在项目中承担 HTTP 控制面。`gallery_server.py` 挂载 Gallery、Studio、Infra 和 Agent 四类 Router。短请求负责校验、查询 SQLite/artifact 和创建 Job；长时间 OpenCV/VLM 分析不会占用 API 请求，而是先写入 SQLite jobs，再由 Celery 只携带 job_id 唤醒 worker。Infra 写操作通过 FastAPI dependency 做 ops token 校验，图片和专业 workflow 还使用 realpath-based allowlist。服务同时暴露 health、readiness 和 Prometheus metrics。当前需要继续改进的地方是 app factory/lifespan、统一 response/error model、后台 export durable 化以及正式用户认证。

### 题目：FastAPI 在这个项目中最重要的设计取舍是什么？

> 不是选择了哪个 Web framework，而是明确 HTTP、Job 和推理三层边界：HTTP 负责接收与查询，SQLite Job 负责持久执行状态，Celery 负责唤醒，InferenceQueue 负责模型容量。这样 API 请求超时、Celery 重投和模型过载不会被混成一个问题。

### 题目：当前 API 是否达到生产 SaaS 水平？

> 没有。它适合可信本地、单节点个人工作流。已有 CORS、ops/ingest token、路径和 SSRF 防护，但 Agent 使用匿名 session，部分写接口没有统一 RBAC，active session 是全局文件指针，存储也是本地 archive。公开多租户部署前需要 OIDC/RBAC、tenant-scoped storage、统一 auth、Postgres 和对象存储。

---

## 24. 自测清单

- [ ] 能解释 FastAPI、ASGI 和 Uvicorn 的关系；
- [ ] 能画出 Browser → Next → FastAPI → Job 的调用链；
- [ ] 能列出四个 Router 的职责；
- [ ] 能区分 path/query/header/body；
- [ ] 能使用 Pydantic Field、validator 和 response model；
- [ ] 能解释 `def` 与 `async def`；
- [ ] 能说明为什么长任务交给 Celery；
- [ ] 能区分 Job SSOT 与 Celery AsyncResult；
- [ ] 能解释 400/401/403/404/409/422/500/503；
- [ ] 能说明 `Depends` 的价值；
- [ ] 能解释普通 JSON、FileResponse、Response、StreamingResponse；
- [ ] 能准确说明当前 SSE 不是真 token streaming；
- [ ] 能解释 CORS 不是鉴权；
- [ ] 能说明路径 allowlist 与 SSRF；
- [ ] 能设计 FastAPI TestClient 测试；
- [ ] 能指出当前 app factory、auth、error model 和 durable export 缺口。

---

## 25. 官方学习资料

### FastAPI

- [FastAPI Tutorial](https://fastapi.tiangolo.com/tutorial/)
- [Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [Request Body](https://fastapi.tiangolo.com/tutorial/body/)
- [Response Model](https://fastapi.tiangolo.com/tutorial/response-model/)
- [Handling Errors](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [Testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)

### ASGI / Server

- [Starlette](https://www.starlette.io/)
- [Uvicorn](https://www.uvicorn.org/)
- [ASGI Specification](https://asgi.readthedocs.io/en/latest/)

### Schema / HTTP

- [Pydantic](https://docs.pydantic.dev/latest/)
- [HTTP Semantics RFC 9110](https://www.rfc-editor.org/rfc/rfc9110)
- [MDN HTTP Status](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status)
- [MDN SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)

### Security

- [OWASP API Security Top 10](https://owasp.org/API-Security/)
- [OWASP Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal)
- [OWASP SSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)

