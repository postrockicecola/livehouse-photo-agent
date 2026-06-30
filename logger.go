package main

import (
	"fmt"
	"log"
)

// ANSI prefixes for long-running console watch (SCAN=blue/cyan, EXE=green).
const (
	colorScan  = "\033[34m" // blue
	colorExe   = "\033[32m" // green
	colorOK    = "\033[32m"
	colorWarn  = "\033[33m"
	colorErr   = "\033[31m"
	colorReset = "\033[0m"
)

func logScanf(format string, args ...any) {
	log.Print(colorScan + "[SCAN] " + colorReset + fmt.Sprintf(format, args...))
}

func logExef(format string, args ...any) {
	log.Print(colorExe + "[EXE] " + colorReset + fmt.Sprintf(format, args...))
}

func logSuccessf(format string, args ...any) {
	log.Print(colorOK + "[SUCCESS] " + colorReset + fmt.Sprintf(format, args...))
}

func logWarnf(format string, args ...any) {
	log.Print(colorWarn + "[WARN] " + colorReset + fmt.Sprintf(format, args...))
}

func logErrf(format string, args ...any) {
	log.Print(colorErr + "[ERROR] " + colorReset + fmt.Sprintf(format, args...))
}
