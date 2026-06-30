package main

import "time"

// SessionState is persisted in ingest_worker_state.json (legacy archive mode).
type SessionState struct {
	RawCount       int       `json:"raw_count"`
	RawBytes       int64     `json:"raw_bytes"`
	LastRawModUnix int64     `json:"last_raw_mod_unix"`
	LastChangeUnix int64     `json:"last_change_unix"`
	Status         string    `json:"status"` // pending | running | done | failed
	LastError      string    `json:"last_error,omitempty"`
	LastRunUnix    int64     `json:"last_run_unix,omitempty"`
	UpdatedAt      time.Time `json:"updated_at"`
}

// WorkerState holds all session rows keyed by absolute session directory path.
type WorkerState struct {
	Sessions map[string]*SessionState `json:"sessions"`
}

// LatestSessionRef is written to Archive/runtime/latest_session.json for Python gallery.
type LatestSessionRef struct {
	ArchiveRoot string `json:"archive_root"`
	SessionDir  string `json:"session_dir"`
	RawDir      string `json:"raw_dir"`
	PreviewsDir string `json:"previews_dir"`
	UpdatedAt   string `json:"updated_at"`
}

// RawSnapshot summarizes RAW or Previews under a session directory (incremental compare).
type RawSnapshot struct {
	RawCount       int
	RawBytes       int64
	LastRawModUnix int64
}
