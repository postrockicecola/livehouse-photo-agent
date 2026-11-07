package main

import (
	"errors"
	"image"
	"image/color"
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

func TestOrientImageRotatesAccordingToEXIF(t *testing.T) {
	src := image.NewRGBA(image.Rect(0, 0, 2, 3))
	red := color.RGBA{R: 255, A: 255}
	blue := color.RGBA{B: 255, A: 255}
	src.Set(0, 0, red)
	src.Set(1, 2, blue)

	clockwise := orientImage(src, 6)
	if got := clockwise.Bounds().Size(); got.X != 3 || got.Y != 2 {
		t.Fatalf("orientation 6 size = %v, want 3x2", got)
	}
	if got := clockwise.At(2, 0); got != red {
		t.Fatalf("orientation 6 red pixel = %v", got)
	}
	if got := clockwise.At(0, 1); got != blue {
		t.Fatalf("orientation 6 blue pixel = %v", got)
	}

	counterClockwise := orientImage(src, 8)
	if got := counterClockwise.At(0, 1); got != red {
		t.Fatalf("orientation 8 red pixel = %v", got)
	}
	if got := counterClockwise.At(2, 0); got != blue {
		t.Fatalf("orientation 8 blue pixel = %v", got)
	}
}

func TestFindRawDirsSkipsGeneratedPreviews(t *testing.T) {
	session := t.TempDir()
	rawDir := filepath.Join(session, "RAW")
	previewDir := filepath.Join(session, "Previews")
	if err := os.MkdirAll(rawDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(previewDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(previewDir, "preview.jpg"), []byte("jpeg"), 0644); err != nil {
		t.Fatal(err)
	}

	got, err := findRawDirs(session)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0] != rawDir {
		t.Fatalf("findRawDirs() = %v, want [%s]", got, rawDir)
	}
}
