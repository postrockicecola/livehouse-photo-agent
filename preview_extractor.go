//go:build tools

package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/joho/godotenv"
)

func loadDotEnvPreview() {
	wd, err := os.Getwd()
	if err != nil {
		return
	}
	_ = godotenv.Load(filepath.Join(wd, ".env"))
}

// defaultBaseDir matches env.defaultArchiveRoot logic (this file is built standalone with go run preview_extractor.go).
func defaultBaseDir() string {
	if v := strings.TrimSpace(os.Getenv("LUMA_ARCHIVE_ROOT")); v != "" {
		p := v
		if strings.HasPrefix(p, "~/") {
			if h, err := os.UserHomeDir(); err == nil {
				p = filepath.Join(h, p[2:])
			}
		}
		return filepath.Clean(p)
	}
	wd, err := os.Getwd()
	if err == nil {
		if _, err := os.Stat(filepath.Join(wd, "go.mod")); err == nil {
			return filepath.Clean(filepath.Join(wd, "..", "Livehouse_Archive"))
		}
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "."
	}
	return filepath.Join(home, "Livehouse_Archive")
}

func main() {
	loadDotEnvPreview()
	baseDir := flag.String("base-dir", defaultBaseDir(), "Archive root or a single session dir")
	workers := flag.Int("workers", max(2, runtime.NumCPU()/2), "Concurrent extraction workers")
	flag.Parse()

	rawDirs, err := findRawDirs(*baseDir)
	if err != nil {
		log.Fatalf("扫描目录失败: %v", err)
	}
	if len(rawDirs) == 0 {
		log.Printf("未找到 RAW 目录: %s", *baseDir)
		return
	}

	var wg sync.WaitGroup
	concurrencyLimit := max(1, *workers)
	semaphore := make(chan struct{}, concurrencyLimit)

	total := int64(0)
	ok := int64(0)
	fail := int64(0)

	for _, rawPath := range rawDirs {
		previewPath := previewsDirForRawPath(rawPath)

		if err := os.MkdirAll(previewPath, 0755); err != nil {
			log.Printf("创建目录失败 %s: %v", previewPath, err)
			continue
		}

		sessionName := sessionNameForRawPath(rawPath)
		fmt.Printf("🚀 开始处理场次: %s\n", sessionName)

		files, err := os.ReadDir(rawPath)
		if err != nil {
			log.Printf("读取 RAW 目录失败 %s: %v", rawPath, err)
			continue
		}
		for _, file := range files {
			ext := strings.ToLower(filepath.Ext(file.Name()))
			if file.IsDir() || (!isJPEGExt(ext) && !isRAWExt(ext)) {
				continue
			}
				atomic.AddInt64(&total, 1)
				wg.Add(1)
				semaphore <- struct{}{}

				go func(fName, srcDir, dstDir, fileExt string) {
					defer wg.Done()
					defer func() { <-semaphore }()

					srcFile := filepath.Join(srcDir, fName)
					dstName := fName
					if isRAWExt(fileExt) {
						dstName = strings.TrimSuffix(fName, filepath.Ext(fName)) + ".jpg"
					}
					dstFile := filepath.Join(dstDir, dstName)

					if _, err := os.Stat(dstFile); err == nil {
						atomic.AddInt64(&ok, 1)
						return 
					}

					var err error
					if isJPEGExt(fileExt) {
						err = copyFile(srcFile, dstFile)
					} else {
						err = extractPreviewJPEG(srcFile, dstFile)
					}
					if err != nil {
						log.Printf("提取失败 %s: %v", fName, err)
						atomic.AddInt64(&fail, 1)
						return
					}
					atomic.AddInt64(&ok, 1)
				}(file.Name(), rawPath, previewPath, ext)
		}
	}

	wg.Wait()
	fmt.Printf("📊 预览提取统计: total=%d ok=%d fail=%d workers=%d\n", total, ok, fail, concurrencyLimit)
	fmt.Println("\n✨ 所有预览图提取完成！你可以去 Previews 文件夹里快速选片了。")
}

// previewsDirForRawPath: .../Session/RAW -> .../Session/Previews; flat .../Session -> .../Session/Previews
func previewsDirForRawPath(rawPath string) string {
	base := filepath.Base(rawPath)
	if base == "RAW" || base == "Raw" || base == "raw" {
		return filepath.Join(filepath.Dir(rawPath), "Previews")
	}
	return filepath.Join(rawPath, "Previews")
}

func sessionNameForRawPath(rawPath string) string {
	base := filepath.Base(rawPath)
	if base == "RAW" || base == "Raw" || base == "raw" {
		return filepath.Base(filepath.Dir(rawPath))
	}
	return base
}

func hasJPEGFiles(dir string) bool {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return false
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		ext := strings.ToLower(filepath.Ext(e.Name()))
		if ext == ".jpg" || ext == ".jpeg" {
			return true
		}
	}
	return false
}

func hasRAWSubfolder(dir string) bool {
	for _, sub := range []string{"RAW", "Raw", "raw"} {
		p := filepath.Join(dir, sub)
		if st, err := os.Stat(p); err == nil && st.IsDir() {
			return true
		}
	}
	return false
}

func findRawDirs(root string) ([]string, error) {
	var dirs []string
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() {
			return nil
		}
		base := info.Name()
		if base == "RAW" || base == "Raw" || base == "raw" {
			dirs = append(dirs, path)
			return nil
		}
		if hasRAWSubfolder(path) {
			return nil
		}
		if hasJPEGFiles(path) {
			dirs = append(dirs, path)
		}
		return nil
	})
	return dirs, err
}

func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, data, 0644)
}

func extractPreviewJPEG(src, dst string) error {
	cmd := exec.Command("exiftool", "-PreviewImage", "-b", src)
	outFile, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer outFile.Close()
	cmd.Stdout = outFile
	return cmd.Run()
}

func isJPEGExt(ext string) bool {
	return ext == ".jpg" || ext == ".jpeg"
}

func isRAWExt(ext string) bool {
	switch ext {
	case ".arw", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".rw2", ".orf":
		return true
	default:
		return false
	}
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}