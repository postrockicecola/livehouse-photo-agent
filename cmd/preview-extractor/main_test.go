package main

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestAtomicWriteFileDoesNotPublishPartialOutput(t *testing.T) {
	dir := t.TempDir()
	dst := filepath.Join(dir, "preview.jpg")
	if err := os.WriteFile(dst, []byte("old"), 0644); err != nil {
		t.Fatal(err)
	}

	wantErr := errors.New("disk write failed")
	err := atomicWriteFile(dst, func(out *os.File) error {
		if _, writeErr := out.Write([]byte("partial")); writeErr != nil {
			return writeErr
		}
		return wantErr
	})
	if !errors.Is(err, wantErr) {
		t.Fatalf("got error %v, want %v", err, wantErr)
	}
	got, err := os.ReadFile(dst)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "old" {
		t.Fatalf("published partial output: %q", got)
	}
	matches, err := filepath.Glob(filepath.Join(dir, ".preview.jpg.tmp-*"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 0 {
		t.Fatalf("temporary files leaked: %v", matches)
	}
}

func TestAtomicWriteFilePublishesCompleteOutput(t *testing.T) {
	dst := filepath.Join(t.TempDir(), "preview.jpg")
	err := atomicWriteFile(dst, func(out *os.File) error {
		_, err := out.Write([]byte("complete"))
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(dst)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "complete" {
		t.Fatalf("got %q", got)
	}
}
