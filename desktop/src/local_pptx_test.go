package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLocalPPTXCannotWriteToReadOnlyDirectory(t *testing.T) {
	session := docxSession{UserID: 1, DeviceID: "mine", Context: context.Background()}
	worker := newLocalDOCX(t.TempDir())
	defer closePPTXTestGrants(worker)
	dir := t.TempDir()
	grant, err := worker.grant(session, dir, "ro")
	if err != nil {
		t.Fatal(err)
	}
	request := docxRequest{
		Format: "pptx", SourceGrant: grant.ID, OutputGrant: grant.ID,
		Source: "资料.pptx", Output: "汇报.pptx", Instruction: "整理项目进展",
	}
	if _, err = worker.run(session, request, func() error { return nil }); err == nil || err.Error() != "只读目录不能写入" {
		t.Fatalf("expected read-only grant rejection, got %v", err)
	}
	if _, err = os.Stat(filepath.Join(dir, request.Output)); !os.IsNotExist(err) {
		t.Fatalf("unexpected output: %v", err)
	}
}

func TestLocalPPTXTaskWithOfflineRuntime(t *testing.T) {
	home := os.Getenv("COLINK_TEST_PPTX_HOME")
	if home == "" {
		t.Skip("requires staged offline presentation runtime (COLINK_TEST_PPTX_HOME)")
	}
	t.Setenv("OCTOP_HOME", home)
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", "")
	const report = `{"slides":[{"title":"项目汇报","bullets":["已完成 12 项"]}]}`
	requests := make(chan map[string]string, 2)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/devices/mine/pptx/") || r.Header.Get("X-Colink-Device-Session") != "secret" {
			t.Errorf("incorrect presentation device request %s", r.URL.Path)
			w.WriteHeader(http.StatusForbidden)
			return
		}
		if strings.HasSuffix(r.URL.Path, "/generate") {
			var sent map[string]string
			if err := json.NewDecoder(r.Body).Decode(&sent); err != nil {
				t.Error(err)
			}
			requests <- sent
			json.NewEncoder(w).Encode(map[string]string{"text": report})
			return
		}
		_, _ = w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()

	ctx := context.Background()
	original, err := makeLocalPPTX(ctx, `{"slides":[{"title":"项目进展","bullets":["完成 12 项"]}]}`)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	sourceDir, outputDir := filepath.Join(root, "资料"), filepath.Join(root, "成果")
	for _, dir := range []string{sourceDir, outputDir} {
		if err := os.Mkdir(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}
	sourcePath := filepath.Join(sourceDir, "项目进展.pptx")
	if err := os.WriteFile(sourcePath, original, 0600); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	defer closePPTXTestGrants(worker)
	session := docxSession{Base: server.URL, UserID: 1, DeviceID: "mine", Token: "token", Secret: "secret", Context: ctx}
	source, err := worker.grant(session, sourceDir, "ro")
	if err != nil {
		t.Fatal(err)
	}
	output, err := worker.grant(session, outputDir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	request := docxRequest{
		Format: "pptx", SourceGrant: source.ID, OutputGrant: output.ID,
		Source: "项目进展.pptx", Output: "项目汇报.pptx", Instruction: "整理项目进展",
	}
	task, err := worker.run(session, request, func() error { return nil })
	if err != nil || task.Status != "completed" {
		t.Fatalf("task=%+v err=%v", task, err)
	}
	sent := <-requests
	if len(sent) != 2 || sent["instruction"] != request.Instruction || !strings.Contains(sent["text"], "完成 12 项") {
		t.Fatalf("expected only extracted slide text and instruction, got %v", sent)
	}
	outputPath := filepath.Join(outputDir, request.Output)
	data, err := os.ReadFile(outputPath)
	if err != nil {
		t.Fatal(err)
	}
	result, err := readLocalPPTX(ctx, bytes.NewReader(data), int64(len(data)))
	if err != nil || !strings.Contains(result, "项目汇报\n已完成 12 项") {
		t.Fatalf("result=%q err=%v", result, err)
	}
	if _, err := worker.run(session, request, func() error { return nil }); err == nil {
		t.Fatal("task overwrote an existing presentation")
	}
	unchanged, err := os.ReadFile(outputPath)
	if err != nil || !bytes.Equal(unchanged, data) {
		t.Fatal("output changed after retry")
	}
	unchanged, err = os.ReadFile(sourcePath)
	if err != nil || !bytes.Equal(unchanged, original) {
		t.Fatal("source was changed")
	}
	state, err := newLocalDOCX(worker.home).state(session)
	if err != nil || len(state.Tasks) != 2 || state.Tasks[0].Status != "completed" || state.Tasks[1].Status != "failed" || state.Tasks[0].Output != outputPath {
		t.Fatalf("state=%+v err=%v", state, err)
	}
}

func closePPTXTestGrants(worker *localDOCX) {
	for _, root := range worker.roots {
		root.Close()
	}
}
