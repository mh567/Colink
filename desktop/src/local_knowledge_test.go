package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestKnowledgeImportReadsOnlyAuthorizedDirectory(t *testing.T) {
	var received string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Colink-Device-Session") != "secret" {
			t.Error("native session missing")
		}
		if r.URL.Path == "/api/devices/device/knowledge/authorize" {
			_, _ = w.Write([]byte(`{"import_ticket":"confirmed"}`))
			return
		}
		if r.URL.Path != "/api/knowledge-bases/KB123/documents" {
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		if r.Header.Get("X-Colink-Knowledge-Import") != "confirmed" {
			t.Error("import confirmation missing")
		}
		file, _, err := r.FormFile("upload")
		if err != nil {
			t.Error(err)
			return
		}
		defer file.Close()
		content, _ := io.ReadAll(file)
		received = string(content)
		w.WriteHeader(http.StatusCreated)
	}))
	defer server.Close()
	directory := t.TempDir()
	if err := os.WriteFile(filepath.Join(directory, "sample.txt"), []byte("private"), 0600); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	session := docxSession{Base: server.URL, APIPath: "/api/devices/connect", DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
	grant, err := worker.grant(session, directory, "ro")
	if err != nil {
		t.Fatal(err)
	}
	if err := worker.importKnowledge(session, grant.ID, "../outside.txt", "KB123", "", func() error { return nil }); err == nil {
		t.Fatal("accepted a path outside the authorized directory")
	}
	if received != "" {
		t.Fatal("uploaded an unauthorized file")
	}
	if err := worker.importKnowledge(session, grant.ID, "sample.txt", "KB123", "", func() error { return nil }); err != nil {
		t.Fatal(err)
	}
	if received != "private" {
		t.Fatalf("unexpected content: %q", received)
	}
	received = ""
	if err := worker.revoke(session, grant.ID); err != nil {
		t.Fatal(err)
	}
	if err := worker.importKnowledge(session, grant.ID, "sample.txt", "KB123", "", func() error { return nil }); err == nil {
		t.Fatal("accepted a revoked directory grant")
	}
	if received != "" {
		t.Fatal("uploaded after directory authorization was revoked")
	}
}
