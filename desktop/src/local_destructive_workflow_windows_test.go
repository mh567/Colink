package main

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

func TestLocalFileOverwritePersistsAndReplaysTheConfirmedDocument(t *testing.T) {
	for _, allowed := range []bool{false, true} {
		name := "revoked while waiting"
		if allowed {
			name = "approved"
		}
		t.Run(name, func(t *testing.T) {
			var revoked atomic.Bool
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case revoked.Load():
					w.WriteHeader(http.StatusForbidden)
				case strings.HasSuffix(r.URL.Path, "/generate"):
					w.Write([]byte(`{"text":"更新后的周报"}`))
				default:
					w.Write([]byte(`{"allowed":true}`))
				}
			}))
			defer server.Close()

			directory := t.TempDir()
			path := filepath.Join(directory, "周报.docx")
			original, err := makeDOCX("原件")
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(path, original, 0600); err != nil {
				t.Fatal(err)
			}
			session := docxSession{Base: server.URL, DeviceID: "device", UserID: 1, Secret: "secret", Context: context.Background()}
			worker := newLocalDOCX(t.TempDir())
			t.Cleanup(func() {
				for _, root := range worker.roots {
					root.Close()
				}
			})
			grant, err := worker.grant(session, directory, "rw")
			if err != nil {
				t.Fatal(err)
			}
			request := docxRequest{
				Action: "overwrite", RequestID: "0123456789abcdef0123456789abcdef", Instruction: "整理",
				SourceGrant: grant.ID, Source: "周报.docx", OutputGrant: grant.ID, Output: "周报.docx",
			}
			worker.confirmDestructive = func(_ context.Context, _ Locale, operation, target string) (bool, error) {
				if operation != "overwrite" || target != path {
					t.Fatalf("confirmation for %q %q", operation, target)
				}
				data, readErr := os.ReadFile(path)
				if readErr != nil || !bytes.Equal(data, original) {
					t.Fatal("target changed before confirmation")
				}
				revoked.Store(!allowed)
				return true, nil
			}
			current := func() error { return nil }
			task, err := worker.run(session, request, current)
			result, readErr := os.ReadFile(path)
			if readErr != nil {
				t.Fatal(readErr)
			}
			if !allowed {
				if err == nil || task.Status != "failed" || !bytes.Equal(result, original) {
					t.Fatalf("revoked overwrite changed the file: %+v, %v", task, err)
				}
				return
			}
			if err != nil || task.Status != "completed" || task.Output != path {
				t.Fatalf("overwrite: %+v, %v", task, err)
			}
			if text, docErr := readDOCX(bytes.NewReader(result), int64(len(result))); docErr != nil || text != "更新后的周报\n" {
				t.Fatalf("updated document: %q, %v", text, docErr)
			}
			if err = os.WriteFile(path, original, 0600); err != nil {
				t.Fatal(err)
			}
			restarted := newLocalDOCX(worker.home)
			replay, err := restarted.run(session, request, current)
			if err != nil || replay != task {
				t.Fatalf("replayed result: %+v, %v", replay, err)
			}
			if result, readErr = os.ReadFile(path); readErr != nil || !bytes.Equal(result, original) {
				t.Fatal("replay changed the document again")
			}
		})
	}
}

func newDeleteWorkflow(t *testing.T, allowed *atomic.Bool) (*localDOCX, docxSession, docxRequest, string) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Colink-Device-Session") != "session-secret" || r.URL.Path != "/api/devices/device/docx/authorize" {
			t.Error("unexpected operation or missing desktop identity")
			w.WriteHeader(http.StatusForbidden)
			return
		}
		if !allowed.Load() {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		w.Write([]byte(`{"allowed":true}`))
	}))
	t.Cleanup(server.Close)
	dir := t.TempDir()
	name := "report.docx"
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	t.Cleanup(func() {
		for _, root := range worker.roots {
			root.Close()
		}
	})
	session := docxSession{Base: server.URL, DeviceID: "device", UserID: 1, Secret: "session-secret", Context: context.Background()}
	grant, err := worker.grant(session, dir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	request := docxRequest{Action: "delete", RequestID: "0123456789abcdef0123456789abcdef", OutputGrant: grant.ID, Output: name}
	return worker, session, request, filepath.Join(grant.Path, name)
}

func TestLocalFileDeleteChecksApprovalAndLatestPermission(t *testing.T) {
	for _, scenario := range []string{"cancelled", "revoked", "account changed", "replaced"} {
		t.Run(scenario, func(t *testing.T) {
			var allowed atomic.Bool
			allowed.Store(true)
			worker, session, request, path := newDeleteWorkflow(t, &allowed)
			calls := 0
			worker.confirmDestructive = func(_ context.Context, _ Locale, operation, target string) (bool, error) {
				calls++
				if operation != "delete" || target != path {
					t.Errorf("wrong operation or path presented: %q %q", operation, target)
				}
				switch scenario {
				case "cancelled":
					return false, nil
				case "revoked":
					allowed.Store(false)
				case "replaced":
					if err := os.Rename(path, path+".previous"); err != nil {
						return false, err
					}
					if err := os.WriteFile(path, []byte("replacement"), 0600); err != nil {
						return false, err
					}
				}
				return true, nil
			}
			task, err := worker.run(session, request, func() error {
				if scenario == "account changed" && calls > 0 {
					return errors.New("account changed")
				}
				return nil
			})
			if calls != 1 || task.Status == "completed" {
				t.Fatalf("unexpected execution: confirmations=%d status=%s err=%v", calls, task.Status, err)
			}
			if scenario == "cancelled" {
				if err != nil || task.Status != "cancelled" {
					t.Fatalf("cancel result: %+v %v", task, err)
				}
			} else if err == nil {
				t.Fatalf("operation accepted after %s", scenario)
			}
			expected := "original"
			if scenario == "replaced" {
				expected = "replacement"
				data, readErr := os.ReadFile(path + ".previous")
				if string(data) != "original" || readErr != nil {
					t.Fatalf("moved original changed: %q %v", data, readErr)
				}
			}
			data, readErr := os.ReadFile(path)
			if string(data) != expected || readErr != nil {
				t.Fatalf("target changed: %q %v", data, readErr)
			}
		})
	}
}

func TestLocalFileDeleteReplayReturnsResultWithoutDeletingReplacement(t *testing.T) {
	var allowed atomic.Bool
	allowed.Store(true)
	worker, session, request, path := newDeleteWorkflow(t, &allowed)
	prompts := 0
	worker.confirmDestructive = func(context.Context, Locale, string, string) (bool, error) {
		prompts++
		return true, nil
	}
	current := func() error { return nil }
	task, err := worker.run(session, request, current)
	if err != nil || task.Status != "completed" || prompts != 1 {
		t.Fatalf("delete: %+v, confirmations=%d, err=%v", task, prompts, err)
	}
	if _, err = os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("confirmed file remains: %v", err)
	}
	if err = os.WriteFile(path, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	restarted := newLocalDOCX(worker.home)
	restarted.confirmDestructive = worker.confirmDestructive
	replay, err := restarted.run(session, request, current)
	if err != nil || replay != task || prompts != 1 {
		t.Fatalf("replay: %+v, confirmations=%d, err=%v", replay, prompts, err)
	}
	request.Output = "other.docx"
	if _, err = restarted.run(session, request, current); err == nil {
		t.Fatal("changed target reused the same request ID")
	}
	other := session
	other.UserID++
	request.Output = "report.docx"
	if _, err = restarted.run(other, request, current); err == nil {
		t.Fatal("another account reused a grant or task")
	}
	data, err := os.ReadFile(path)
	if string(data) != "replacement" || err != nil || prompts != 1 {
		t.Fatalf("replacement changed: %q confirmations=%d err=%v", data, prompts, err)
	}
}
