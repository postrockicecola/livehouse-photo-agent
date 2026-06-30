package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// scanOnce discovers sessions, updates JSON state, and may start one pipeline run in a goroutine.
func scanOnce(archiveRoot string, st *WorkerState, stableSeconds int, workers int, pipelineCmd string, maxPerScan int, verbose bool, stateFile string, stateMu *sync.Mutex, pipelineRunning *bool, onlySession string, repoRoot string) error {
	stateMu.Lock()
	var sessionDirs []string
	var err error
	if strings.TrimSpace(onlySession) != "" {
		sessionDirs, err = resolveOnlySessionDir(archiveRoot, onlySession)
	} else {
		sessionDirs, err = findSessionDirs(archiveRoot)
	}
	if err != nil {
		stateMu.Unlock()
		return err
	}
	sortSessionDirsNewestFirst(sessionDirs)
	now := time.Now()

	var candidates []string
	for _, sessionDir := range sessionDirs {
		snap, err := snapshotSession(sessionDir)
		if err != nil {
			logWarnf("snapshot failed %s: %v", sessionDir, err)
			continue
		}
		if verbose {
			logScanf("%s | images=%d bytes=%d", filepath.Base(sessionDir), snap.RawCount, snap.RawBytes)
		}
		if snap.RawCount == 0 {
			continue
		}

		ss, ok := st.Sessions[sessionDir]
		if !ok {
			logScanf("new session discovered: %s", filepath.Base(sessionDir))
			ss = &SessionState{
				Status:         "pending",
				LastChangeUnix: now.Unix(),
			}
			st.Sessions[sessionDir] = ss
		}

		changed := ss.RawCount != snap.RawCount || ss.RawBytes != snap.RawBytes || ss.LastRawModUnix != snap.LastRawModUnix
		if changed {
			ss.RawCount = snap.RawCount
			ss.RawBytes = snap.RawBytes
			ss.LastRawModUnix = snap.LastRawModUnix
			ss.LastChangeUnix = now.Unix()
			if ss.Status == "done" || ss.Status == "failed" {
				ss.Status = "pending"
				ss.LastError = ""
			}
		}

		ss.UpdatedAt = now
		stableFor := now.Unix() - ss.LastChangeUnix
		if ss.Status == "pending" && stableFor >= int64(stableSeconds) {
			candidates = append(candidates, sessionDir)
		}
	}

	sortSessionDirsNewestFirst(candidates)
	if len(candidates) > 0 {
		logScanf("pending+stable=%d newest_first=%s", len(candidates), filepath.Base(candidates[0]))
	}

	busy := *pipelineRunning
	if len(candidates) > 0 && busy {
		logWarnf("pipeline busy; %d pending+stable queued (next: %s)", len(candidates), filepath.Base(candidates[0]))
	}
	limit := maxPerScan
	if limit == 0 || limit > len(candidates) {
		limit = len(candidates)
	}
	var toRun string
	var toRunSS *SessionState
	if !busy && len(candidates) > 0 && limit > 0 {
		toRun = candidates[0]
		toRunSS = st.Sessions[toRun]
		*pipelineRunning = true
	}
	saveState(stateFile, st)
	stateMu.Unlock()

	if toRun != "" {
		stableFor := now.Unix() - toRunSS.LastChangeUnix
		logSuccessf("stable session detected: %s | raw=%d size=%.2fGB stable_for=%ds — starting executor",
			toRun, toRunSS.RawCount, float64(toRunSS.RawBytes)/(1024*1024*1024), stableFor)
		sd := toRun
		sess := toRunSS
		go func() {
			defer func() {
				stateMu.Lock()
				*pipelineRunning = false
				saveState(stateFile, st)
				stateMu.Unlock()
			}()
			if err := runSession(sd, sess, workers, pipelineCmd, archiveRoot, stateMu, repoRoot); err != nil {
				logErrf("runSession: %v", err)
			}
		}()
	}
	return nil
}

// resolveRawDir picks RAW / Raw / raw (Linux/macOS case differences).
func resolveRawDir(sessionDir string) string {
	for _, sub := range []string{"RAW", "Raw", "raw"} {
		p := filepath.Join(sessionDir, sub)
		if info, err := os.Stat(p); err == nil && info.IsDir() {
			return p
		}
	}
	return ""
}

// effectiveRawDirForARW returns the directory that actually holds .ARW files:
// prefer RAW/ subfolder if it has ARW; else session root if flat ARW layout.
func effectiveRawDirForARW(sessionDir string) string {
	if rd := resolveRawDir(sessionDir); rd != "" && countARWFiles(rd) > 0 {
		return rd
	}
	if countARWFiles(sessionDir) > 0 {
		return sessionDir
	}
	if rd := resolveRawDir(sessionDir); rd != "" {
		return rd
	}
	return ""
}

func countARWFiles(rawDir string) int {
	if rawDir == "" {
		return 0
	}
	entries, err := os.ReadDir(rawDir)
	if err != nil {
		return 0
	}
	n := 0
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := strings.ToUpper(e.Name())
		if strings.HasSuffix(name, ".ARW") {
			n++
		}
	}
	return n
}

func snapshotFromARWDir(rawDir string) (*RawSnapshot, error) {
	entries, err := os.ReadDir(rawDir)
	if err != nil {
		return nil, err
	}
	s := &RawSnapshot{}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := strings.ToUpper(e.Name())
		if !strings.HasSuffix(name, ".ARW") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		s.RawCount++
		s.RawBytes += info.Size()
		mod := info.ModTime().Unix()
		if mod > s.LastRawModUnix {
			s.LastRawModUnix = mod
		}
	}
	return s, nil
}

func snapshotFromPreviewsDir(previewsDir string) (*RawSnapshot, error) {
	entries, err := os.ReadDir(previewsDir)
	if err != nil {
		return nil, err
	}
	s := &RawSnapshot{}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := strings.ToLower(e.Name())
		if !(strings.HasSuffix(name, ".jpg") || strings.HasSuffix(name, ".jpeg") || strings.HasSuffix(name, ".png") || strings.HasSuffix(name, ".webp")) {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		s.RawCount++
		s.RawBytes += info.Size()
		mod := info.ModTime().Unix()
		if mod > s.LastRawModUnix {
			s.LastRawModUnix = mod
		}
	}
	return s, nil
}

func snapshotSession(sessionDir string) (*RawSnapshot, error) {
	if rd := resolveRawDir(sessionDir); rd != "" {
		s, err := snapshotFromARWDir(rd)
		if err != nil {
			return nil, err
		}
		if s != nil && s.RawCount > 0 {
			return s, nil
		}
	}
	if s, err := snapshotFromARWDir(sessionDir); err != nil {
		return nil, err
	} else if s != nil && s.RawCount > 0 {
		return s, nil
	}
	return snapshotFromPreviewsDir(filepath.Join(sessionDir, "Previews"))
}

func findSessionDirs(archiveRoot string) ([]string, error) {
	entries, err := os.ReadDir(archiveRoot)
	if err != nil {
		return nil, err
	}
	var out []string
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		name := e.Name()
		if name == ".runtime" || name == "runtime" || strings.HasPrefix(name, ".") {
			continue
		}
		sessionDir := filepath.Join(archiveRoot, e.Name())
		if resolveRawDir(sessionDir) != "" {
			out = append(out, sessionDir)
			continue
		}
		if countARWFiles(sessionDir) > 0 {
			out = append(out, sessionDir)
			continue
		}
		prev := filepath.Join(sessionDir, "Previews")
		if n, _ := countPreviewImages(prev); n > 0 {
			out = append(out, sessionDir)
		}
	}
	return out, nil
}

// sortSessionDirsNewestFirst: prefer newer calendar date from folder name (YYYY-MM-DD prefix),
// then directory mtime. Avoids ReadDir alphabetical order (2026-01-30 before 2026-04-10).
func sortSessionDirsNewestFirst(dirs []string) {
	sort.Slice(dirs, func(i, j int) bool {
		di := sessionDateKey(dirs[i])
		dj := sessionDateKey(dirs[j])
		if di != dj {
			return di > dj
		}
		return sessionDirNewestKey(dirs[i]) > sessionDirNewestKey(dirs[j])
	})
}

// sessionDateKey parses leading YYYY-MM-DD from folder base name (e.g. 2026-04-10 or 2026-04-10_sample).
func sessionDateKey(dir string) int64 {
	base := filepath.Base(dir)
	if len(base) >= 10 && base[4] == '-' && base[7] == '-' {
		t, err := time.Parse("2006-01-02", base[:10])
		if err == nil {
			return t.Unix()
		}
	}
	return 0
}

func sessionDirNewestKey(dir string) int64 {
	info, err := os.Stat(dir)
	if err != nil {
		return 0
	}
	return info.ModTime().Unix()
}

// resolveOnlySessionDir returns exactly one session directory (absolute path) for --only-session.
func resolveOnlySessionDir(archiveRoot, only string) ([]string, error) {
	only = strings.TrimSpace(only)
	if only == "" {
		return nil, fmt.Errorf("only-session: empty")
	}
	var target string
	if filepath.IsAbs(only) {
		target = filepath.Clean(only)
	} else {
		target = filepath.Join(archiveRoot, filepath.Clean(only))
	}
	fi, err := os.Stat(target)
	if err != nil {
		return nil, fmt.Errorf("only-session: %w", err)
	}
	if !fi.IsDir() {
		return nil, fmt.Errorf("only-session: not a directory: %s", target)
	}
	return []string{target}, nil
}

func countPreviewImages(previewsDir string) (int, error) {
	entries, err := os.ReadDir(previewsDir)
	if err != nil {
		return 0, err
	}
	n := 0
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := strings.ToLower(e.Name())
		if strings.HasSuffix(name, ".jpg") || strings.HasSuffix(name, ".jpeg") || strings.HasSuffix(name, ".png") || strings.HasSuffix(name, ".webp") {
			n++
		}
	}
	return n, nil
}
