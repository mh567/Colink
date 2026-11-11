package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
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
	if _, err = worker.run(session, request, func() error { return nil }); err == nil || err.Error() != "PPTX_OUTPUT_FOLDER" {
		t.Fatalf("expected read-only grant rejection, got %v", err)
	}
	if _, err = os.Stat(filepath.Join(dir, request.Output)); !os.IsNotExist(err) {
		t.Fatalf("unexpected output: %v", err)
	}
}

func TestLocalPPTXReportsInvalidDOCXSource(t *testing.T) {
	worker, session, request, _ := newPPTXTestTask(t, []byte("invalid DOCX"))
	mockPPTXTestServer(t, func(r *http.Request, _ map[string]string) (int, string) {
		if !strings.HasSuffix(r.URL.Path, "/authorize") {
			t.Errorf("submitted invalid source to %s", r.URL.Path)
		}
		return http.StatusOK, `{"allowed":true}`
	})
	task, err := worker.run(session, request, func() error { return nil })
	if err == nil || task.Status != "failed" || task.Error != "PPTX_INVALID_SOURCE" {
		t.Fatalf("task=%+v err=%v", task, err)
	}
}

func TestLocalPPTXChecksDOCXPermissionBeforeWriting(t *testing.T) {
	usePPTXTestRuntime(t)
	source, err := makeDOCX("验收 12 项")
	if err != nil {
		t.Fatal(err)
	}
	worker, session, request, output := newPPTXTestTask(t, source)
	var authorizedRead, authorizedOutput bool
	mockPPTXTestServer(t, func(r *http.Request, body map[string]string) (int, string) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/docx/authorize"):
			authorizedRead = body["action"] == "read"
		case strings.HasSuffix(r.URL.Path, "/pptx/generate"):
			if !authorizedRead || body["source_format"] != "docx" || !strings.Contains(body["text"], "验收 12 项") || len(body) != 3 {
				t.Errorf("unexpected generation request: %v", body)
			}
			return http.StatusOK, pptxTestReport
		case body["action"] == "create":
			authorizedOutput = true
			if body["source_format"] == "docx" {
				return http.StatusForbidden, `{"error":{"message":"DOCX permission was revoked"}}`
			}
		}
		return http.StatusOK, `{"allowed":true}`
	})
	task, err := worker.run(session, request, func() error { return nil })
	if !authorizedOutput || err == nil || task.Status != "failed" {
		t.Fatalf("output authorization=%v task=%+v err=%v", authorizedOutput, task, err)
	}
	if _, err = os.Stat(output); !os.IsNotExist(err) {
		t.Fatalf("wrote output after revocation: %v", err)
	}
}

func TestLocalPPTXPreservesConcurrentDestination(t *testing.T) {
	root, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	const name = "汇报.pptx"
	path := filepath.Join(root.Name(), name)
	const existing = "another process owns this file"
	session := docxSession{Apply: func(publish func() error) error {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("report visible before publication: %v", err)
		}
		if err := os.WriteFile(path, []byte(existing), 0600); err != nil {
			t.Fatal(err)
		}
		return publish()
	}}
	err = writeLocalPPTXOutput(session, root, name, []byte("generated report"), func() error { return nil })
	if err == nil || err.Error() != "PPTX_CREATE_ONLY" {
		t.Fatalf("expected an existing target error, got %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != existing {
		t.Fatalf("concurrent output was changed: %q, %v", data, err)
	}
	files, err := os.ReadDir(root.Name())
	if err != nil || len(files) != 1 {
		t.Fatalf("staged output remains: %v, %v", files, err)
	}
}

func TestLocalPPTXRevocationDuringStagingStopsPublication(t *testing.T) {
	usePPTXTestRuntime(t)
	source, err := makeDOCX("验收 12 项")
	if err != nil {
		t.Fatal(err)
	}
	worker, session, request, output := newPPTXTestTask(t, source)
	var createChecks int
	const denied = "DOCX permission was revoked"
	mockPPTXTestServer(t, func(r *http.Request, body map[string]string) (int, string) {
		if strings.HasSuffix(r.URL.Path, "/generate") {
			return http.StatusOK, pptxTestReport
		}
		if body["action"] == "create" {
			createChecks++
			if createChecks == 2 {
				if body["source_format"] != "docx" {
					t.Errorf("missing source format: %v", body)
				}
				files, err := os.ReadDir(filepath.Dir(output))
				if err != nil || len(files) != 2 || files[0].Name() == request.Output || files[1].Name() == request.Output {
					t.Errorf("expected staged output before publication: %v, %v", files, err)
				}
				return http.StatusForbidden, `{"error":{"message":"` + denied + `"}}`
			}
		}
		return http.StatusOK, `{"allowed":true}`
	})
	task, err := worker.run(session, request, func() error { return nil })
	if createChecks != 2 || err == nil || task.Status != "failed" || task.Error != denied {
		t.Fatalf("create checks=%d task=%+v err=%v", createChecks, task, err)
	}
	files, err := os.ReadDir(filepath.Dir(output))
	if err != nil || len(files) != 1 || files[0].Name() != request.Source {
		t.Fatalf("output or staging file remains after revocation: %v, %v", files, err)
	}
}

const pptxTestReport = `{"text":"{\"slides\":[{\"title\":\"项目汇报\",\"bullets\":[\"验收 12 项\"]}]}"}`

type pptxTestTransport func(*http.Request) (*http.Response, error)

func (f pptxTestTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func mockPPTXTestServer(t *testing.T, respond func(*http.Request, map[string]string) (int, string)) {
	t.Helper()
	transport := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = transport })
	http.DefaultTransport = pptxTestTransport(func(r *http.Request) (*http.Response, error) {
		defer r.Body.Close()
		var body map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			return nil, err
		}
		status, response := respond(r, body)
		return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(response))}, nil
	})
}

func newPPTXTestTask(t *testing.T, source []byte) (*localDOCX, docxSession, docxRequest, string) {
	t.Helper()
	worker := newLocalDOCX(t.TempDir())
	dir := t.TempDir()
	t.Cleanup(func() { closePPTXTestGrants(worker) })
	session := docxSession{Base: "https://example.test", UserID: 1, DeviceID: "mine", Token: "token", Secret: "secret", Context: context.Background()}
	grant, err := worker.grant(session, dir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	request := docxRequest{
		Format: "pptx", SourceGrant: grant.ID, OutputGrant: grant.ID,
		Source: "资料.docx", Output: "汇报.pptx", Instruction: "整理验收情况",
	}
	if err := os.WriteFile(filepath.Join(dir, request.Source), source, 0600); err != nil {
		t.Fatal(err)
	}
	return worker, session, request, filepath.Join(dir, request.Output)
}

func usePPTXTestRuntime(t *testing.T) {
	t.Helper()
	home := os.Getenv("COLINK_TEST_PPTX_HOME")
	if home == "" {
		t.Skip("requires staged offline presentation runtime (COLINK_TEST_PPTX_HOME)")
	}
	t.Setenv("OCTOP_HOME", home)
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", "")
}

func TestLocalPPTXTaskWithOfflineRuntime(t *testing.T) {
	usePPTXTestRuntime(t)
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
