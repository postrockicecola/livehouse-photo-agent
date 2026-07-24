package main

import (
	"path/filepath"
)

const runtimeDirName = "runtime"

func archiveRuntimeDir(archiveRoot string) string {
	return filepath.Join(archiveRoot, runtimeDirName)
}

func defaultIngestStatePath(archiveRoot, basename string) string {
	return filepath.Join(archiveRuntimeDir(archiveRoot), basename)
}
