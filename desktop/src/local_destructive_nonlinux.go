//go:build !linux

package main

import (
	"os"
)

func createLocalOutput(docxSession, *os.Root, string, []byte) (bool, error) {
	return false, nil
}

func linuxManagedDirectory(docxSession) (string, error) {
	return "", fileopsCopyError(copyFileUnsupported)
}
