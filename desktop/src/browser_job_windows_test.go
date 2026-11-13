package main

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"golang.org/x/sys/windows"
)

func TestBrowserWorkerProcessTree(t *testing.T) {
	if role := os.Getenv("COLINK_BROWSER_JOB_TEST"); role != "" {
		if role == "worker" {
			_, _ = io.ReadAll(io.LimitReader(os.Stdin, 1))
			child := exec.Command(os.Args[0], "-test.run=^TestBrowserWorkerProcessTree$")
			child.Env = append(os.Environ(), "COLINK_BROWSER_JOB_TEST=browser")
			if err := child.Start(); err != nil {
				t.Fatal(err)
			}
			fmt.Println(child.Process.Pid)
		}
		time.Sleep(30 * time.Second)
		return
	}

	cmd := exec.Command(os.Args[0], "-test.run=^TestBrowserWorkerProcessTree$")
	cmd.Env = append(os.Environ(), "COLINK_BROWSER_JOB_TEST=worker")
	input, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	output, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer cmd.Process.Kill()
	release, err := ownBrowserWorker(cmd)
	if err != nil {
		t.Fatal(err)
	}
	var cleanup sync.Once
	defer cleanup.Do(release)
	if _, err := input.Write([]byte("x")); err != nil {
		t.Fatal(err)
	}
	pidLine := make(chan string, 1)
	go func() {
		line, _ := bufio.NewReader(output).ReadString('\n')
		pidLine <- line
	}()
	var pid uint32
	select {
	case line := <-pidLine:
		number, err := strconv.ParseUint(strings.TrimSpace(line), 10, 32)
		if err != nil {
			t.Fatalf("browser process id %q: %v", line, err)
		}
		pid = uint32(number)
	case <-time.After(10 * time.Second):
		t.Fatal("browser did not start")
	}
	child, err := windows.OpenProcess(windows.SYNCHRONIZE|windows.PROCESS_TERMINATE, false, pid)
	if err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(child)
	defer windows.TerminateProcess(child, 1)
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = cmd.Wait()

	cleanup.Do(release)
	status, err := windows.WaitForSingleObject(child, 5000)
	if err != nil || status != windows.WAIT_OBJECT_0 {
		t.Fatalf("browser survived worker exit: status=%v err=%v", status, err)
	}
}
