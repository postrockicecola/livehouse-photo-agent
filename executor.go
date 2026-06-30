package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// runSession runs preview extraction (if ARW present), then optional pipeline shell command.
//
// Legacy ingest path (mode A): when pipelineCmd is set, runs e.g. run_pipeline.py subprocess —
// bypasses SQLite jobs and POST /api/ingest/check_new_images. Recommended production path is
// SD brain + gallery hook → process_brain_ingested → tasks.run_job (see README / ARCH_MAP).
//
// Updates ss.Status and returns a non-nil error on failure paths (state is still persisted by caller patterns).
func runSession(sessionDir string, ss *SessionState, workers int, pipelineCmd string, archiveRoot string, stateMu *sync.Mutex, repoRoot string) error {
	sessionStart := time.Now()
	stateMu.Lock()
	ss.Status = "running"
	ss.LastRunUnix = time.Now().Unix()
	ss.LastError = ""
	stateMu.Unlock()

	logExef("run preview extraction: %s", sessionDir)

	arwN := countARWFiles(effectiveRawDirForARW(sessionDir))
	if arwN > 0 {
		cmd := exec.Command("go", "run", "-tags=tools", "preview_extractor.go", "--base-dir", sessionDir, "--workers", fmt.Sprintf("%d", workers))
		cmd.Dir = repoRoot
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			stateMu.Lock()
			ss.Status = "failed"
			ss.LastError = fmt.Sprintf("preview_extractor failed: %v", err)
			stateMu.Unlock()
			logErrf("%s", ss.LastError)
			return fmt.Errorf("preview_extractor: %w", err)
		}
	} else {
		logWarnf("skip preview_extractor (no .ARW in RAW/Raw): %s — using existing Previews if any", sessionDir)
	}

	previewCount, err := countPreviewImages(filepath.Join(sessionDir, "Previews"))
	if err != nil {
		stateMu.Lock()
		ss.Status = "failed"
		ss.LastError = fmt.Sprintf("count previews failed: %v", err)
		stateMu.Unlock()
		logErrf("%s", ss.LastError)
		return fmt.Errorf("count previews: %w", err)
	}
	if previewCount == 0 {
		stateMu.Lock()
		ss.Status = "failed"
		ss.LastError = "no preview images found after extraction; skip pipeline"
		stateMu.Unlock()
		logErrf("%s", ss.LastError)
		return fmt.Errorf("no preview images")
	}
	logExef("previews ready: %d", previewCount)

	if strings.TrimSpace(pipelineCmd) != "" {
		cmdText := expandPipelineCommand(pipelineCmd, archiveRoot, sessionDir)
		logExef("starting Python pipeline: %s", cmdText)
		pc := exec.Command("sh", "-c", cmdText)
		pc.Dir = repoRoot
		pc.Stdout = os.Stdout
		pc.Stderr = os.Stderr
		if err := pc.Run(); err != nil {
			stateMu.Lock()
			ss.Status = "failed"
			ss.LastError = fmt.Sprintf("pipeline_cmd failed: %v", err)
			stateMu.Unlock()
			logErrf("%s", ss.LastError)
			return fmt.Errorf("pipeline_cmd: %w", err)
		}
		logSuccessf("Python pipeline finished OK for %s", filepath.Base(sessionDir))
	}

	stateMu.Lock()
	ss.Status = "done"
	stateMu.Unlock()
	if err := writeLatestSessionRef(archiveRoot, sessionDir); err != nil {
		logWarnf("write latest session ref failed: %v", err)
	}
	elapsed := time.Since(sessionStart)
	n := previewCount
	if n > 0 {
		avgSec := elapsed.Seconds() / float64(n)
		logExef("session stats: %s | images=%d | wall time=%.1fs | avg per image=%.2fs",
			filepath.Base(sessionDir), n, elapsed.Seconds(), avgSec)
	} else {
		logExef("session stats: %s | images=0 | wall time=%.1fs", filepath.Base(sessionDir), elapsed.Seconds())
	}
	logSuccessf("session done: %s", sessionDir)
	return nil
}

func expandPipelineCommand(template, archiveRoot, sessionDir string) string {
	rawDir := effectiveRawDirForARW(sessionDir)
	if rawDir == "" {
		rawDir = filepath.Join(sessionDir, "RAW")
	}
	previewsDir := filepath.Join(sessionDir, "Previews")
	sessionName := filepath.Base(sessionDir)
	r := strings.NewReplacer(
		"{archive_root}", shQuote(archiveRoot),
		"{session_dir}", shQuote(sessionDir),
		"{raw_dir}", shQuote(rawDir),
		"{previews_dir}", shQuote(previewsDir),
		"{session_name}", shQuote(sessionName),
	)
	return r.Replace(template)
}

func shQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
}
