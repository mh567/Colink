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
	"sync/atomic"
	"testing"
)

func TestLocalArtifactPreviewReturnsOnlyGeneratedBytesWithCurrentPermission(t *testing.T) {
	var allowRead atomic.Bool
	allowRead.Store(true)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Colink-Device-Session") != "secret" {
			t.Error("preview did not recheck the device and document permission")
		}
		switch {
		case strings.HasSuffix(r.URL.Path, "/docx/authorize"):
			var request map[string]string
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil || request["agent_id"] != "test-expert" {
				t.Errorf("wrong authorization request: %v, %v", request, err)
			}
			_ = json.NewEncoder(w).Encode(map[string]bool{"allowed": request["action"] != "read" || allowRead.Load()})
		case strings.HasSuffix(r.URL.Path, "/docx/generate"):
			_, _ = w.Write([]byte(`{"text":"周报示例"}`))
		case strings.HasSuffix(r.URL.Path, "/file-events"):
			_, _ = w.Write([]byte(`{"recorded":true}`))
		default:
			t.Errorf("unexpected request: %s", r.URL.Path)
		}
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
	if err := os.WriteFile(filepath.Join(grant.Path, "source.docx"), data, 0600); err != nil {
		t.Fatal(err)
	}
	run := func(name string) docxTask {
		t.Helper()
		task, err := worker.run(s, docxRequest{
			AgentID: "test-expert", SourceGrant: grant.ID, Source: "source.docx",
			OutputGrant: grant.ID, Output: name, Instruction: "整理",
		}, func() error { return nil })
		if err != nil || task.Status != "completed" {
			t.Fatalf("did not generate %s: %+v, %v", name, task, err)
		}
		return task
	}
	task := run("result.docx")
	restarted := newLocalDOCX(worker.home)
	defer restarted.close()
	got, err := restarted.previewArtifact(s, task.ID)
	want, readErr := os.ReadFile(task.Output)
	if err != nil || readErr != nil || !bytes.Equal(got, want) {
		t.Fatalf("preview did not return the generated document: %v", err)
	}
	other := s
	other.UserID++
	if _, err := restarted.previewArtifact(other, task.ID); err == nil {
		t.Fatal("another account read the result")
	}
	allowRead.Store(false)
	if _, err := restarted.previewArtifact(s, task.ID); err == nil {
		t.Fatal("preview read the result after document permission was denied")
	}
	allowRead.Store(true)
	unavailable := func(t *testing.T, id string) {
		t.Helper()
		got, err := restarted.previewArtifact(s, id)
		if len(got) != 0 || err == nil || err.Error() != "ARTIFACT_PREVIEW_CHANGED" {
			t.Fatalf("expected a changed or unverified result, got %d bytes and %v", len(got), err)
		}
	}
	t.Run("same name recreated", func(t *testing.T) {
		if err := os.Rename(task.Output, filepath.Join(grant.Path, "moved.docx")); err != nil {
			t.Fatal(err)
		}
		replacement, err := makeDOCX("别的任务生成的周报")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(task.Output, replacement, 0600); err != nil {
			t.Fatal(err)
		}
		unavailable(t, task.ID)
	})
	t.Run("same size and modification time", func(t *testing.T) {
		task := run("edited.docx")
		info, err := os.Stat(task.Output)
		if err != nil {
			t.Fatal(err)
		}
		edited, err := os.ReadFile(task.Output)
		if err != nil {
			t.Fatal(err)
		}
		edited[len(edited)/2] ^= 1
		if err := os.WriteFile(task.Output, edited, 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(task.Output, info.ModTime(), info.ModTime()); err != nil {
			t.Fatal(err)
		}
		unavailable(t, task.ID)
	})
	t.Run("legacy history", func(t *testing.T) {
		legacy := docxTask{ID: localID(), AgentID: "test-expert", Format: "docx", Status: "completed", Output: run("legacy.docx").Output}
		if err := worker.record(s, legacy); err != nil {
			t.Fatal(err)
		}
		unavailable(t, legacy.ID)
	})
	if err := worker.revoke(s, grant.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := restarted.previewArtifact(s, task.ID); err == nil || err.Error() == "ARTIFACT_PREVIEW_CHANGED" {
		t.Fatalf("directory revocation was not checked: %v", err)
	}
}
