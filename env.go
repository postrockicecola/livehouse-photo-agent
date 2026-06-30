package main

import (
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/joho/godotenv"
)

// loadDotEnv loads project .env: first cwd, then optional <LUMA_REPO_ROOT>/.env and <LIVEHOUSE_REPO_ROOT>/.env (does not override existing env vars).
func loadDotEnv() {
	wd, err := os.Getwd()
	if err != nil {
		return
	}
	_ = godotenv.Load(filepath.Join(wd, ".env"))
	for _, key := range []string{"LUMA_REPO_ROOT", "LIVEHOUSE_REPO_ROOT"} {
		r := strings.TrimSpace(os.Getenv(key))
		if r == "" {
			continue
		}
		_ = godotenv.Load(filepath.Join(filepath.Clean(expandTilde(r)), ".env"))
	}
}

// defaultArchiveRoot returns the default --archive-root: $LUMA_ARCHIVE_ROOT if set (after .env),
// else when cwd is the repo (go.mod present) ../Livehouse_Archive (sibling of the repo on the same volume),
// else ~/Livehouse_Archive.
func defaultArchiveRoot() string {
	if v := strings.TrimSpace(os.Getenv("LUMA_ARCHIVE_ROOT")); v != "" {
		return filepath.Clean(expandTilde(v))
	}
	wd, err := os.Getwd()
	if err == nil {
		if _, err := os.Stat(filepath.Join(wd, "go.mod")); err == nil {
			return filepath.Clean(filepath.Join(wd, "..", "Livehouse_Archive"))
		}
	}
	home, err := os.UserHomeDir()
	if err != nil {
		log.Fatalf("cannot resolve user home directory: %v", err)
	}
	return filepath.Join(home, "Livehouse_Archive")
}

// expandTilde turns "~/foo" into $HOME/foo.
func expandTilde(path string) string {
	if strings.HasPrefix(path, "~/") {
		h, err := os.UserHomeDir()
		if err != nil {
			return path
		}
		return filepath.Join(h, path[2:])
	}
	return path
}

// resolveDeviceID: explicit --device-id wins; else DEVICE_ID env; else "default".
func resolveDeviceID(flagValue string) string {
	if s := strings.TrimSpace(flagValue); s != "" {
		return s
	}
	if s := strings.TrimSpace(os.Getenv("DEVICE_ID")); s != "" {
		return s
	}
	return "default"
}

// resolveGalleryHook: explicit --gallery-hook wins; else GALLERY_HOOK, then LIVEHOUSE_GALLERY_HOOK (compat).
func resolveGalleryHook(flagValue string) string {
	if s := strings.TrimSpace(flagValue); s != "" {
		return s
	}
	if s := strings.TrimSpace(os.Getenv("GALLERY_HOOK")); s != "" {
		return s
	}
	return strings.TrimSpace(os.Getenv("LIVEHOUSE_GALLERY_HOOK"))
}
