//go:build tools

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func main() {
	// 1. 定义基础路径（用 LUMA_SESSION_DIR 覆盖）
	baseDir := os.Getenv("LUMA_SESSION_DIR")
	if baseDir == "" {
		baseDir = "Livehouse_Archive/session"
	}
	rawDir := filepath.Join(baseDir, "RAW")
	previewDir := filepath.Join(baseDir, "Previews")

	// 2. 检查 Previews 目录是否存在，不存在则创建
	if _, err := os.Stat(previewDir); os.IsNotExist(err) {
		os.MkdirAll(previewDir, 0755)
	}

	// 3. 读取 RAW 目录
	files, err := os.ReadDir(rawDir)
	if err != nil {
		fmt.Printf("❌ 无法读取 RAW 目录: %v\n", err)
		return
	}

	fmt.Println("🚀 开始提取预览图...")

	for _, file := range files {
		// 只处理 .ARW 文件
		if !file.IsDir() && strings.HasSuffix(strings.ToUpper(file.Name()), ".ARW") {
			rawPath := filepath.Join(rawDir, file.Name())
			// 构造预览图文件名 (例如: DSC08226.jpg)
			previewName := strings.TrimSuffix(file.Name(), filepath.Ext(file.Name())) + ".jpg"
			previewPath := filepath.Join(previewDir, previewName)

			// 4. 调用 exiftool 提取预览图
			// -b: 二进制模式, -PreviewImage: 提取预览图标签
			cmd := exec.Command("exiftool", "-b", "-PreviewImage", rawPath)
			
			// 创建目标文件
			outFile, err := os.Create(previewPath)
			if err != nil {
				fmt.Printf("⚠️ 无法创建文件 %s: %v\n", previewName, err)
				continue
			}
			
			// 将命令输出重定向到文件
			cmd.Stdout = outFile
			err = cmd.Run()
			outFile.Close()

			if err != nil {
				fmt.Printf("❌ 提取失败 %s: %v\n", file.Name(), err)
			} else {
				fmt.Printf("✅ 已生成: %s\n", previewName)
			}
		}
	}

	fmt.Println("\n✨ 全部提取完成！现在你可以去运行 LumaKernel 进行 AI 筛选了。")
}