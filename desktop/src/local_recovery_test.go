package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestLocalDOCXRestartPreservesAwaitingConfirmationWithoutAutoExecuting(t *testing.T) {
	home := t.TempDir()
	worker := newLocalDOCX(home)
	defer worker.close()

	directory := t.TempDir()
	source, err := makeDOCX("原始内容")
	if err != nil {
		t.Fatal(err)
	}
	docPath := filepath.Join(directory, "周报.docx")
	if err = os.WriteFile(docPath, source, 0600); err != nil {
		t.Fatal(err)
	}

	session := docxSession{
		Base:     "https://example.test",
		DeviceID: "dev1",
		UserID:   1,
		Secret:   "secret",
		Context:  context.Background(),
	}
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}

	requestID := "0123456789abcdef0123456789abcdef"
	req := docxRequest{
		Action:      "overwrite",
		Format:      "docx",
		RequestID:   requestID,
		SourceGrant: grant.ID,
		Source:      "周报.docx",
		OutputGrant: grant.ID,
		Output:      "周报.docx",
		Instruction: "整理",
	}

	// Record initial task that reached awaiting_confirmation before process termination
	initTask := docxTask{
		ID:          requestID,
		Format:      "docx",
		Operation:   "overwrite",
		Output:      docPath,
		Instruction: "整理",
		Status:      "awaiting_confirmation",
		Steps:       []string{"已登记操作意图与约束", "已核验操作权限"},
	}
	journal, entry, _, err := worker.claimDestructive(session, req, initTask)
	if err != nil {
		t.Fatal(err)
	}
	entry.Task = initTask
	data, err := json.Marshal(entry)
	if err != nil {
		t.Fatal(err)
	}
	if err = saveLocalTaskJSON(journal, data); err != nil {
		t.Fatal(err)
	}

	// Verify task is currently awaiting_confirmation
	state, err := worker.state(session)
	if err != nil {
		t.Fatal(err)
	}
	var found *docxTask
	for _, tk := range state.Tasks {
		if tk.ID == requestID {
			found = &tk
			break
		}
	}
	if found == nil || found.Status != "awaiting_confirmation" {
		t.Fatalf("expected awaiting_confirmation before restart, got: %+v", found)
	}

	// Now simulate client process killed / restarted
	worker.close()

	// Boot new client instance after restart
	restarted := newLocalDOCX(home)
	defer restarted.close()

	restartedState, err := restarted.state(session)
	if err != nil {
		t.Fatal(err)
	}

	var restartedTask *docxTask
	for _, tk := range restartedState.Tasks {
		if tk.ID == requestID {
			restartedTask = &tk
			break
		}
	}
	if restartedTask == nil {
		t.Fatal("task lost after restart")
	}

	// Acceptance criteria:
	// "客户端重启后恢复相应状态和产物关联，待确认动作不能自动执行。"
	if restartedTask.Status != "awaiting_confirmation" {
		t.Fatalf("expected status awaiting_confirmation after restart, but got: %q", restartedTask.Status)
	}
	if len(restartedTask.Steps) != 2 {
		t.Fatalf("expected 2 completed steps preserved, got: %+v", restartedTask.Steps)
	}

	// Replay must return existing awaiting_confirmation task without auto-executing
	replayedTask, err := restarted.run(session, req, func() error { return nil })
	if err != nil {
		t.Fatalf("replay failed: %v", err)
	}
	if replayedTask.Status != "awaiting_confirmation" {
		t.Fatalf("expected replayed status awaiting_confirmation, got: %s", replayedTask.Status)
	}

	// Now user explicitly cancels this awaiting confirmation task
	if err = restarted.cancel(session, requestID); err != nil {
		t.Fatalf("cancel failed: %v", err)
	}

	afterCancelState, err := restarted.state(session)
	if err != nil {
		t.Fatal(err)
	}
	var cancelledTask *docxTask
	for _, tk := range afterCancelState.Tasks {
		if tk.ID == requestID {
			cancelledTask = &tk
			break
		}
	}
	if cancelledTask == nil || cancelledTask.Status != "cancelled" {
		t.Fatalf("expected status cancelled after explicit cancel, got: %+v", cancelledTask)
	}
}

func TestLocalDOCXCancelRunningTaskStopsExecutionAndPreservesArtifacts(t *testing.T) {
	home := t.TempDir()
	worker := newLocalDOCX(home)
	defer worker.close()

	generationStarted := make(chan struct{})
	var generatedCount atomic.Int32

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/generate") {
			generatedCount.Add(1)
			close(generationStarted)
			select {
			case <-r.Context().Done():
			case <-time.After(500 * time.Millisecond):
			}
			return
		}
		w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()

	directory := t.TempDir()
	source, err := makeDOCX("原始内容")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(directory, "输入.docx"), source, 0600); err != nil {
		t.Fatal(err)
	}

	session := docxSession{
		Base:     server.URL,
		DeviceID: "dev1",
		UserID:   1,
		Token:    "token",
		Secret:   "secret",
		Context:  context.Background(),
	}
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}

	var runErr error
	var wg sync.WaitGroup
	wg.Add(1)

	req := docxRequest{
		Action:      "run",
		SourceGrant: grant.ID,
		Source:      "输入.docx",
		OutputGrant: grant.ID,
		Output:      "成果.docx",
		Instruction: "整理",
	}

	go func() {
		defer wg.Done()
		_, runErr = worker.run(session, req, func() error { return nil })
	}()

	select {
	case <-generationStarted:
	case <-time.After(3 * time.Second):
		t.Fatal("timed out waiting for generate to be called")
	}

	// Task is currently running. Get state to find active task ID
	activeState, err := worker.state(session)
	if err != nil {
		t.Fatal(err)
	}
	if len(activeState.Tasks) == 0 {
		t.Fatal("no running task found in state")
	}
	runningTask := activeState.Tasks[0]
	if runningTask.Status != "running" {
		t.Fatalf("expected status running, got: %s", runningTask.Status)
	}

	// Cancel the task
	if err = worker.cancel(session, runningTask.ID); err != nil {
		t.Fatalf("cancel failed: %v", err)
	}

	wg.Wait()

	finalState, err := worker.state(session)
	if err != nil {
		t.Fatal(err)
	}
	if len(finalState.Tasks) != 1 {
		t.Fatalf("expected 1 task, got: %d", len(finalState.Tasks))
	}
	finalTask := finalState.Tasks[0]
	if finalTask.Status != "cancelled" {
		t.Fatalf("expected task status to be cancelled, got: %s (err: %v)", finalTask.Status, runErr)
	}
	// "已读取源文件" should have been recorded as completed step
	if len(finalTask.Steps) == 0 {
		t.Fatal("expected completed steps to be recorded before cancellation")
	}

	// Original input artifact must still exist
	if _, statErr := os.Stat(filepath.Join(directory, "输入.docx")); statErr != nil {
		t.Fatalf("original input was lost: %v", statErr)
	}
}

func TestLocalDOCXFailureRecordsCompletedSteps(t *testing.T) {
	home := t.TempDir()
	worker := newLocalDOCX(home)
	defer worker.close()

	// Server fails during generation
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/generate") {
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(`{"error":"model service offline"}`))
			return
		}
		w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()

	directory := t.TempDir()
	source, err := makeDOCX("资料")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(directory, "资料.docx"), source, 0600); err != nil {
		t.Fatal(err)
	}

	session := docxSession{
		Base:     server.URL,
		DeviceID: "dev1",
		UserID:   1,
		Token:    "token",
		Secret:   "secret",
		Context:  context.Background(),
	}
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}

	req := docxRequest{
		Action:      "run",
		SourceGrant: grant.ID,
		Source:      "资料.docx",
		OutputGrant: grant.ID,
		Output:      "失败产物.docx",
		Instruction: "汇总",
	}

	task, err := worker.run(session, req, func() error { return nil })
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	if task.Status != "failed" {
		t.Fatalf("expected task status failed, got: %s", task.Status)
	}
	if task.Error == "" {
		t.Fatal("expected failure reason in task.Error")
	}
	// Verify completed steps before failure are preserved
	if len(task.Steps) == 0 {
		t.Fatalf("expected completed steps to be recorded on failure, got: %+v", task.Steps)
	}
	if task.Steps[0] != "已读取源文件" {
		t.Fatalf("expected first step to be '已读取源文件', got: %q", task.Steps[0])
	}
}

func TestLocalDOCXParseCancelRequest(t *testing.T) {
	req, err := parseDOCXRequest(`{"action":"cancel","task_id":"task-123"}`)
	if err != nil {
		t.Fatalf("parse cancel request failed: %v", err)
	}
	if req.Action != "cancel" || req.TaskID != "task-123" {
		t.Fatalf("unexpected parsed request: %+v", req)
	}
}

