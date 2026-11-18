package main

import (
	"context"
	"encoding/json"
	"maps"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func checkOCRRequest(t *testing.T, r *http.Request, output string) map[string]string {
	t.Helper()
	var body map[string]string
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		t.Error(err)
	}
	var want map[string]string
	switch r.URL.Path {
	case "/api/devices/device/ocr/authorize":
		if body["action"] == "read" {
			want = map[string]string{"action": "read"}
		} else {
			want = map[string]string{"action": "create", "request_id": body["request_id"], "path": output}
		}
	case "/api/devices/device/file-events":
		want = map[string]string{"format": "ocr", "operation": "create", "request_id": body["request_id"], "path": output, "event": "completed"}
		if body["event"] == "failed" {
			want["event"], want["error_code"] = "failed", "failed"
		}
	}
	if want == nil || !maps.Equal(body, want) || (want["request_id"] != "" || want["path"] != "") && !validDestructiveID(body["request_id"]) {
		t.Errorf("unexpected OCR request: %s %v", r.URL.Path, body)
	}
	return body
}

func TestLocalOCRMissingResourcesFailsWithoutUploadingSource(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	var calls []string
	var receipt map[string]string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body := checkOCRRequest(t, r, "识别.txt")
		if handleFileAudit(w, r) {
			receipt = body
			return
		}
		calls = append(calls, body["action"])
		w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()
	worker := newLocalDOCX(t.TempDir())
	session := docxSession{Base: server.URL, DeviceID: "device", UserID: 1, Secret: "secret", Context: context.Background()}
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "扫描.pdf"), []byte("%PDF-1.7\nsource"), 0600); err != nil {
		t.Fatal(err)
	}
	grant, err := worker.grant(session, dir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	task, err := worker.run(session, docxRequest{Format: "ocr", SourceGrant: grant.ID, Source: "扫描.pdf", OutputGrant: grant.ID, Output: "识别.txt"}, func() error { return nil })
	if err == nil || !strings.Contains(err.Error(), "OCR") || task.Status != "failed" {
		t.Fatalf("%+v %v", task, err)
	}
	if strings.Join(calls, ",") != "create,read" || receipt["event"] != "failed" || receipt["request_id"] != task.ID {
		t.Fatalf("requests: %v; receipt: %v", calls, receipt)
	}
	if _, err := os.Stat(filepath.Join(dir, "识别.txt")); !os.IsNotExist(err) {
		t.Fatalf("created artifact: %v", err)
	}
	state, err := newLocalDOCX(worker.home).state(session)
	if err != nil || len(state.Tasks) != 1 || state.Tasks[0].Status != "failed" {
		t.Fatalf("state: %+v %v", state, err)
	}
}

func TestLocalOCRRejectsPathsAndUnauthorizedDirectories(t *testing.T) {
	worker := newLocalDOCX(t.TempDir())
	session := docxSession{Base: "https://example.test", DeviceID: "device", UserID: 1, Context: context.Background()}
	grant, err := worker.grant(session, t.TempDir(), "ro")
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"../outside.pdf", "/outside.pdf", `sub\outside.pdf`, "data.docx"} {
		task, err := worker.run(session, docxRequest{Format: "ocr", SourceGrant: grant.ID, Source: name, OutputGrant: grant.ID, Output: "result.txt"}, func() error { return nil })
		if err == nil || task.Status == "completed" {
			t.Fatalf("accepted %q", name)
		}
	}
	for _, name := range []string{"../outside.txt", "/outside.txt", `sub\outside.txt`, "data.pdf"} {
		if ocrOutputFilename(name) == nil {
			t.Fatalf("accepted output %q", name)
		}
	}
	for _, user := range []int{1, 2} {
		s := session
		s.UserID = user
		_, err := worker.run(s, docxRequest{Format: "ocr", SourceGrant: grant.ID, Source: "扫描.pdf", OutputGrant: grant.ID, Output: "结果.txt"}, func() error { return nil })
		if err == nil {
			t.Fatalf("accepted read-only or cross-user grant: %d", user)
		}
	}
}

func TestLocalOCRRealTask(t *testing.T) {
	home := os.Getenv("COLINK_TEST_OCR_HOME")
	if home == "" {
		t.Skip("requires staged offline OCR runtime (COLINK_TEST_OCR_HOME)")
	}
	t.Setenv("OCTOP_HOME", home)
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", "")
	source, err := os.ReadFile(filepath.Join("..", "..", "tests", "fixtures", "ocr", "scanned.pdf"))
	if err != nil {
		t.Fatal(err)
	}
	var calls []string
	var receipts []map[string]string
	denyWrite := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		output := "识别.txt"
		if denyWrite {
			output = "禁止.txt"
		}
		body := checkOCRRequest(t, r, output)
		if handleFileAudit(w, r) {
			receipts = append(receipts, body)
			return
		}
		calls = append(calls, body["action"])
		if denyWrite && body["action"] == "create" {
			w.WriteHeader(403)
			return
		}
		w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()
	session := docxSession{Base: server.URL, DeviceID: "device", UserID: 1, Context: context.Background()}
	worker := newLocalDOCX(t.TempDir())
	dir := t.TempDir()
	if err = os.WriteFile(filepath.Join(dir, "扫描.pdf"), source, 0600); err != nil {
		t.Fatal(err)
	}
	grant, err := worker.grant(session, dir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	request := docxRequest{Format: "ocr", SourceGrant: grant.ID, Source: "扫描.pdf", OutputGrant: grant.ID, Output: "识别.txt"}
	task, err := worker.run(session, request, func() error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	output, err := os.ReadFile(filepath.Join(dir, "识别.txt"))
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"第 1 页", "第 2 页", "12500", "CL2026", "张三"} {
		if !strings.Contains(string(output), key) {
			t.Errorf("missing %s in %s", key, output)
		}
	}
	if task.Status != "completed" || strings.Join(calls, ",") != "create,read,create" || len(receipts) != 1 || receipts[0]["event"] != "completed" || receipts[0]["request_id"] != task.ID {
		t.Fatalf("%+v requests=%v receipts=%v", task, calls, receipts)
	}
	failed, err := worker.run(session, request, func() error { return nil })
	if err == nil {
		t.Fatal("overwrote existing file")
	}
	preserved, _ := os.ReadFile(filepath.Join(dir, "识别.txt"))
	if string(preserved) != string(output) {
		t.Fatal("existing file changed")
	}
	denyWrite = true
	request.Output = "禁止.txt"
	task, err = worker.run(session, request, func() error { return nil })
	if err == nil || task.Status != "failed" {
		t.Fatalf("write denial: %+v %v", task, err)
	}
	if _, err = os.Stat(filepath.Join(dir, "禁止.txt")); !os.IsNotExist(err) {
		t.Fatalf("created file after revoked write: %v", err)
	}
	if len(receipts) != 2 || receipts[1]["event"] != "failed" || receipts[1]["request_id"] != failed.ID {
		t.Fatalf("unexpected audit receipts: %v", receipts)
	}
	state, err := newLocalDOCX(worker.home).state(session)
	if err != nil || len(state.Tasks) != 3 || state.Tasks[0].Status != "completed" || state.Tasks[2].Status != "failed" {
		t.Fatalf("restart state: %+v %v", state, err)
	}
}
