package main

import (
	"os"
	"path/filepath"
	"strings"
)

// resolveRepoRoot returns --repo-root, LUMA_REPO_ROOT, LIVEHOUSE_REPO_ROOT, cwd, or ".".
func resolveRepoRoot(flagValue string) string {
	s := strings.TrimSpace(flagValue)
	if s != "" {
		return filepath.Clean(expandTilde(s))
	}
	if e := strings.TrimSpace(os.Getenv("LUMA_REPO_ROOT")); e != "" {
		return filepath.Clean(expandTilde(e))
	}
	if e := strings.TrimSpace(os.Getenv("LIVEHOUSE_REPO_ROOT")); e != "" {
		return filepath.Clean(expandTilde(e))
	}
	wd, err := os.Getwd()
	if err == nil {
		return wd
	}
	return "."
}
