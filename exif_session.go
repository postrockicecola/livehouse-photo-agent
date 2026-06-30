package main

import (
	"os/exec"
	"strings"
)

// majorityExifSessionDate picks the most common YYYY-MM-DD CreateDate among ARW paths (exiftool batch).
func majorityExifSessionDate(cands []arwCandidate) string {
	if len(cands) == 0 {
		return ""
	}
	paths := make([]string, 0, len(cands))
	for _, c := range cands {
		if c.Path != "" {
			paths = append(paths, c.Path)
		}
	}
	dates := batchCreateDates(paths, 80)
	if len(dates) == 0 {
		return ""
	}
	counts := make(map[string]int)
	var best string
	var bestN int
	for _, d := range dates {
		if d == "" || d == "-" {
			d = "Unknown_Date"
		}
		counts[d]++
		if counts[d] > bestN {
			bestN = counts[d]
			best = d
		}
	}
	return best
}

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
		args := append([]string{"-m", "-q", "-s3", "-CreateDate", "-d", "%Y-%m-%d"}, batch...)
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
