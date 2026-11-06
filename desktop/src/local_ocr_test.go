package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLocalOCRMissingResourcesFailsWithoutUploadingSource(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	var calls []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.URL.Path)
		var body map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		if len(body) != 1 || body["action"] != "read" {
			t.Errorf("unexpected upload: %v", body)
		}
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
	if len(calls) != 1 || !strings.HasSuffix(calls[0], "/ocr/authorize") {
		t.Fatalf("requests: %v", calls)
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
	denyWrite := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		json.NewDecoder(r.Body).Decode(&body)
		if len(body) != 1 || (body["action"] != "read" && body["action"] != "create") || !strings.HasSuffix(r.URL.Path, "/ocr/authorize") {
			t.Errorf("unexpected upload: %s %v", r.URL.Path, body)
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
	if task.Status != "completed" || strings.Join(calls, ",") != "read,create" {
		t.Fatalf("%+v requests=%v", task, calls)
	}
	_, err = worker.run(session, request, func() error { return nil })
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
	state, err := newLocalDOCX(worker.home).state(session)
	if err != nil || len(state.Tasks) != 3 || state.Tasks[0].Status != "completed" || state.Tasks[2].Status != "failed" {
		t.Fatalf("restart state: %+v %v", state, err)
	}
}
