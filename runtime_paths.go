package main

import (
	"os"
	"path/filepath"
)

const runtimeDirName = "runtime"
const legacyRuntimeDirName = ".runtime"

func archiveRuntimeDir(archiveRoot string) string {
	return filepath.Join(archiveRoot, runtimeDirName)
}

func resolveLatestSessionRefPath(archiveRoot string) string {
	newPath := filepath.Join(archiveRoot, runtimeDirName, "latest_session.json")
	if _, err := os.Stat(newPath); err == nil {
		return newPath
	}
	legPath := filepath.Join(archiveRoot, legacyRuntimeDirName, "latest_session.json")
	if _, err := os.Stat(legPath); err == nil {
		return legPath
	}
	return newPath
}

func defaultIngestStatePath(archiveRoot, basename string) string {
	return filepath.Join(archiveRuntimeDir(archiveRoot), basename)
}
