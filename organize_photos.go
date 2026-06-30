//go:build tools

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func main() {
	// 1. 目标会话目录（用 LUMA_SESSION_DIR 覆盖）
	targetDir := os.Getenv("LUMA_SESSION_DIR")
	if targetDir == "" {
		targetDir = "Livehouse_Archive/session"
	}

	// 切换工作目录到目标路径
	err := os.Chdir(targetDir)
	if err != nil {
		fmt.Printf("❌ 找不到目录: %v\n", err)
		return
	}
	fmt.Printf("📂 当前工作路径: %s\n", targetDir)

	// 2. 定义要创建的子目录
	dirs := []string{"RAW", "Previews", "Selected"}
	for _, dir := range dirs {
		if _, err := os.Stat(dir); os.IsNotExist(err) {
			os.Mkdir(dir, 0755)
		}
	}

	// 3. 读取当前目录下的所有文件
	files, err := os.ReadDir(".")
	if err != nil {
		fmt.Printf("❌ 读取失败: %v\n", err)
		return
	}

	count := 0
	for _, file := range files {
		// 只处理文件，且后缀是 .ARW (忽略大小写)
		if !file.IsDir() && strings.HasPrefix(strings.ToUpper(file.Name()), "DSC") && strings.HasSuffix(strings.ToUpper(file.Name()), ".ARW") {
			oldPath := file.Name()
			newPath := filepath.Join("RAW", file.Name())

			err := os.Rename(oldPath, newPath)
			if err != nil {
				fmt.Printf("⚠️ 无法移动 %s: %v\n", oldPath, err)
			} else {
				count++
			}
		}
	}

	fmt.Printf("\n✨ 整理完成！\n✅ 移动了 %d 个 ARW 文件到 %s/RAW\n", count, targetDir)
}