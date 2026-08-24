# Go 数据接入层笔记

`cmd/ingest/` 这个常驻 Go 进程的职责边界、两条并存的接入路径、SQLite 账本语义，
以及四个 Go 命令行工具里用到的并发模式。行号会随提交漂移，以符号名/文件为准。

配套材料：`cmd/ingest/luma_brain_schema.sql`（表语义 SSOT）、`docs/PROJECT_GUIDE.txt`、
`docs/LUMAKERNEL_ARCHITECTURE.md`、`.env.example`（Go ingest 段）。

---

## 1. 定位与职责边界

`module photo-organizer`，`go 1.22`，四个 `cmd/` 二进制，全部是 `package main`：

| 目录 | 角色 |
|---|---|
| `cmd/ingest/` | **接入层主进程**（常驻）：发现新照片 → 落地归档 → 登记账本 → 通知 Python |
| `cmd/preview-extractor/` | 从 ARW 抽预览 JPEG，被 ingest 以子进程方式调用，也可独立跑 |
| `cmd/arw-extractor/` | 独立 ARW 处理工具（纯串行，无并发） |
| `cmd/jpeg-organizer/` | 按 EXIF 日期归档 JPEG（channel worker 池） |

关键点：**接入层不做任何图像分析**。它和 Python 侧只通过三样东西耦合——
归档目录里的文件、`luma_brain.db` 的表、以及一个 HTTP 钩子。

第三方依赖只有四个：`godotenv`（读 `.env`）、`mattn/go-sqlite3`、`google/uuid`、`go-redis`。

---

## 2. 两条并存的接入路径

由 `--sd-mount` 决定走哪条，在 `main.go` 里分叉。**传了 `--sd-mount` 就走模式 B，
legacy 轮询被完全禁用**。

### 模式 B：SD 卡增量扫描（推荐的生产路径）

```
SD 卡 → listARWWithPool 扫 ARW → 按 EXIF 日期分组 → 逐文件指纹去重
     → 原子复制进 <archive>/<date>/RAW/ → 写 SQLite photos/sessions
     → preview-extractor 抽预览 → 写 runtime/latest_session.json
     → POST /api/ingest/check_new_images
```

入口 `runSDIngestLoop` → `sdScanOnce`（`ingest_sd_brain.go`），默认 30s 一轮。

### 模式 A：归档目录轮询（legacy）

轮询 `--archive-root` 下的日期文件夹，状态存 JSON，稳定后直接 `sh -c` 起
`run_pipeline.py` 子进程。入口 `scanOnce`（`scanner.go`）→ `runSession`（`executor.go`），
默认 5s 一轮。

`executor.go` 的函数注释已明确标注这条路**绕过了 SQLite jobs 和 HTTP 钩子**，
不推荐用于生产。读代码时先确认自己在看哪一条，两条路的状态存储完全不同。

---

## 3. 文件速查

| 文件 | 作用 | 模式 |
|---|---|---|
| `main.go` | flag 解析、模式分叉、legacy 主循环 | 共用 |
| `ingest_sd_brain.go` | 扫卡、EXIF 分组、复制、触发钩子 | B |
| `luma_brain.go` | SQLite 打开/建表、指纹、session 与 photo 写入事务 | B |
| `luma_brain_schema.sql` | 全部表结构（`//go:embed` 进二进制） | B |
| `exif_session.go` | 批量 `exiftool` 取 CreateDate | B |
| `scanner.go` | 目录发现、快照对比、稳定窗口判定 | A |
| `executor.go` | preview-extractor + pipeline 子进程编排 | A |
| `state_manager.go` | JSON 状态读写、`latest_session.json` | A（后者共用）|
| `env.go` | `.env` 加载、archive root、device id、gallery hook 解析 | 共用 |
| `config.go` | `resolveRepoRoot` | 共用 |
| `studio_ingest_config.go` | 读 `configs/studio_ingest.json` 覆盖 CLI 默认值 | 共用 |
| `runtime_paths.go` | `<archive>/runtime/` 路径拼装 | 共用 |
| `logger.go` | ANSI 彩色前缀 `[SCAN]` `[EXE]` `[SUCCESS]` `[WARN]` `[ERROR]` | 共用 |
| `helpers.go` | 自定义 `max(a, b int)`（遮蔽了 Go 1.21 内置 `max`） | 共用 |

---

## 4. 关键 flag 与配置来源

```bash
go build -o ingest_worker ./cmd/ingest

./ingest_worker \
  --sd-mount /Volumes/CAMERA_SD \
  --archive-root ../Livehouse_Archive \
  --brain-db luma_brain.db \
  --device-id my_card \
  --gallery-hook http://127.0.0.1:8080/api/ingest/check_new_images \
  --workers 4
```

| flag | 默认 | 说明 |
|---|---|---|
| `--sd-mount` | 空 | 非空 → 模式 B。可由 Studio 配置填充 |
| `--archive-root` | `$LUMA_ARCHIVE_ROOT` → 仓库同级 `../Livehouse_Archive` → `~/Livehouse_Archive` | 见 `defaultArchiveRoot` |
| `--poll-seconds` | 0=自动（B:30 / A:5） | |
| `--stable-seconds` | 60 | 仅模式 A：文件不再变动多久算稳定 |
| `--workers` | 6 | 并发度，模式 B 里会被 `max(4, workers)` 抬到至少 4 |
| `--brain-db` | `luma_brain.db` | 相对路径按 `--repo-root` 解析 |
| `--repo-root` | `$LUMA_REPO_ROOT` → `$LIVEHOUSE_REPO_ROOT` → cwd | 子进程工作目录 |
| `--max-sessions-per-scan` | 1 | 仅模式 A |
| `--only-session` | 空 | 仅模式 A：只处理一个文件夹，用独立 state 文件 |

配置优先级（`env.go` + `studio_ingest_config.go`）：
**显式 flag > 环境变量 > `configs/studio_ingest.json` > 内置默认**。
`.env` 由 `godotenv` 从 cwd 加载，且**不覆盖已存在的环境变量**；
另外还会尝试加载 `$LUMA_REPO_ROOT/.env` 和 `$LIVEHOUSE_REPO_ROOT/.env`。

`GALLERY_HOOK` 和 `LIVEHOUSE_GALLERY_HOOK` 两个名字都认（后者是兼容别名）。

Docker 部署见 `deploy/docker-compose.yml` 的 `ingest` profile，SD 卡是
**只读挂载**（`${SD_MOUNT}:/sd:ro`），brain db 走独立 volume。

---

## 5. 模式 B 的一轮扫描

### 5.1 发现

`listARWWithPool` 递归 `filepath.WalkDir` 整张卡，过滤规则：

- 跳过隐藏目录（`SkipDir`，root 本身除外）
- 跳过 macOS 的 `._` 伴生文件
- 只要 `.ARW`（`EqualFold`，大小写不敏感）

然后用 worker 池并发 `os.Stat` 拿 size/mtime，非常规文件丢弃。

SD 卡不存在时（拔卡/路径没了）`sdScanOnce` 直接静默返回，不刷日志。

### 5.2 按 EXIF 日期分组

`groupCandidatesBySession` 按**每个文件自己的 EXIF CreateDate** 分组，所以一张混装了
昨天和今天的卡不会被塞进同一个日期文件夹。EXIF 由 `exif_session.go` 批量取：

```
exiftool -m -q -f -s3 -CreateDate -d %Y-%m-%d <最多 80 个文件>
```

`-f` 是关键：强制每个文件输出一行（缺 tag 时输出 `-`），否则「行号 → 文件路径」的
位置映射会错位，日期会串到别的文件上。exiftool 整批失败 → 该批全标 `Unknown_Date`
→ 回落到今天。

Studio 若在 `configs/studio_ingest.json` 里钉了 `session_folder_name`，
则**强制单一 session**，跳过 EXIF 分组。

### 5.3 逐文件入库

`ingestSessionGroup` 对每个候选文件：

1. 算指纹 → `LookupPhotoStatus` 查 `photos`。已是 `INGESTED`/`ANALYZING`/`ANALYZED` → 跳过。
2. 目标路径已存在且 size+mtime 一致（复制过但 DB 记录丢了）→ `EnsureIngestedRow`
   用 `INSERT OR IGNORE` 补一行，修复归档与 DB 的漂移。
3. 目标路径已存在但 size/mtime 不匹配（不同卡的同名 `DSC00001.ARW`）→ 文件名追加
   指纹前 8 位。
4. `IngestNewARW`：复制 + 写 DB。

只有 `nNew > 0` 才继续做后面的预览抽取、写 `latest_session.json`、发钩子。

---

## 6. 几个值得记住的设计

**指纹不读文件内容。** `FingerprintARW` 只用 `文件名(大写)|size|mtime` 算 SHA-256
（返回完整 64 位 hex；重命名时取前 8 位）。一张卡扫一百遍几乎零 I/O 开销。
代价是同名同大小同时间戳的不同文件会被误判为同一张——对相机出片场景可以接受。

**复制是原子的。** `copyFileAtomic`：写 `dest.partial` → `Sync()` 刷盘 → `os.Rename` 上位。
中途断电不会留下半个 ARW 冒充成品。DB 写入任一步失败都会 `os.Remove(destPath)` 回滚
已复制的文件，事务本身靠 `defer tx.Rollback()`。

**容错是「跳过而非中断」。** `isBusyOrIO` 把 `resource busy` / `permission denied` /
`input/output` / `no space left` 归为可跳过，单个坏文件不会让整轮扫描挂掉。
`preview_extractor` 失败也只 `logWarnf`——因为 RAW 已经安全落地了，预览可以重跑。

**状态机的边界（容易搞混）。** `photos.status` 只有 `NEW → INGESTED → ANALYZED`，
Go 侧只负责前两步，且在**同一个事务**里完成（先 INSERT 成 `NEW`，紧接着 UPDATE
成 `INGESTED`，同时 `sessions.photo_count + 1`）。真正的执行状态
（`CLAIMED`/`INFERENCING`/…）在 `jobs` 表里。schema 注释写得很直白：
`ANALYZING` 只为兼容老数据保留，**runnable work 的唯一真相源是 `jobs` + `job_events`**。

**SQLite 用法。** `WAL` + `_busy_timeout=8000` + `_foreign_keys=1`，
`SetMaxOpenConns(1)` 直接在连接池层串行化单写者，省掉手写锁。
schema 每次 `OpenLumaBrain` 都执行一遍（全是 `IF NOT EXISTS`，幂等）。
唯一约束：`sessions UNIQUE(session_key, device_id)`、`photos.file_hash UNIQUE`。

**模式 A 的稳定窗口。** `snapshotSession` 依次尝试 `RAW/` 下的 ARW → 根目录扁平 ARW
→ `Previews/` 里的图片，取到非空即止。快照的 (count, bytes, last_mod) 任一变化就刷新
`LastChangeUnix`；`pending` 且静默超过 `--stable-seconds` 才成为候选。
排序用 `sortSessionDirsNewestFirst`——先按文件夹名的 `YYYY-MM-DD` 前缀，再按 mtime，
避免 `ReadDir` 的字典序把 `2026-01-30` 排在 `2026-04-10` 前面。
进程启动时 `resetStaleRunningSessions` 把上次中断的 `running` 打回 `pending`。

---

## 7. 并发模型

全仓库 5 处 goroutine，三种模式。

### 7.1 信号量限流 + 每文件一个 goroutine

`listARWWithPool`（`ingest_sd_brain.go`）和 `cmd/preview-extractor/main.go` 都是这套：
带缓冲 channel 当信号量，`sync.WaitGroup` 等全部完成。

```go
sem := make(chan struct{}, w)
for _, p := range paths {
    p := p
    wg.Add(1)
    sem <- struct{}{}          // ← 在主循环里发送，不是在 goroutine 内
    go func() {
        defer wg.Done()
        defer func() { <-sem }()
        ...
    }()
}
wg.Wait()
```

**`sem <- struct{}{}` 必须写在主循环里**，这样生产者本身会被阻塞，同时存活的 goroutine
数量才真正被限住。写进 goroutine 内部的话会瞬间 spawn 几千个。

共享结果切片用 `sync.Mutex` 保护追加；preview-extractor 的计数用 `atomic.AddInt64`。

两处闭包传参风格不统一：`ingest_sd_brain.go` 用 `p := p` 重新声明，
preview-extractor 用显式传参 `go func(fName, srcDir, ...)`。都正确。

### 7.2 Channel 扇出的固定 worker 池

`cmd/jpeg-organizer/main.go`：先起固定数量 worker，用无缓冲 channel 喂任务，
`close(jch)` 后 `wg.Wait()`。任务多且单个很短时比 7.1 省创建成本。

两个细节：复制缓冲 `buf` **每个 worker 各分配一个**（不共享）；
`seenDir` 这个「已创建目录」缓存用 `mkdirMu` 保护，省掉重复 `MkdirAll` 系统调用。

### 7.3 Fire-and-forget

- `go triggerGalleryCheck(hook, 25*time.Second)`：通知 Python 的 HTTP 钩子，
  不阻塞下一轮扫卡，超时靠 `http.Client{Timeout}` 兜住，UA 是 `livehouse-ingest-worker/1`。
- `scanner.go` 里起 goroutine 跑整个 pipeline session，主循环得以继续轮询发现新文件。
  用 `pipelineRunning` 布尔标志做 single-flight，保证同时只有一个 pipeline。
  该标志的读写全部在 `stateMu` 持锁区间内，`SessionState` 的每次字段修改也都单独加锁
  （`executor.go` 里那些小的 `Lock/Unlock` 块），所以没有数据竞争。

### 7.4 共享状态保护手段汇总

| 手段 | 用在哪 |
|---|---|
| `sync.Mutex` | `stateMu` 保护 `WorkerState`；`mu` 保护结果切片；`mkdirMu` 保护目录缓存 |
| `atomic` | 计数器（旧式 `atomic.AddInt64` 与新式 `atomic.Int64` 混用） |
| channel | 信号量限流、任务分发 |
| `SetMaxOpenConns(1)` | SQLite 层串行化单写者 |

---

## 8. 与 Python 的交接

Go 侧的终点是一个 POST：

```
POST /api/ingest/check_new_images        (api/gallery_routes.py)
  → tasks.process_brain_ingested         (tasks/ingest.py)
  → seed ANALYZE_SESSION jobs
  → send_task("tasks.run_job", [job_id])
```

`process_brain_ingested` 只把 `photos` 当作**候选发现**用（找出有待分析文件的 session），
runnable 的判定完全走 `jobs` 表。Celery 的返回值不是生命周期的权威来源。

鉴权是 fail-closed 的（`utils/http_security.require_ingest_auth`）：

| 条件 | 结果 |
|---|---|
| `LIVEHOUSE_INGEST_TOKEN` 已设置 | 必须带匹配的 `X-Luma-Token`，否则 401 |
| 未设置 + 客户端是 loopback（或 `LIVEHOUSE_ALLOW_INSECURE_LOCAL=1`） | 放行，Python 侧打 warning |
| 未设置 + 非 loopback | 503 `LIVEHOUSE_INGEST_TOKEN is not configured` |

另一个交接点是 `<archive>/runtime/latest_session.json`（`LatestSessionRef`：
archive_root / session_dir / raw_dir / previews_dir / updated_at），
供 `gallery_server.py` 定位「当前 session」。模式 A 和 B 都会写。

---

## 9. 观察点与待办

- **Hook 不是唯一交接保证。** Go 已发送 `X-Luma-Token` 并记录非 2xx；Celery Beat
  还会周期执行 `process_brain_ingested`，因此一次 fire-and-forget Hook 丢失不会永久漏单。
- **preview-extractor 的生产路径已预编译。** ingest 镜像包含
  `/usr/local/bin/preview-extractor`；源码开发环境找不到二进制时才回退到 `go run`。
- **SIGTERM 会协作取消。** root context 会停止扫描并取消 preview/Python 子进程；
  模式 A 最多等待 10 秒让 session goroutine 保存状态。
- **模式 A 仍在代码里。** 不要在它上面加新功能，新逻辑走模式 B。
- **`helpers.go` 自定义了 `max(a, b int)`**，遮蔽了 Go 1.21 起的内置泛型 `max`。
  想删的话要确认所有调用点都是 int。
