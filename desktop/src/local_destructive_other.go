//go:build !windows && !linux

package main

import (
	"os"
)

func prepareDestructiveTarget(docxSession, *os.Root, string, string) (destructiveTarget, error) {
	return nil, fileopsCopyError(copyFileUnsupported)
}
