package main

import (
	"flag"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

func main() {
	loadDotEnv()

	defaultArchive := defaultArchiveRoot()

	archiveRoot := flag.String("archive-root", defaultArchive, "Archive root path (default: $LUMA_ARCHIVE_ROOT, else ../Livehouse_Archive when cwd is the repo, else ~/Livehouse_Archive)")
	pollSeconds := flag.Int("poll-seconds", 0, "Polling interval seconds (0=auto: 30 with --sd-mount, else 5)")
	stableSeconds := flag.Int("stable-seconds", 60, "Stable window seconds before trigger")
	workers := flag.Int("workers", 6, "Preview extraction workers")
	stateFile := flag.String("state-file", "", "State JSON path (default: <archive-root>/runtime/ingest_worker_state.json)")
	pipelineCmd := flag.String("pipeline-cmd", "python run_pipeline.py --config configs/livehouse.yaml --source-dir {previews_dir} --no-serve", "Pipeline command template after previews (sh -c). Supports {session_dir} {raw_dir} {previews_dir} {session_name} {archive_root}")
	maxPerScan := flag.Int("max-sessions-per-scan", 1, "Max sessions to dequeue per poll when idle (default 1). Pipeline runs in the background so scans keep discovering new folders.")
	verbose := flag.Bool("verbose", false, "Log each session snapshot count (debug)")
	onlySession := flag.String("only-session", "", "Only ingest this folder (basename like 2026-04-10 or absolute path). Ignores all other dates; uses a separate state file unless --state-file is set.")
	repoRootFlag := flag.String("repo-root", "", "Project root containing run_pipeline.py and preview_extractor.go (default: $LUMA_REPO_ROOT, $LIVEHOUSE_REPO_ROOT, or cwd)")
	sdMount := flag.String("sd-mount", "", "If set, incremental SD scanner with SQLite luma brain (mounted card path, e.g. /Volumes/CAMERA_SD). Legacy archive polling is disabled.")
	brainDB := flag.String("brain-db", "luma_brain.db", "SQLite ledger path for --sd-mount (relative paths resolve under --repo-root)")
	deviceID := flag.String("device-id", "", "Device id for SD mode (default: $DEVICE_ID or 'default'); omit flag to use env")
	galleryHook := flag.String("gallery-hook", "", "POST URL after new ARW ingest (default: $GALLERY_HOOK or $LIVEHOUSE_GALLERY_HOOK); omit flag to use env")
	flag.Parse()

	repoRoot := resolveRepoRoot(*repoRootFlag)
	deviceIDResolved := resolveDeviceID(*deviceID)
	galleryHookResolved := resolveGalleryHook(*galleryHook)
	applyStudioIngestCLIDefaults(repoRoot, sdMount, archiveRoot)

	poll := *pollSeconds
	if poll <= 0 {
		if strings.TrimSpace(*sdMount) != "" {
			poll = 30
		} else {
			poll = 5
		}
	}

	if strings.TrimSpace(*sdMount) != "" {
		bp := strings.TrimSpace(*brainDB)
		if !filepath.IsAbs(bp) {
			bp = filepath.Join(repoRoot, bp)
		}
		db, err := OpenLumaBrain(bp)
		if err != nil {
			log.Fatalf("luma brain: %v", err)
		}
		defer db.Close()
		runSDIngestLoop(db, strings.TrimSpace(*sdMount), *archiveRoot, deviceIDResolved, time.Duration(max(1, poll))*time.Second, *workers, repoRoot, galleryHookResolved)
		return
	}

	sf := *stateFile
	if strings.TrimSpace(sf) == "" {
		if s := strings.TrimSpace(*onlySession); s != "" {
			sf = defaultIngestStatePath(*archiveRoot, "ingest_worker_only_"+sanitizeForFilename(filepath.Base(s))+".json")
		} else {
			sf = defaultIngestStatePath(*archiveRoot, "ingest_worker_state.json")
		}
	}

	if err := os.MkdirAll(filepath.Dir(sf), 0755); err != nil {
		log.Fatalf("创建 state 目录失败: %v", err)
	}

	state := loadState(sf)
	resetStaleRunningSessions(state)
	saveState(sf, state)
	ticker := time.NewTicker(time.Duration(max(1, poll)) * time.Second)
	defer ticker.Stop()

	var stateMu sync.Mutex
	pipelineRunning := false

	if s := strings.TrimSpace(*onlySession); s != "" {
		log.Printf("ingest-worker ONLY-SESSION mode | target=%s | state=%s", s, sf)
	}
	log.Printf("ingest-worker started | archive=%s repo_root=%s poll=%ds stable=%ds workers=%d max_per_scan=%d", *archiveRoot, repoRoot, poll, *stableSeconds, *workers, *maxPerScan)
	log.Printf("pipeline template: %s", *pipelineCmd)
	for {
		if err := scanOnce(*archiveRoot, state, *stableSeconds, *workers, *pipelineCmd, *maxPerScan, *verbose, sf, &stateMu, &pipelineRunning, *onlySession, repoRoot); err != nil {
			log.Printf("scan error: %v", err)
		}
		<-ticker.C
	}
}
