package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLocalArtifactPreviewRequiresCurrentGrantAndCompletedTask(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/docx/authorize") || r.Header.Get("X-Colink-Device-Session") != "secret" {
			t.Error("preview did not recheck the device and document permission")
		}
		_, _ = w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()
	s := docxSession{Base: server.URL, DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
	worker := newLocalDOCX(t.TempDir())
	defer worker.close()
	grant, err := worker.grant(s, t.TempDir(), "rw")
	if err != nil {
		t.Fatal(err)
	}
	data, err := makeDOCX("周报示例")
	if err != nil {
		t.Fatal(err)
	}
	task := docxTask{ID: localID(), Format: "docx", Status: "completed", Output: filepath.Join(grant.Path, "result.docx")}
	if err := os.WriteFile(task.Output, data, 0600); err != nil {
		t.Fatal(err)
	}
	if err := worker.record(s, task); err != nil {
		t.Fatal(err)
	}
	got, err := worker.previewArtifact(s, task.ID)
	if err != nil || !bytes.Equal(got, data) {
		t.Fatalf("preview did not return the generated document: %v", err)
	}
	other := s
	other.UserID++
	if _, err := worker.previewArtifact(other, task.ID); err == nil {
		t.Fatal("another account read the result")
	}
	if err := worker.revoke(s, grant.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := worker.previewArtifact(s, task.ID); err == nil {
		t.Fatal("preview read the result after directory revocation")
	}
}
