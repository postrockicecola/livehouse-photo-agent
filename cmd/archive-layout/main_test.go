package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestOrganizeSession(t *testing.T) {
	sessionDir := t.TempDir()
	for _, name := range []string{"A.ARW", "B.arw", "A.JPG", "B.jpeg", "notes.txt"} {
		if err := os.WriteFile(filepath.Join(sessionDir, name), []byte(name), 0644); err != nil {
			t.Fatal(err)
		}
	}

	preview, err := organizeSession(sessionDir, false)
	if err != nil {
		t.Fatal(err)
	}
	if preview.raw != 2 || preview.previews != 2 {
		t.Fatalf("unexpected preview stats: %+v", preview)
	}
	if _, err := os.Stat(filepath.Join(sessionDir, "A.ARW")); err != nil {
		t.Fatalf("dry run moved a file: %v", err)
	}

	applied, err := organizeSession(sessionDir, true)
	if err != nil {
		t.Fatal(err)
	}
	if applied.raw != 2 || applied.previews != 2 {
		t.Fatalf("unexpected apply stats: %+v", applied)
	}
	for _, path := range []string{
		filepath.Join(sessionDir, "RAW", "A.ARW"),
		filepath.Join(sessionDir, "RAW", "B.arw"),
		filepath.Join(sessionDir, "Previews", "A.JPG"),
		filepath.Join(sessionDir, "Previews", "B.jpeg"),
		filepath.Join(sessionDir, "notes.txt"),
	} {
		if _, err := os.Stat(path); err != nil {
			t.Errorf("expected %s: %v", path, err)
		}
	}
}

func TestOrganizeSessionDoesNotOverwrite(t *testing.T) {
	sessionDir := t.TempDir()
	rawDir := filepath.Join(sessionDir, "RAW")
	if err := os.Mkdir(rawDir, 0755); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(sessionDir, "same.ARW")
	target := filepath.Join(rawDir, "same.ARW")
	if err := os.WriteFile(source, []byte("source"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte("target"), 0644); err != nil {
		t.Fatal(err)
	}

	result, err := organizeSession(sessionDir, true)
	if err != nil {
		t.Fatal(err)
	}
	if result.skipped != 1 {
		t.Fatalf("unexpected stats: %+v", result)
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "target" {
		t.Fatalf("target overwritten: %q", got)
	}
	if _, err := os.Stat(source); err != nil {
		t.Fatalf("source removed after skip: %v", err)
	}
}
