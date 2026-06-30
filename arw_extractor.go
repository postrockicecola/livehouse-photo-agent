//go:build tools

package main

import (
	"encoding/binary"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// ARW 文件结构
type ARWExtractor struct {
	inputDir  string
	outputDir string
}

// JPEG 标记
const (
	SOI  = 0xFFD8 // Start of Image
	EOI  = 0xFFD9 // End of Image
	APP1 = 0xFFE1 // EXIF 数据
	SOS  = 0xFFDA // Start of Scan
)

// NewARWExtractor 创建新的提取器
func NewARWExtractor(inputDir, outputDir string) *ARWExtractor {
	return &ARWExtractor{
		inputDir:  inputDir,
		outputDir: outputDir,
	}
}

// ExtractJPEG 从ARW文件中提取JPEG预览
func (e *ARWExtractor) ExtractJPEG(arwPath string) ([]byte, error) {
	file, err := os.Open(arwPath)
	if err != nil {
		return nil, fmt.Errorf("无法打开文件 %s: %v", arwPath, err)
	}
	defer file.Close()

	// 读取文件内容
	content, err := io.ReadAll(file)
	if err != nil {
		return nil, fmt.Errorf("无法读取文件 %s: %v", arwPath, err)
	}

	// 查找 JPEG 数据
	jpegData := e.findJPEGData(content)
	if jpegData == nil {
		return nil, fmt.Errorf("未找到JPEG数据在 %s 中", arwPath)
	}

	return jpegData, nil
}

// findJPEGData 在ARW文件中查找JPEG数据
func (e *ARWExtractor) findJPEGData(content []byte) []byte {
	// 查找 JPEG SOI 标记 (0xFFD8)
	for i := 0; i < len(content)-1; i++ {
		if content[i] == 0xFF && content[i+1] == 0xD8 {
			// 找到 SOI，现在查找 EOI (0xFFD9)
			for j := i + 2; j < len(content)-1; j++ {
				if content[j] == 0xFF && content[j+1] == 0xD9 {
					// 找到 EOI，返回JPEG数据
					return content[i : j+2]
				}
			}
		}
	}
	return nil
}

// ProcessARWFile 处理单个ARW文件
func (e *ARWExtractor) ProcessARWFile(arwPath string) error {
	fileName := filepath.Base(arwPath)
	baseName := strings.TrimSuffix(fileName, filepath.Ext(fileName))
	jpgFileName := baseName + ".jpg"
	jpgPath := filepath.Join(e.outputDir, jpgFileName)

	fmt.Printf("处理: %s\n", fileName)

	// 提取JPEG
	jpegData, err := e.ExtractJPEG(arwPath)
	if err != nil {
		return err
	}

	// 保存JPEG文件
	if err := os.WriteFile(jpgPath, jpegData, 0644); err != nil {
		return fmt.Errorf("无法保存JPEG文件 %s: %v", jpgPath, err)
	}

	fmt.Printf("✅ 已保存: %s (%d bytes)\n", jpgFileName, len(jpegData))
	return nil
}

// Process 处理整个文件夹
func (e *ARWExtractor) Process() error {
	// 检查输入目录
	if _, err := os.Stat(e.inputDir); err != nil {
		return fmt.Errorf("输入目录不存在: %s", e.inputDir)
	}

	// 创建输出目录
	if err := os.MkdirAll(e.outputDir, 0755); err != nil {
		return fmt.Errorf("无法创建输出目录: %v", err)
	}

	// 遍历输入目录
	entries, err := os.ReadDir(e.inputDir)
	if err != nil {
		return fmt.Errorf("无法读取目录: %v", err)
	}

	successCount := 0
	failCount := 0

	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}

		fileName := entry.Name()
		// 检查是否是ARW文件
		if !strings.EqualFold(filepath.Ext(fileName), ".arw") {
			continue
		}

		arwPath := filepath.Join(e.inputDir, fileName)
		if err := e.ProcessARWFile(arwPath); err != nil {
			fmt.Printf("❌ 失败: %s - %v\n", fileName, err)
			failCount++
		} else {
			successCount++
		}
	}

	fmt.Println("\n" + strings.Repeat("=", 60))
	fmt.Printf("✨ 处理完成!\n")
	fmt.Printf("   成功: %d 张\n", successCount)
	fmt.Printf("   失败: %d 张\n", failCount)
	fmt.Printf("   输出目录: %s\n", e.outputDir)

	return nil
}

// VerifyJPEG 验证提取的JPEG文件
func VerifyJPEG(jpgPath string) error {
	file, err := os.Open(jpgPath)
	if err != nil {
		return err
	}
	defer file.Close()

	// 读取文件头
	header := make([]byte, 2)
	if _, err := file.Read(header); err != nil {
		return fmt.Errorf("无法读取文件头: %v", err)
	}

	// 检查JPEG标记
	marker := binary.BigEndian.Uint16(header)
	if marker != SOI {
		return fmt.Errorf("不是有效的JPEG文件 (标记: 0x%04X)", marker)
	}

	// 查找EOI标记
	buffer := make([]byte, 1024)
	found := false
	for {
		n, err := file.Read(buffer)
		if err != nil && err != io.EOF {
			return fmt.Errorf("读取文件出错: %v", err)
		}
		if n == 0 {
			break
		}

		for i := 0; i < n-1; i++ {
			if buffer[i] == 0xFF && buffer[i+1] == 0xD9 {
				found = true
				break
			}
		}
		if found {
			break
		}
	}

	if !found {
		return fmt.Errorf("未找到JPEG结束标记")
	}

	return nil
}

// PrintInfo 打印处理信息
func PrintInfo() {
	fmt.Println("╔════════════════════════════════════════════════════════════╗")
	fmt.Println("║          ARW 格式照片 JPEG 提取工具                         ║")
	fmt.Println("║                                                            ║")
	fmt.Println("║  功能: 从 Sony ARW 原始格式文件中提取 JPEG 预览             ║")
	fmt.Println("║  用法: go run arw_extractor.go -input <输入目录>            ║")
	fmt.Println("╚════════════════════════════════════════════════════════════╝")
	fmt.Println()
}

func main() {
	PrintInfo()

	// 定义命令行参数
	inputDir := flag.String("input", "", "输入目录（包含ARW文件）")
	outputDir := flag.String("output", "", "输出目录（默认为输入目录下的reviews）")
	verify := flag.Bool("verify", false, "验证提取的JPEG文件")

	flag.Parse()

	// 验证必要参数
	if *inputDir == "" {
		fmt.Println("❌ 错误: 必须指定 -input 参数")
		fmt.Println("示例: go run arw_extractor.go -input /path/to/arw/folder")
		os.Exit(1)
	}

	// 设置输出目录
	if *outputDir == "" {
		*outputDir = filepath.Join(*inputDir, "reviews")
	}

	fmt.Printf("📁 输入目录: %s\n", *inputDir)
	fmt.Printf("📂 输出目录: %s\n", *outputDir)
	fmt.Println()

	// 创建提取器并处理
	extractor := NewARWExtractor(*inputDir, *outputDir)
	if err := extractor.Process(); err != nil {
		fmt.Printf("❌ 处理出错: %v\n", err)
		os.Exit(1)
	}

	// 验证输出文件
	if *verify {
		fmt.Println("\n🔍 验证提取的JPEG文件...")
		entries, _ := os.ReadDir(*outputDir)
		for _, entry := range entries {
			if !entry.IsDir() && strings.EqualFold(filepath.Ext(entry.Name()), ".jpg") {
				jpgPath := filepath.Join(*outputDir, entry.Name())
				if err := VerifyJPEG(jpgPath); err != nil {
					fmt.Printf("⚠️  %s - %v\n", entry.Name(), err)
				} else {
					fmt.Printf("✅ %s - 有效\n", entry.Name())
				}
			}
		}
	}

	fmt.Println("\n🎉 完成！")
}
