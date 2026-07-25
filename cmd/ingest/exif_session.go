package main

import (
	"os/exec"
	"strings"
)

// batchCreateDates maps each file path → YYYY-MM-DD from EXIF CreateDate (organizer-compatible).
func batchCreateDates(paths []string, batchSize int) map[string]string {
	out := make(map[string]string, len(paths))
	if batchSize < 1 {
		batchSize = 80
	}
	for i := 0; i < len(paths); i += batchSize {
		end := i + batchSize
		if end > len(paths) {
			end = len(paths)
		}
		batch := paths[i:end]
		// -f forces one output line per file ("-" when tag missing) so the
		// positional line->path mapping below stays aligned even for files
		// without a readable CreateDate.
		args := append([]string{"-m", "-q", "-f", "-s3", "-CreateDate", "-d", "%Y-%m-%d"}, batch...)
		raw, err := exec.Command("exiftool", args...).Output()
		if err != nil {
			for _, p := range batch {
				out[p] = "Unknown_Date"
			}
			continue
		}
		lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
		for k, p := range batch {
			dateDirName := "Unknown_Date"
			if k < len(lines) {
				if d := strings.TrimSpace(lines[k]); d != "" && d != "-" {
					dateDirName = d
				}
			}
			out[p] = dateDirName
		}
	}
	return out
}
