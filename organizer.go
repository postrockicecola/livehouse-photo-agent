//go:build tools

package main

import (
	"fmt"
	"io"
	"io/fs"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
)

// 环境变量（可选，不设则用默认值）：
//   ORGANIZER_MOVE_WORKERS  并行拷贝协程数，默认 4；SD 卡可试 1（顺序读有时更快）。
//   ORGANIZER_EXIF_BATCH    每批 exiftool 文件数，默认 80。
//   ORGANIZER_COPY_BUF_MIB  每协程拷贝缓冲区 MiB，默认 4。
func getenvInt(key string, def, min, max int) int {
	v := strings.TrimSpace(os.Getenv(key))
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return def
	}
	if n < min {
		return min
	}
	if n > max {
		return max
	}
	return n
}

// moveFile 跨卷：读 src 写 dst 后删 src。buf 由调用方复用（每 worker 一块），减少分配。
func moveFile(sourcePath, destPath string, buf []byte) error {
	srcFile, err := os.Open(sourcePath)
	if err != nil {
		return err
	}
	dstFile, err := os.Create(destPath)
	if err != nil {
		srcFile.Close()
		return err
	}
	_, err = io.CopyBuffer(dstFile, srcFile, buf)
	_ = srcFile.Close()
	closeErr := dstFile.Close()
	if err != nil {
		_ = os.Remove(destPath)
		return err
	}
	if closeErr != nil {
		_ = os.Remove(destPath)
		return closeErr
	}
	return os.Remove(sourcePath)
}

// batchCreateDates 一次 exiftool 处理多文件，避免每个 ARW 都 fork 一次进程。
func batchCreateDates(paths []string, batchSize int) map[string]string {
	out := make(map[string]string, len(paths))
	for i := 0; i < len(paths); i += batchSize {
		end := i + batchSize
		if end > len(paths) {
			end = len(paths)
		}
		batch := paths[i:end]
		args := append([]string{"-m", "-q", "-s3", "-CreateDate", "-d", "%Y-%m-%d"}, batch...)
		raw, err := exec.Command("exiftool", args...).Output()
		if err != nil {
			log.Printf("exiftool 批量失败 [%d-%d]: %v，本批归入 Unknown_Date\n", i, end, err)
			for _, p := range batch {
				out[p] = "Unknown_Date"
			}
			continue
		}
		lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
		for k, p := range batch {
			dateDirName := "Unknown_Date"
			if k < len(lines) {
				if d := strings.TrimSpace(lines[k]); d != "" && d != "-" {
					dateDirName = d
				}
			}
			out[p] = dateDirName
		}
	}
	return out
}

func collectARWPaths(root string) ([]string, error) {
	var paths []string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
		if strings.EqualFold(filepath.Ext(d.Name()), ".arw") {
			paths = append(paths, path)
		}
		return nil
	})
	return paths, err
}

func main() {
	moveWorkers := getenvInt("ORGANIZER_MOVE_WORKERS", 4, 1, 16)
	exifBatch := getenvInt("ORGANIZER_EXIF_BATCH", 80, 20, 200)
	bufMiB := getenvInt("ORGANIZER_COPY_BUF_MIB", 4, 1, 32)
	copyBufSize := bufMiB << 20

	srcDirs := []string{
		"/Volumes/Untitled/DCIM/103MSDCF",
	}
	destParentDir := getenv("LUMA_ARCHIVE_ROOT", "Livehouse_Archive")

	for _, srcDir := range srcDirs {
		fmt.Printf("开始搬家: %s (workers=%d exif_batch=%d buf_MiB=%d)\n", srcDir, moveWorkers, exifBatch, bufMiB)

		paths, err := collectARWPaths(srcDir)
		if err != nil {
			log.Printf("扫描出错: %v\n", err)
			continue
		}
		if len(paths) == 0 {
			fmt.Println("未发现 .ARW 文件")
			continue
		}

		dateByPath := batchCreateDates(paths, exifBatch)

		mkdirMu := sync.Mutex{}
		seenDir := make(map[string]struct{})

		type job struct {
			src, dest, name, dateLabel string
		}
		jobs := make([]job, 0, len(paths))
		for _, path := range paths {
			dateDirName := dateByPath[path]
			if dateDirName == "" {
				dateDirName = "Unknown_Date"
			}
			targetDir := filepath.Join(destParentDir, dateDirName, "RAW")
			base := filepath.Base(path)
			destPath := filepath.Join(targetDir, base)
			jobs = append(jobs, job{src: path, dest: destPath, name: base, dateLabel: dateDirName})
		}

		jch := make(chan job)
		var wg sync.WaitGroup
		for w := 0; w < moveWorkers; w++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				buf := make([]byte, copyBufSize)
				for j := range jch {
					mkdirMu.Lock()
					if _, ok := seenDir[filepath.Dir(j.dest)]; !ok {
						if err := os.MkdirAll(filepath.Dir(j.dest), 0755); err != nil {
							log.Printf("创建目录失败 [%s]: %v\n", j.dest, err)
							mkdirMu.Unlock()
							continue
						}
						seenDir[filepath.Dir(j.dest)] = struct{}{}
					}
					mkdirMu.Unlock()

					fmt.Printf("🚚 正在搬运: %s -> %s\n", j.name, j.dateLabel)
					if err := moveFile(j.src, j.dest, buf); err != nil {
						log.Printf("❌ 搬运失败 [%s]: %v\n", j.name, err)
					}
				}
			}()
		}
		for _, j := range jobs {
			jch <- j
		}
		close(jch)
		wg.Wait()
	}
	fmt.Println("\n✨ 任务完成！这回照片全住进新家了。")
}
