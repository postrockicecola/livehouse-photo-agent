package main

import (
	"path/filepath"
	"testing"
)

func TestPreviewExtractorCommandUsesConfiguredBinary(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "preview-extractor")
	t.Setenv("LIVEHOUSE_PREVIEW_EXTRACTOR", binary)

	cmd := previewExtractorCommand("/repo", "/archive/session", 3)
	if cmd.Path != binary {
		t.Fatalf("path=%q, want %q", cmd.Path, binary)
	}
	want := []string{
		binary,
		"--base-dir",
		"/archive/session",
		"--workers",
		"3",
	}
	if len(cmd.Args) != len(want) {
		t.Fatalf("args=%v, want %v", cmd.Args, want)
	}
	for i := range want {
		if cmd.Args[i] != want[i] {
			t.Fatalf("args[%d]=%q, want %q", i, cmd.Args[i], want[i])
		}
	}
}
