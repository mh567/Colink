//go:build !windows

package main

import (
	"errors"
	"os/exec"
	"syscall"
)

func isAddrInUse(err error) bool {
	return errors.Is(err, syscall.EADDRINUSE)
}

func configureProcGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

func killProcessTree(cmd *exec.Cmd) {
	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
	_ = cmd.Wait()
}

func ownBrowserWorker(cmd *exec.Cmd) (func(), error) {
	group := cmd.Process.Pid
	return func() { _ = syscall.Kill(-group, syscall.SIGKILL) }, nil
}
