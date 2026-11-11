package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const browserReplyLimit = 5 << 20

type browserWorker struct {
	cmd    *exec.Cmd
	input  io.WriteCloser
	output *os.File
	lines  *bufio.Scanner
	done   chan struct{}
	once   sync.Once
}

func startBrowserWorker(session docxSession, profile string) (browserProcess, error) {
	unavailable := errors.New("BROWSER_RUNTIME_UNAVAILABLE")
	if err := ensurePortable(session.Locale, func(string) {}); err != nil {
		return nil, unavailable
	}
	root, err := filepath.Abs(portableDir())
	if err != nil {
		return nil, unavailable
	}
	profile, err = filepath.Abs(profile)
	if err != nil {
		return nil, unavailable
	}
	runner, cleanup, err := prepareLocalPortableRunner(root, "browser", unavailable.Error())
	if err != nil {
		return nil, err
	}
	started := false
	defer func() {
		if !started {
			cleanup()
		}
	}()
	cmd := exec.Command(pythonExe(root), "-I", runner, "--profile", profile)
	cmd.Dir = root
	cmd.Env = browserWorkerEnvironment()
	configureProcGroup(cmd)
	cmd.Stderr = io.Discard
	input, err := cmd.StdinPipe()
	if err != nil {
		return nil, unavailable
	}
	output, outputWriter, err := os.Pipe()
	if err != nil {
		_ = input.Close()
		return nil, unavailable
	}
	defer outputWriter.Close()
	cmd.Stdout = outputWriter
	if err := cmd.Start(); err != nil {
		_ = input.Close()
		_ = output.Close()
		return nil, unavailable
	}
	started = true
	lines := bufio.NewScanner(output)
	lines.Buffer(make([]byte, 65536), browserReplyLimit)
	worker := &browserWorker{cmd: cmd, input: input, output: output, lines: lines, done: make(chan struct{})}
	go func() {
		_ = cmd.Wait()
		cleanup()
		close(worker.done)
	}()
	go func() {
		select {
		case <-session.Context.Done():
			worker.stop()
		case <-worker.done:
		}
	}()
	return worker, nil
}

func browserWorkerEnvironment() []string {
	env := []string{"PYTHONUTF8=1", "NO_PROXY=127.0.0.1,localhost,::1"}
	for _, key := range []string{
		"SystemRoot", "SYSTEMDRIVE", "USERPROFILE", "LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)",
		"ProgramW6432", "TEMP", "TMP", "TMPDIR", "PATH", "HOME", "DISPLAY", "WAYLAND_DISPLAY",
		"XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "LANG", "LC_ALL",
	} {
		if value, ok := os.LookupEnv(key); ok {
			env = append(env, key+"="+value)
		}
	}
	return env
}

func (w *browserWorker) perform(ctx context.Context, action browserAction) (browserResult, error) {
	failed := errors.New("BROWSER_TASK_FAILED")
	ctx, cancel := context.WithTimeout(ctx, 40*time.Second)
	defer cancel()
	stop := context.AfterFunc(ctx, w.stop)
	defer stop()
	if ctx.Err() != nil || json.NewEncoder(w.input).Encode(action) != nil || !w.lines.Scan() {
		return browserResult{}, failed
	}
	var result browserResult
	decoder := json.NewDecoder(bytes.NewReader(w.lines.Bytes()))
	decoder.DisallowUnknownFields()
	var extra any
	if decoder.Decode(&result) != nil || decoder.Decode(&extra) != io.EOF ||
		ctx.Err() != nil || (result.Error != "" && !strings.HasPrefix(result.Error, "BROWSER_")) {
		return browserResult{}, failed
	}
	return result, nil
}

func (w *browserWorker) stop() {
	w.once.Do(func() {
		_ = w.input.Close()
		timer := time.NewTimer(6 * time.Second)
		defer timer.Stop()
		select {
		case <-w.done:
		case <-timer.C:
			forceStopBrowserWorker(w.cmd)
		}
		_ = w.output.Close()
	})
}
