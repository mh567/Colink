//go:build linux || darwin

package main

import (
	"fmt"
	"os"
	"syscall"
)

const docxNonblock = syscall.O_NONBLOCK

func fileIdentity(file *os.File) (string, uint64, error) {
	var stat syscall.Stat_t
	if err := syscall.Fstat(int(file.Fd()), &stat); err != nil {
		return "", 0, err
	}
	return fmt.Sprintf("%d:%d", stat.Dev, stat.Ino), uint64(stat.Nlink), nil
}
