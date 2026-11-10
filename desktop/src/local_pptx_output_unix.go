//go:build linux || darwin

package main

import (
	"os"
	"runtime"
	"syscall"

	"golang.org/x/sys/unix"
)

func writeStagedPPTX(root *os.Root, directory *os.File, name string, data []byte, apply func(func() error) error) error {
	temporary := ".colink-" + localID()
	if err := root.Mkdir(temporary, 0700); err != nil {
		return errPPTXOutputWrite
	}
	entry, err := root.Lstat(temporary)
	if err != nil || !entry.IsDir() {
		return errPPTXOutputWrite
	}
	owner, ok := entry.Sys().(*syscall.Stat_t)
	if !ok || owner.Uid != uint32(os.Geteuid()) {
		return errPPTXOutputWrite
	}
	defer func() {
		if latest, err := root.Lstat(temporary); err == nil && os.SameFile(entry, latest) {
			_ = unix.Unlinkat(int(directory.Fd()), temporary, unix.AT_REMOVEDIR)
			runtime.KeepAlive(directory)
		}
	}()
	if entry.Mode().Perm() != 0700 {
		return errPPTXOutputWrite
	}
	stage, err := root.OpenRoot(temporary)
	if err != nil {
		return errPPTXOutputWrite
	}
	defer stage.Close()
	stageDirectory, err := stage.Open(".")
	if err != nil {
		return errPPTXOutputWrite
	}
	defer stageDirectory.Close()
	if actual, err := stageDirectory.Stat(); err != nil || !os.SameFile(entry, actual) {
		return errPPTXOutputWrite
	}

	const stagedName = "report"
	file, err := stage.OpenFile(stagedName, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errPPTXOutputWrite
	}
	defer stage.Remove(stagedName)
	err = syncPPTXOutput(file, data)
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return errPPTXOutputWrite
	}
	return apply(func() error {
		// A writer in the shared grant can rename the stage, but cannot replace its contents.
		return renamePPTXOutput(stageDirectory, stagedName, directory, name)
	})
}
