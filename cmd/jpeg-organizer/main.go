// JPEG 从 SD 卡目录按 EXIF CreateDate 归档到目标盘。
//
// 运行（需已安装 exiftool）:
//
//	go run ./cmd/jpeg-organizer
//
// 可选环境变量:
//
//	JPEG_ORG_SRC           源目录，默认 /Volumes/Untitled/DCIM/100MSDCF
//	JPEG_ORG_DEST          目标根目录，默认 ./photos
//	JPEG_ORG_MOVE_WORKERS  并行搬运协程数，默认 4
//	JPEG_ORG_EXIF_BATCH    每批 exiftool 文件数，默认 80
//	JPEG_ORG_COPY_BUF_MIB  每协程拷贝缓冲区 MiB，默认 4
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
	"sync/atomic"
)

const (
	defaultSrc  = "/Volumes/Untitled/DCIM/100MSDCF"
	defaultDest = "./photos"
)

func getenv(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

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

func isJPEG(name string) bool {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".jpg", ".jpeg":
		return true
	default:
		return false
	}
}

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

func collectJPEGPaths(root string) ([]string, error) {
	var paths []string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
		if isJPEG(d.Name()) {
			paths = append(paths, path)
		}
		return nil
	})
	return paths, err
}

func main() {
	srcDir := getenv("JPEG_ORG_SRC", defaultSrc)
	destRoot := getenv("JPEG_ORG_DEST", defaultDest)
	moveWorkers := getenvInt("JPEG_ORG_MOVE_WORKERS", 4, 1, 16)
	exifBatch := getenvInt("JPEG_ORG_EXIF_BATCH", 80, 20, 200)
	bufMiB := getenvInt("JPEG_ORG_COPY_BUF_MIB", 4, 1, 32)
	copyBufSize := bufMiB << 20

	if _, err := os.Stat(srcDir); err != nil {
		log.Fatalf("源目录不可用 %s: %v\n", srcDir, err)
	}
	if err := os.MkdirAll(destRoot, 0755); err != nil {
		log.Fatalf("无法创建目标根目录 %s: %v\n", destRoot, err)
	}

	fmt.Printf("开始搬运 JPEG: %s -> %s (workers=%d exif_batch=%d buf_MiB=%d)\n",
		srcDir, destRoot, moveWorkers, exifBatch, bufMiB)

	paths, err := collectJPEGPaths(srcDir)
	if err != nil {
		log.Fatalf("扫描出错: %v\n", err)
	}
	if len(paths) == 0 {
		fmt.Println("未发现 JPEG 文件")
		return
	}
	fmt.Printf("共 %d 个 JPEG\n", len(paths))

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
		targetDir := filepath.Join(destRoot, dateDirName)
		base := filepath.Base(path)
		destPath := filepath.Join(targetDir, base)
		jobs = append(jobs, job{src: path, dest: destPath, name: base, dateLabel: dateDirName})
	}

	var moved, skipped, failed atomic.Int64
	jch := make(chan job)
	var wg sync.WaitGroup
	for w := 0; w < moveWorkers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			buf := make([]byte, copyBufSize)
			for j := range jch {
				if _, err := os.Stat(j.dest); err == nil {
					log.Printf("跳过（目标已存在）: %s\n", j.dest)
					skipped.Add(1)
					continue
				}

				mkdirMu.Lock()
				dir := filepath.Dir(j.dest)
				if _, ok := seenDir[dir]; !ok {
					if err := os.MkdirAll(dir, 0755); err != nil {
						log.Printf("创建目录失败 [%s]: %v\n", dir, err)
						mkdirMu.Unlock()
						failed.Add(1)
						continue
					}
					seenDir[dir] = struct{}{}
				}
				mkdirMu.Unlock()

				fmt.Printf("搬运: %s -> %s/\n", j.name, j.dateLabel)
				if err := moveFile(j.src, j.dest, buf); err != nil {
					log.Printf("搬运失败 [%s]: %v\n", j.name, err)
					failed.Add(1)
				} else {
					moved.Add(1)
				}
			}
		}()
	}
	for _, j := range jobs {
		jch <- j
	}
	close(jch)
	wg.Wait()

	fmt.Printf("\n完成: 成功 %d，跳过 %d，失败 %d\n", moved.Load(), skipped.Load(), failed.Load())
}
