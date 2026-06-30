//go:build tools

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// ImageTask 定义了在流水线中流转的任务对象
type ImageTask struct {
	ID          string    `json:"id"`
	RawPath     string    `json:"raw_path"`     // 原始 .ARW 路径
	PreviewPath string    `json:"preview_path"` // 提取出的 .jpg 路径
	Status      string    `json:"status"`
	CreatedAt   time.Time `json:"created_at"`
}

var (
	ctx         = context.Background()
	redisClient *redis.Client
	// 临时存放预览图的目录
	tempPreviewDir = "./temp_previews"
)

func init() {
	// 初始化 Redis 客户端
	redisClient = redis.NewClient(&redis.Options{
		Addr: "localhost:6379", // 根据你的 Redis 配置修改
		DB:   0,
	})

	// 确保临时目录存在
	if _, err := os.Stat(tempPreviewDir); os.IsNotExist(err) {
		os.Mkdir(tempPreviewDir, 0755)
	}
}

// extractPreview 使用 exiftool 从 ARW 中提取嵌入的预览图
func extractPreview(arwPath string) (string, error) {
	taskID := uuid.NewString()
	previewPath := filepath.Join(tempPreviewDir, taskID+".jpg")

	// exiftool 参数解释:
	// -b: 二进制输出
	// -PreviewImage: 提取预览图标签（Sony ARW 通用）
	// -w: 指定输出路径
	cmd := exec.Command("exiftool", "-b", "-PreviewImage", arwPath, "-w", previewPath)
	
	// 注意：exiftool 的 -w 参数如果发现文件已存在会报错，这里我们直接运行
	err := cmd.Run()
	if err != nil {
		return "", err
	}

	// 检查文件是否真的生成了（有些损坏的 ARW 可能提取失败）
	if _, err := os.Stat(previewPath); os.IsNotExist(err) {
		return "", fmt.Errorf("preview image not found after extraction")
	}

	return previewPath, nil
}

func main() {
	inputDir := "./data" // 你的 Livehouse 照片存放目录
	fmt.Printf("📂 正在扫描目录: %s\n", inputDir)

	err := filepath.Walk(inputDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		// 只处理 .ARW 文件
		if !info.IsDir() && strings.ToUpper(filepath.Ext(path)) == ".ARW" {
			fmt.Printf("📸 发现原片: %s\n", info.Name())

			// 1. 提取预览图 (核心步骤)
			previewPath, err := extractPreview(path)
			if err != nil {
				log.Printf("❌ 预览图提取失败 [%s]: %v", info.Name(), err)
				return nil
			}

			// 2. 包装任务结构体
			task := ImageTask{
				ID:          uuid.NewString(),
				RawPath:     path,
				PreviewPath: previewPath,
				Status:      "PENDING",
				CreatedAt:   time.Now(),
			}

			// 3. 序列化为 JSON
			payload, _ := json.Marshal(task)

			// 4. 推送到 Redis 队列 (List: task_queue)
			err = redisClient.LPush(ctx, "task_queue", payload).Err()
			if err != nil {
				log.Printf("❌ Redis 入队失败: %v", err)
			} else {
				fmt.Printf("✅ 任务已入队: %s (Preview: %s)\n", task.ID[:8], filepath.Base(previewPath))
			}
		}
		return nil
	})

	if err != nil {
		log.Fatal(err)
	}

	fmt.Println("\n🏁 目录扫描完毕，引擎正在后台运行...")
}