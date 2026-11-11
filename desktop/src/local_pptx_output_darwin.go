package main

import (
	"os"
	"runtime"

	"golang.org/x/sys/unix"
)

func renamePPTXOutput(stage *os.File, source string, directory *os.File, name string) error {
	err := unix.RenameatxNp(int(stage.Fd()), source, int(directory.Fd()), name, unix.RENAME_EXCL)
	runtime.KeepAlive(stage)
	runtime.KeepAlive(directory)
	return err
}
