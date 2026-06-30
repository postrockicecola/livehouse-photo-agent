package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// StudioIngestConfig is persisted by Studio at configs/studio_ingest.json (repo root).
type StudioIngestConfig struct {
	IngestMonitorPath string `json:"ingest_monitor_path"`
	ArchiveRoot       string `json:"archive_root"`
	SessionFolderName string `json:"session_folder_name"`
	UpdatedAt         int64  `json:"updated_at"`
}

func studioIngestConfigPath(repoRoot string) string {
	return filepath.Join(repoRoot, "configs", "studio_ingest.json")
}

func loadStudioIngestConfig(repoRoot string) (StudioIngestConfig, bool) {
	p := studioIngestConfigPath(repoRoot)
	raw, err := os.ReadFile(p)
	if err != nil {
		return StudioIngestConfig{}, false
	}
	var cfg StudioIngestConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		logWarnf("studio_ingest.json parse error: %v", err)
		return StudioIngestConfig{}, false
	}
	cfg.IngestMonitorPath = strings.TrimSpace(cfg.IngestMonitorPath)
	cfg.ArchiveRoot = strings.TrimSpace(cfg.ArchiveRoot)
	cfg.SessionFolderName = strings.TrimSpace(cfg.SessionFolderName)
	return cfg, true
}

// applyStudioIngestCLIDefaults fills empty --sd-mount / default archive-root from Studio config.
func applyStudioIngestCLIDefaults(repoRoot string, sdMount, archiveRoot *string) {
	cfg, ok := loadStudioIngestConfig(repoRoot)
	if !ok {
		return
	}
	if strings.TrimSpace(*sdMount) == "" && cfg.IngestMonitorPath != "" {
		*sdMount = cfg.IngestMonitorPath
	}
	if cfg.ArchiveRoot != "" {
		if st, err := os.Stat(cfg.ArchiveRoot); err == nil && st.IsDir() {
			*archiveRoot = cfg.ArchiveRoot
		}
	}
}

func studioSessionFolderName(repoRoot string, candidates []arwCandidate) string {
	cfg, ok := loadStudioIngestConfig(repoRoot)
	if ok && cfg.SessionFolderName != "" {
		return cfg.SessionFolderName
	}
	key := majorityExifSessionDate(candidates)
	if key != "" && key != "Unknown_Date" {
		return key
	}
	return ""
}
