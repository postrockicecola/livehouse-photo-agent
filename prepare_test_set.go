//go:build tools

package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
)

func main() {
	// 1. 配置路径（用 LUMA_ARCHIVE_ROOT 覆盖归档根目录）
	sourceBase := os.Getenv("LUMA_ARCHIVE_ROOT")
	if sourceBase == "" {
		sourceBase = "Livehouse_Archive"
	}
	targetBase := filepath.Join(sourceBase, "Small_Test_Set")

	// 2. 你的精选名单 (按分类组织)
	datasets := map[string][]string{
		"normal": {
			"2026-03-21/Previews/DSC07193.jpg",
			"2026-03-21/Previews/DSC07199.jpg",
			"2026-03-21/Previews/DSC07201.jpg",
			"2026-03-21/Previews/DSC07215.jpg",
			"2026-03-21/Previews/DSC07227.jpg",
			"2026-03-21/Previews/DSC07237.jpg",
		},
		"bad": {
			"2026-03-21/Previews/DSC07229.jpg",
			"2026-03-21/Previews/DSC07231.jpg",
			"2026-03-21/Previews/DSC07266.jpg",
			"2026-03-21/Previews/DSC07663.jpg",
			"2026-03-21/Previews/DSC07743.jpg",
		},
		"good": {
			"2026-03-21/Previews/DSC07254.jpg",
			"2026-03-21/Previews/DSC07734.jpg",
			"2026-03-21/Previews/DSC07735.jpg",
		},
	}

	fmt.Println("🚀 正在构建小规模测试集...")

	for category, files := range datasets {
		// 为每个分类创建子目录，方便观察 AI 表现
		categoryDir := filepath.Join(targetBase, category)
		os.MkdirAll(categoryDir, 0755)

		for _, relPath := range files {
			src := filepath.Join(sourceBase, relPath)
			dst := filepath.Join(categoryDir, filepath.Base(relPath))

			err := copyFile(src, dst)
			if err != nil {
				fmt.Printf("❌ 复制失败 [%s]: %v\n", relPath, err)
			} else {
				fmt.Printf("✅ [%s] -> %s\n", category, filepath.Base(relPath))
			}
		}
	}

	fmt.Printf("\n✨ 准备就绪！测试集路径: %s\n", targetBase)
}

// 简单的文件复制函数
func copyFile(src, dst string) error {
	sourceFile, err := os.Open(src)
	if err != nil {
		return err
	}
	defer sourceFile.Close()

	destFile, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer destFile.Close()

	_, err = io.Copy(destFile, sourceFile)
	return err
}