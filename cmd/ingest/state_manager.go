// JSON persistence for legacy archive mode (ingest_worker_state.json) and latest_session.json.
// SQLite luma brain lives in luma_brain.go / ingest_sd_brain.go.
package main

import (
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// loadState reads JSON worker state; missing file yields empty map. Malformed JSON logs and yields empty map (same as before).
func loadState(path string) *WorkerState {
	st := &WorkerState{Sessions: map[string]*SessionState{}}
	b, err := os.ReadFile(path)
	if err != nil {
		return st
	}
	if err := json.Unmarshal(b, st); err != nil {
		log.Printf("state parse warning: %v", err)
		return st
	}
	if st.Sessions == nil {
		st.Sessions = map[string]*SessionState{}
	}
	return st
}

// saveState writes worker state JSON (best-effort; logs on failure).
func saveState(path string, st *WorkerState) {
	b, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		log.Printf("state marshal warning: %v", err)
		return
	}
	if err := os.WriteFile(path, b, 0644); err != nil {
		log.Printf("state write warning: %v", err)
	}
}

// resetStaleRunningSessions marks interrupted "running" sessions as pending (startup recovery).
func resetStaleRunningSessions(st *WorkerState) {
	for _, ss := range st.Sessions {
		if ss != nil && ss.Status == "running" {
			ss.Status = "pending"
			ss.LastError = ""
		}
	}
}

// sanitizeForFilename makes a safe fragment for state file names.
func sanitizeForFilename(s string) string {
	s = filepath.Base(strings.TrimSpace(s))
	if s == "" || s == "." {
		return "session"
	}
	var b strings.Builder
	for _, r := range s {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '-', r == '_':
			b.WriteRune(r)
		default:
			b.WriteRune('_')
		}
	}
	out := b.String()
	if out == "" {
		return "session"
	}
	return out
}

// writeLatestSessionRef writes Archive/runtime/latest_session.json for gallery_server.
func writeLatestSessionRef(archiveRoot, sessionDir string) error {
	refPath := filepath.Join(archiveRuntimeDir(archiveRoot), "latest_session.json")
	if err := os.MkdirAll(filepath.Dir(refPath), 0755); err != nil {
		return err
	}
	rd := effectiveRawDirForARW(sessionDir)
	if rd == "" {
		rd = filepath.Join(sessionDir, "RAW")
	}
	ref := LatestSessionRef{
		ArchiveRoot: archiveRoot,
		SessionDir:  sessionDir,
		RawDir:      rd,
		PreviewsDir: filepath.Join(sessionDir, "Previews"),
		UpdatedAt:   time.Now().Format(time.RFC3339),
	}
	b, err := json.MarshalIndent(ref, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(refPath, b, 0644)
}
