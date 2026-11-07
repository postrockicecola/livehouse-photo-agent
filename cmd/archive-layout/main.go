// archive-layout 将场次目录顶层的 ARW 与 JPEG 整理为 RAW/ 和 Previews/。
//
// 默认只预览；确认输出后加 -apply 才会真正移动文件：
//
//	go run ./cmd/archive-layout
//	go run ./cmd/archive-layout -apply
//
// 也可以传入任意场次目录：
//
//	go run ./cmd/archive-layout -apply /path/to/session-a /path/to/session-b
package main

import (
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

var defaultSessionDirs = []string{
	"/Volumes/M4Buffer/Visions/Livehouse_Archive/2026-10-02-01",
	"/Volumes/M4Buffer/Visions/Livehouse_Archive/2026-10-02-02",
	"/Volumes/M4Buffer/Visions/Livehouse_Archive/2026-10-02-03",
	"/Volumes/M4Buffer/Visions/Livehouse_Archive/2026-10-02-04",
}

type stats struct {
	raw, previews, skipped int
}

func fileStem(name string) string {
	return strings.TrimSuffix(name, filepath.Ext(name))
}

func normalizedStem(name string) string {
	return strings.ToLower(fileStem(name))
}

func destinationDir(sessionDir, name string) string {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".arw":
		return filepath.Join(sessionDir, "RAW")
	case ".jpg", ".jpeg":
		return filepath.Join(sessionDir, "Previews")
	default:
		return ""
	}
}

func extractPreviewJPEG(sourcePath, targetPath string) (err error) {
	if err := os.MkdirAll(filepath.Dir(targetPath), 0755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(targetPath), "."+filepath.Base(targetPath)+".tmp-*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	defer func() {
		_ = tmp.Close()
		if err != nil {
			_ = os.Remove(tmpPath)
		}
	}()

	var stderr strings.Builder
	cmd := exec.Command("exiftool", "-PreviewImage", "-b", sourcePath)
	cmd.Stdout = tmp
	cmd.Stderr = &stderr
	if err = cmd.Run(); err != nil {
		return fmt.Errorf("exiftool: %w: %s", err, strings.TrimSpace(stderr.String()))
	}
	if err = tmp.Sync(); err != nil {
		return err
	}
	if _, err = tmp.Seek(0, 0); err != nil {
		return err
	}
	header := make([]byte, 2)
	if _, err = tmp.Read(header); err != nil || header[0] != 0xff || header[1] != 0xd8 {
		return fmt.Errorf("ARW 中未提取到有效 JPEG")
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpPath, targetPath)
}

func organizeSession(sessionDir string, apply bool) (stats, error) {
	entries, err := os.ReadDir(sessionDir)
	if err != nil {
		return stats{}, err
	}

	rootJPEGs := make(map[string]bool)
	for _, entry := range entries {
		ext := strings.ToLower(filepath.Ext(entry.Name()))
		if !entry.IsDir() && (ext == ".jpg" || ext == ".jpeg") {
			rootJPEGs[normalizedStem(entry.Name())] = true
		}
	}

	var result stats
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}

		targetDir := destinationDir(sessionDir, entry.Name())
		if targetDir == "" {
			continue
		}

		sourcePath := filepath.Join(sessionDir, entry.Name())
		targetPath := filepath.Join(targetDir, entry.Name())
		if _, err := os.Stat(targetPath); err == nil {
			log.Printf("跳过，目标已存在: %s", targetPath)
			result.skipped++
			continue
		} else if !errors.Is(err, fs.ErrNotExist) {
			return result, fmt.Errorf("检查目标 %s: %w", targetPath, err)
		}

		if strings.EqualFold(filepath.Ext(entry.Name()), ".arw") && !rootJPEGs[normalizedStem(entry.Name())] {
			previewPath := filepath.Join(sessionDir, "Previews", fileStem(entry.Name())+".jpg")
			if _, err := os.Stat(previewPath); errors.Is(err, fs.ErrNotExist) {
				fmt.Printf("提取预览: %s -> %s\n", sourcePath, previewPath)
				if apply {
					if err := extractPreviewJPEG(sourcePath, previewPath); err != nil {
						return result, fmt.Errorf("提取 %s: %w", sourcePath, err)
					}
				}
				result.previews++
			} else if err != nil {
				return result, fmt.Errorf("检查预览图 %s: %w", previewPath, err)
			}
		}

		fmt.Printf("%s -> %s\n", sourcePath, targetPath)
		if apply {
			if err := os.MkdirAll(targetDir, 0755); err != nil {
				return result, fmt.Errorf("创建目录 %s: %w", targetDir, err)
			}
			if err := os.Rename(sourcePath, targetPath); err != nil {
				return result, fmt.Errorf("移动 %s: %w", sourcePath, err)
			}
		}

		if filepath.Base(targetDir) == "RAW" {
			result.raw++
		} else {
			result.previews++
		}
	}
	return result, nil
}

func main() {
	apply := flag.Bool("apply", false, "真正移动文件；不指定时仅预览")
	flag.Parse()

	sessionDirs := flag.Args()
	if len(sessionDirs) == 0 {
		sessionDirs = defaultSessionDirs
	}

	var total stats
	var failed int
	for _, sessionDir := range sessionDirs {
		fmt.Printf("\n整理场次: %s\n", sessionDir)
		result, err := organizeSession(filepath.Clean(sessionDir), *apply)
		if err != nil {
			log.Printf("整理失败: %v", err)
			failed++
			continue
		}
		total.raw += result.raw
		total.previews += result.previews
		total.skipped += result.skipped
		fmt.Printf("本场次: ARW=%d JPEG=%d 跳过=%d\n", result.raw, result.previews, result.skipped)
	}

	mode := "预览"
	if *apply {
		mode = "已执行"
	}
	fmt.Printf("\n%s完成: ARW=%d JPEG=%d 跳过=%d 失败场次=%d\n",
		mode, total.raw, total.previews, total.skipped, failed)
	if !*apply {
		fmt.Println("以上未修改文件；确认无误后运行: go run ./cmd/archive-layout -apply")
	}
	if failed > 0 {
		os.Exit(1)
	}
}
