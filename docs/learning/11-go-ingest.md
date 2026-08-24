# 11｜Go 摄取、EXIF、RAW 与 Preview

## 学习目标

- 读懂 Go CLI、goroutine、worker pool、context 和文件扫描；
- 对照本仓库说明用了哪些 Go 知识点（并发 / 网络 / I/O 调优），以及明确没用哪些；
- 理解 SD → archive → SQLite Brain → Gallery hook；
- 掌握 EXIF、RAW 内嵌 Preview、文件稳定窗口和幂等摄取；
- 区分 Brain 主路径与 legacy pipeline-cmd；
- 能设计断点恢复和重复文件处理。

## 1. 模块

- `cmd/ingest/`：主摄取；
- `cmd/preview-extractor/`：通过 exiftool 提取 RAW preview；
- `cmd/arw-extractor/`：纯 Go 扫 ARW 内嵌 JPEG；
- `cmd/jpeg-organizer/`：按 EXIF 时间整理 JPEG。

## 2. Go 知识点对照（对照本仓库）

先给结论，避免面试夸大：

- 这是 **I/O 密集型 CLI / 轮询 worker**，不是高 QPS 网络服务；
- **有并发，但是有界磁盘并发**，不是「上万连接 / 抢锁调优」那种高并发；
- **有网络，但是薄 HTTP 客户端**，服务端在 Python FastAPI；
- **有性能意识，集中在 SD/磁盘/子进程**，没有 `pprof`、逃逸分析或 CPU 热点优化。

Go 不使用异常控制普通错误。文件、EXIF 和数据库操作都应逐层返回并添加上下文（`fmt.Errorf("…: %w", err)`）。

### 2.1 语法与工程基础（必会）

| 知识点 | 本仓库怎么用 | 关键文件 |
|---|---|---|
| `package main` + `flag` CLI | ingest / preview / JPEG 都是独立命令 | `cmd/ingest/main.go` |
| struct + 显式 error | session 状态、Brain 行、复制失败上卷 | `types.go`、`luma_brain.go` |
| `path/filepath` 扫描 | `WalkDir`、大小写 `RAW/Raw/raw`、跳过 `._` / 隐藏目录 | `scanner.go`、`ingest_sd_brain.go` |
| `os/exec` | 调 `preview-extractor`、exiftool、legacy `run_pipeline.py` | `executor.go`、`preview-extractor` |
| `//go:embed` | 把 `luma_brain_schema.sql` 打进二进制 | `luma_brain.go` |
| `database/sql` + 事务 | session upsert、照片 NEW→INGESTED、失败回滚文件 | `luma_brain.go` |

`cmd/` 已有 preview 原子发布与 extractor 命令选择的回归测试，CI 运行
`go test -race ./...`；其余 ingest 故障场景仍需用动手实验（§11）补充。

### 2.2 并发编程：有，但是「有界 I/O」，不是高并发服务

用到的模式：

1. **信号量 channel**：`sem := make(chan struct{}, N)` + `go func` + `WaitGroup`。限制同时 `Stat` / 抽 preview 的协程数。  
   见 `listARWWithPool`（`ingest_sd_brain.go`）、`cmd/preview-extractor/main.go`。
2. **固定 worker + job channel**：`jpeg-organizer` 起 N 个 worker，从 `chan job` 取任务；每 worker 复用拷贝缓冲。这是教科书 worker pool。
3. **轮询 + 后台 goroutine**：`time.NewTicker` 循环扫描；稳定 session 后 `go runSession(...)`，用 `pipelineRunning` + `sync.Mutex` 保证同时只跑一场。  
   见 `main.go`、`scanner.go`。
4. **计数用 atomic**：preview / JPEG 搬运的 `ok/fail` 用 `atomic.AddInt64`，避免给热路径加锁。
5. **共享状态用 Mutex**：JSON worker state、`mkdir` 去重。SQLite 则用 `SetMaxOpenConns(1)`，不靠多连接并发写。

面试怎么讲：

> 并发目标是吃满 SD/磁盘带宽，同时不把机械盘或 exiftool 打爆。Worker 数默认 4–6，并按介质单独基准，不是 `NumCPU()*N`。

明确没用：

- `errgroup`、pipeline fan-in/fan-out、work stealing；
- 连接级高并发（listener、连接池、限流中间件）；
- `signal.Notify` / 全链路 `context` 取消（文档旧列表提过 signal，**代码未实现**）；
- `context` 只出现在 Brain `Ping` / 建表的 15s timeout，ingest 主循环没有 cancel。

### 2.3 网络编程：只有 HTTP 客户端

唯一网络面是 Gallery hook：`http.Client{Timeout}` + `http.NewRequest(POST)`，在 goroutine 里 fire-and-forget。

```text
Go ingest 写完 Brain
  → go triggerGalleryCheck(url, 25s)
  → POST /api/ingest/check_new_images
  → Python 扫库并 seed ANALYZE_SESSION
```

见 `triggerGalleryCheck`（`ingest_sd_brain.go`）。这是 **client 超时 + 异步通知**，不是：

- `net/http` Server / `ListenAndServe`；
- WebSocket、gRPC、自定义 TCP；
- TLS 证书、连接复用调优。

当前缺口（§9）：未带 ingest token、未校验非 2xx、无重试。网络知识点够讲 timeout / 幂等 hook，不够当「网络编程项目」作品。

### 2.4 性能调优：磁盘与幂等，不是 profiler

本仓库的「快」来自少做贵操作，而不是把 Go 编译器压到极限。

| 手段 | 为什么算调优 | 在哪 |
|---|---|---|
| 抽 Preview，不解码整颗 RAW | 避免慢路径进入 Gallery / OpenCV / VLM | `preview-extractor`、§6 |
| 指纹用 name+size+mtime，不整文件 SHA | 大 RAW 全量 hash 会拖垮插卡扫描 | `FingerprintARW` |
| 目标已存在则跳过 | 重复插卡只扫元数据 | preview skip、`EnsureIngestedRow` |
| 临时文件 + `Sync` + `Rename` | 崩溃不留半文件，也避免读到截断 JPEG | `copyFileAtomic` |
| `io.Copy` / `io.CopyBuffer`（约 4MiB） | 大文件拷贝少 syscall | `luma_brain.go`、`jpeg-organizer` |
| exiftool 按批（默认 80） | 一次子进程摊多张，避免每文件 fork | `jpeg-organizer` `batchCreateDates` |
| SQLite `WAL` + `busy_timeout` + 单连接 | 减少 writer lock 与 “database is locked” | `OpenLumaBrain` |
| 稳定窗口后再摄取 | 防止拷一半的 RAW / preview | `scanner.go`、§4 |
| worker 上限 | SD / 网络卷随机读会随并发变慢 | `--workers`、§8 |

明确没用：`net/http/pprof`、benchmark、逃逸分析、`GOMAXPROCS`、对象池、零拷贝 `sendfile`。`arw-extractor` 甚至 `io.ReadAll` 整颗 ARW 再扫 JPEG marker，这是正确性优先的备用实现，**不是**高性能解析器。

### 2.5 系统编程与二进制

- 文件系统：稳定窗口、原子 rename、跨卷复制（先 copy 再删，不能假设 `Rename` 跨设备成功）；
- 子进程：`Cmd.Dir` / stdout 继承 / `sh -c` 模板展开（legacy pipeline）；
- 二进制扫描：`encoding/binary` + JPEG SOI/EOI（`0xFFD8`/`0xFFD9`），仅 ARW 备用路径；
- CGO：`github.com/mattn/go-sqlite3` 需要 CGO，交叉编译和部署时要知道。

### 2.6 和 Python 侧怎么分工（面试一句）

Go 负责 **扫描、复制、抽 preview、写账本、通知**；Python 负责 **Job、OpenCV、VLM、Gallery、Agent**。选 Go 是因为启动快、静态二进制、文件/子进程控制简单，不是因为这里需要百万级 goroutine。

## 3. 推荐主路径

```text
SD mount
  → scan files
  → determine session
  → copy RAW/JPEG to archive
  → extract Preview
  → write sessions/photos in SQLite
  → POST gallery ingest hook
  → Python seed ANALYZE_SESSION jobs
```

关键文件：

- `cmd/ingest/main.go`
- `cmd/ingest/ingest_sd_brain.go`
- `cmd/ingest/luma_brain_schema.sql`
- `cmd/ingest/scanner.go`
- `cmd/ingest/executor.go`

## 4. 文件稳定窗口

相机/拷贝进程可能仍在写文件。Scanner 不应看到文件就立即处理，而应检查：

- size/mtime 在一段时间内稳定；
- 文件可打开；
- 可选 checksum；
- 临时扩展名；
- session 是否完成。

否则会提取到截断 preview 或复制半个 RAW。

## 5. EXIF

常见字段：

- DateTimeOriginal；
- camera/lens；
- ISO；
- shutter；
- aperture；
- orientation；
- embedded preview offset/length。

注意：

- 时区可能缺失；
- 相机时间可能不准；
- 文件修改时间不是拍摄时间；
- orientation 决定展示旋转；
- 不同厂商 RAW metadata 结构不同。

## 6. RAW 与 Preview

RAW 保留传感器数据，体积大、解码慢；相机通常嵌入 JPEG preview。

主流程优先提取 preview 用于：

- 快速 Gallery；
- OpenCV；
- VLM 缩略图；
- 减少 RAW decode。

最终高质量调色/导出可从 RAW develop。

`preview-extractor` 依赖 exiftool，`arw-extractor` 是针对 ARW 的备用实现。不要假设纯 Go 扫 JPEG marker 适用于所有 RAW 格式。

## 7. 文件与数据库幂等

重复插卡或重启时不应重复导入。

可用 identity：

- device/session + relative path；
- size + mtime；
- content hash；
- camera sequence id。

数据库应有 unique constraint/upsert，文件复制应：

- 检查目标；
- 使用临时文件；
- 完成后原子 rename；
- hash/size 校验；
- 不覆盖不同内容。

## 8. 并发 Worker

提取和复制可并发，但需要限制：

- SD 卡随机读吞吐；
- exiftool 子进程；
- archive 写入；
- SQLite 写；
- 内存。

Worker 数不是越多越好。对机械盘、SD 卡和网络卷需单独基准。

## 9. Gallery Hook

Go 写完 Brain 后调用 `/api/ingest/check_new_images`。Hook 的职责是通知 Python 扫描数据库并 seed jobs，而不是在 HTTP 中执行完整 pipeline。

需要：

- timeout；
- token；
- retry/backoff；
- 幂等；
- hook 失败后可由 beat/人工重新 dispatch。

当前实现风险：Go hook 尚未完整处理 ingest token 和非 2xx 状态。生产化时应补请求鉴权、状态码校验、有限重试与测试；数据库中的已摄取记录仍应允许后续重新 seed，避免把 HTTP 成功当作唯一恢复路径。

## 10. Legacy Mode A

`executor.go` 仍可直接调用 `run_pipeline.py`。它便于兼容，但绕过完整 durable job timeline。

面试主叙事：

- 推荐：Brain + hook + Job；
- Legacy：archive poll + subprocess；
- 两者共享 pipeline 领域实现，但可观测与恢复能力不同。

## 11. 动手实验

1. 用临时目录模拟 SD，导入两次，验证数据库和文件不重复。
2. 写一个慢速追加文件，验证稳定窗口不会提前摄取。
3. 比较 exiftool preview 与纯 Go ARW 提取结果和性能。
4. 中断复制后重启，验证临时文件和原子 rename。
5. 让 hook 失败，验证数据仍在 Brain 且可后续 seed。

## 12. 面试问题

- 为什么用 Go 做 ingest、Python 做 AI？
- 这里的 goroutine 算高并发吗？和写 HTTP 服务有什么差别？
- 如何保证重复插卡不重复导入？
- 为什么先提取 Preview 而不是直接解码 RAW？
- 文件复制中断如何恢复？
- Go worker pool 如何确定并发？SD 卡上把 workers 开到 32 会怎样？
- Gallery hook 用了哪些网络原语，缺了哪些生产化细节？

## 13. 自测清单

- [ ] 能读懂 goroutine/channel/WaitGroup，并说明这是有界 I/O 并发而不是高并发服务；
- [ ] 能指出网络面只有带 timeout 的 HTTP client，服务端不在 Go；
- [ ] 能列举 3 个本仓库的 I/O 调优点（Preview、原子 copy、SQLite WAL / worker 上限）；
- [ ] 能解释 EXIF orientation；
- [ ] 能说明 RAW 与 Preview；
- [ ] 能设计文件/DB 幂等；
- [ ] 能区分主 ingest 与 legacy；
- [ ] 能解释 hook 失败为什么不应丢数据。

## 延伸资料

- [A Tour of Go](https://go.dev/tour/)
- [Effective Go](https://go.dev/doc/effective_go)
- [Go Concurrency Patterns](https://go.dev/blog/pipelines)（对照本仓库的 semaphore / worker pool，不必先学完整 pipeline）
- [database/sql](https://pkg.go.dev/database/sql)
- [ExifTool](https://exiftool.org/)
- [Exif specification](https://www.cipa.jp/std/documents/e/DC-008-Translation-2019-E.pdf)

