package main

import (
	"errors"
	"os"

	"golang.org/x/sys/windows"
)

func writeStagedPPTX(_ *os.Root, directory *os.File, name string, data []byte, apply func(func() error) error) error {
	file, err := openWindowsFileForMutation(directory, ".colink-"+localID(),
		windows.FILE_GENERIC_WRITE|windows.DELETE, windows.FILE_CREATE)
	if err != nil {
		return errPPTXOutputWrite
	}
	published := false
	defer func() {
		if !published {
			_ = deleteWindowsFile(file)
		}
		_ = file.Close()
	}()
	if err = syncPPTXOutput(file, data); err != nil {
		return errPPTXOutputWrite
	}
	return apply(func() error {
		err := renameWindowsFile(directory, file, name)
		published = err == nil
		var status windows.NTStatus
		if errors.As(err, &status) {
			return status.Errno()
		}
		return err
	})
}
