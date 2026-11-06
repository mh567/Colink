package main

import (
	"fmt"
	"golang.org/x/sys/windows"
	"os"
)

const docxNonblock = 0

func fileIdentity(file *os.File) (string, uint64, error) {
	var info windows.ByHandleFileInformation
	if err := windows.GetFileInformationByHandle(windows.Handle(file.Fd()), &info); err != nil {
		return "", 0, err
	}
	return fmt.Sprintf("%d:%d:%d", info.VolumeSerialNumber, info.FileIndexHigh, info.FileIndexLow), uint64(info.NumberOfLinks), nil
}
