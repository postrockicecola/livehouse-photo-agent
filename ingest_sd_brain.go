package main

import (
	"database/sql"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type arwCandidate struct {
	Path string
	Name string
	Size int64
	Mod  int64
}

func runSDIngestLoop(
	db *sql.DB,
	sdMount string,
	archiveRoot string,
	deviceID string,
	poll time.Duration,
	workers int,
	repoRoot string,
	galleryHook string,
) {
	logScanf("SD+SQLite mode | sd=%s archive=%s device=%s poll=%s",
		sdMount, archiveRoot, deviceID, poll)

	ticker := time.NewTicker(poll)
	defer ticker.Stop()

	for {
		_, err := sdScanOnce(db, sdMount, archiveRoot, deviceID, workers, repoRoot, galleryHook)
		if err != nil {
			logErrf("sd scan error: %v", err)
		}
		<-ticker.C
	}
}

func sdScanOnce(
	db *sql.DB,
	sdMount string,
	archiveRoot string,
	deviceID string,
	workers int,
	repoRoot string,
	galleryHook string,
) (ingested int, err error) {
	sdMount = strings.TrimSpace(sdMount)
	if sdMount == "" {
		return 0, fmt.Errorf("sd-mount empty")
	}
	if _, statErr := os.Stat(sdMount); statErr != nil {
		// SD unplugged or path missing — stay quiet (no per-tick spam).
		return 0, nil
	}

	cands, err := listARWWithPool(sdMount, max(4, workers))
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}
	if len(cands) == 0 {
		return 0, nil
	}

	// Split the card into per-day sessions by each file's own EXIF date so a
	// mixed card (e.g. yesterday + today) never collapses into one majority-date
	// folder. A manual studio override still forces a single session.
	groups := groupCandidatesBySession(repoRoot, cands)
	var total int
	for _, key := range sortedSessionKeys(groups) {
		n, err := ingestSessionGroup(db, key, groups[key], archiveRoot, deviceID, workers, repoRoot, galleryHook)
		total += n
		if err != nil {
			return total, err
		}
	}
	return total, nil
}

// groupCandidatesBySession maps sessionKey -> candidates. When Studio pins a
// session_folder_name, all candidates go into that single folder; otherwise
// candidates are grouped by their individual EXIF CreateDate (unknown -> today).
func groupCandidatesBySession(repoRoot string, cands []arwCandidate) map[string][]arwCandidate {
	groups := make(map[string][]arwCandidate)
	if cfg, ok := loadStudioIngestConfig(repoRoot); ok && cfg.SessionFolderName != "" {
		groups[cfg.SessionFolderName] = cands
		return groups
	}

	paths := make([]string, 0, len(cands))
	for _, c := range cands {
		if c.Path != "" {
			paths = append(paths, c.Path)
		}
	}
	dates := batchCreateDates(paths, 80)
	today := time.Now().Format("2006-01-02")
	for _, c := range cands {
		key := dates[c.Path]
		if key == "" || key == "Unknown_Date" || key == "-" {
			key = today
		}
		groups[key] = append(groups[key], c)
	}
	return groups
}

func sortedSessionKeys(groups map[string][]arwCandidate) []string {
	keys := make([]string, 0, len(groups))
	for k := range groups {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func ingestSessionGroup(
	db *sql.DB,
	sessionKey string,
	cands []arwCandidate,
	archiveRoot string,
	deviceID string,
	workers int,
	repoRoot string,
	galleryHook string,
) (int, error) {
	if len(cands) == 0 {
		return 0, nil
	}
	sessionDir := filepath.Join(archiveRoot, sessionKey)
	rawDir := filepath.Join(sessionDir, "RAW")
	previewsDir := filepath.Join(sessionDir, "Previews")
	sid, err := GetOrCreateSession(db, archiveRoot, deviceID, sessionKey, sessionDir, rawDir, previewsDir)
	if err != nil {
		return 0, err
	}

	var nNew int
	for _, c := range cands {
		h := FingerprintARW(c.Name, c.Size, c.Mod)
		st, found, err := LookupPhotoStatus(db, h)
		if err != nil {
			return nNew, err
		}
		if found && photoStatusShouldSkipIngest(st) {
			continue
		}
		if found {
			// Stale NEW or unexpected row — avoid duplicate hash insert.
			continue
		}

		dest := filepath.Join(rawDir, c.Name)
		if fi, e := os.Stat(dest); e == nil {
			if fi.Size() == c.Size && fi.ModTime().Unix() == c.Mod {
				inserted, err := EnsureIngestedRow(db, h, dest, deviceID, sid)
				if err != nil {
					return nNew, err
				}
				if inserted {
					nNew++
				}
				continue
			}
			stem := strings.TrimSuffix(c.Name, filepath.Ext(c.Name))
			dest = filepath.Join(rawDir, fmt.Sprintf("%s_%s.arw", stem, h[:8]))
		}

		if err := IngestNewARW(db, h, dest, deviceID, sid, c.Path); err != nil {
			if isBusyOrIO(err) {
				logWarnf("ingest skip (io): %s: %v", c.Path, err)
				continue
			}
			return nNew, err
		}
		logScanf("new ARW ingested: %s -> %s", c.Path, dest)
		nNew++
	}

	if nNew == 0 {
		return 0, nil
	}

	if err := os.MkdirAll(previewsDir, 0755); err != nil {
		return nNew, err
	}
	logExef("run preview_extractor for session %s", filepath.Base(sessionDir))
	if err := runPreviewExtractor(repoRoot, sessionDir, workers); err != nil {
		logWarnf("preview_extractor failed (raw archived): %v", err)
	}

	if err := writeLatestSessionRef(archiveRoot, sessionDir); err != nil {
		logWarnf("latest_session.json: %v", err)
	}

	hook := strings.TrimSpace(galleryHook)
	if hook != "" {
		go triggerGalleryCheck(hook, 25*time.Second)
	}

	return nNew, nil
}

func isBusyOrIO(err error) bool {
	if err == nil {
		return false
	}
	s := err.Error()
	return strings.Contains(s, "resource busy") || strings.Contains(s, "permission denied") ||
		strings.Contains(s, "input/output") || strings.Contains(s, "no space left")
}

func listARWWithPool(root string, workers int) ([]arwCandidate, error) {
	var paths []string
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() {
			name := d.Name()
			if path != root && strings.HasPrefix(name, ".") {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasPrefix(d.Name(), "._") {
			return nil
		}
		if !strings.EqualFold(filepath.Ext(d.Name()), ".ARW") {
			return nil
		}
		paths = append(paths, path)
		return nil
	})
	if err != nil {
		return nil, err
	}

	if len(paths) == 0 {
		return nil, nil
	}

	w := max(1, workers)
	sem := make(chan struct{}, w)
	var wg sync.WaitGroup
	var mu sync.Mutex
	out := make([]arwCandidate, 0, len(paths))

	for _, p := range paths {
		p := p
		wg.Add(1)
		sem <- struct{}{}
		go func() {
			defer wg.Done()
			defer func() { <-sem }()
			fi, err := os.Stat(p)
			if err != nil {
				return
			}
			if !fi.Mode().IsRegular() {
				return
			}
			mu.Lock()
			out = append(out, arwCandidate{
				Path: p,
				Name: filepath.Base(p),
				Size: fi.Size(),
				Mod:  fi.ModTime().Unix(),
			})
			mu.Unlock()
		}()
	}
	wg.Wait()
	return out, nil
}

func runPreviewExtractor(repoRoot, sessionDir string, workers int) error {
	cmd := execCommandPreviewExtractor(repoRoot, sessionDir, workers)
	cmd.Stdout = nil
	cmd.Stderr = nil
	return cmd.Run()
}

func execCommandPreviewExtractor(repoRoot, sessionDir string, workers int) *exec.Cmd {
	c := exec.Command("go", "run", "-tags=tools", "preview_extractor.go", "--base-dir", sessionDir, "--workers", fmt.Sprintf("%d", workers))
	c.Dir = repoRoot
	return c
}

// triggerGalleryCheck fires the recommended main-path hook (POST check_new_images →
// process_brain_ingested → seed jobs → tasks.run_job). Not used by legacy mode-A pipeline subprocess.
func triggerGalleryCheck(url string, timeout time.Duration) {
	client := &http.Client{Timeout: timeout}
	req, err := http.NewRequest(http.MethodPost, url, nil)
	if err != nil {
		return
	}
	req.Header.Set("User-Agent", "livehouse-ingest-worker/1")
	resp, err := client.Do(req)
	if err != nil {
		logWarnf("gallery hook failed: %v", err)
		return
	}
	_ = resp.Body.Close()
}
