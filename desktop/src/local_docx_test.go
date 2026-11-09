package main

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestLocalDOCXTaskCreatesEditableDocumentWithoutUploadingOriginal(t *testing.T) {
	var sent, sentAgent string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Colink-Device-Session") != "secret" {
			t.Error("missing native session")
		}
		if strings.HasSuffix(r.URL.Path, "/generate") {
			var body struct {
				Text    string `json:"text"`
				AgentID string `json:"agent_id"`
			}
			json.NewDecoder(r.Body).Decode(&body)
			sent = body.Text
			sentAgent = body.AgentID
			json.NewEncoder(w).Encode(map[string]string{"text": "整理结果\n第二段"})
		} else {
			var body map[string]string
			json.NewDecoder(r.Body).Decode(&body)
			if body["agent_id"] != "private-expert" {
				t.Error("expert not checked before file operation")
			}
			w.Write([]byte(`{"allowed":true}`))
		}
	}))
	defer server.Close()
	directory := t.TempDir()
	source, err := makeDOCX("原始中文内容")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(directory, "输入.docx"), source, 0600); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	session := docxSession{Base: server.URL, APIPath: "/api/devices/connect", DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}
	task, err := worker.run(session, docxRequest{AgentID: "private-expert", SourceGrant: grant.ID, Source: "输入.docx", OutputGrant: grant.ID, Output: "成果.docx", Instruction: "整理"}, func() error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if task.Output != filepath.Join(grant.Path, "成果.docx") {
		t.Fatalf("missing artifact path: %q", task.Output)
	}
	if task.Status != "completed" || sent != "原始中文内容\n" {
		t.Fatalf("task=%+v uploaded=%q", task, sent)
	}
	state, err := newLocalDOCX(worker.home).state(session)
	if err != nil || sentAgent != "private-expert" || len(state.Tasks) != 1 || state.Tasks[0].AgentID != "private-expert" {
		t.Fatalf("expert not forwarded or persisted: %q %+v %v", sentAgent, state, err)
	}
	result, err := os.ReadFile(filepath.Join(directory, "成果.docx"))
	if err != nil {
		t.Fatal(err)
	}
	z, err := zip.NewReader(bytes.NewReader(result), int64(len(result)))
	if err != nil {
		t.Fatal(err)
	}
	if len(z.File) != 3 {
		t.Fatalf("invalid DOCX parts: %d", len(z.File))
	}
	text, err := readDOCX(bytes.NewReader(result), int64(len(result)))
	if err != nil || text != "整理结果\n第二段\n" {
		t.Fatalf("%q %v", text, err)
	}
	if _, err = worker.run(session, docxRequest{AgentID: "private-expert", SourceGrant: grant.ID, Source: "输入.docx", OutputGrant: grant.ID, Output: "成果.docx", Instruction: "整理"}, func() error { return nil }); err == nil {
		t.Fatal("overwrote existing file")
	}
}

func TestLocalDOCXRejectsUnauthorizedPathsAndWrites(t *testing.T) {
	directory := t.TempDir()
	worker := newLocalDOCX(t.TempDir())
	session := docxSession{Base: "https://example.test", DeviceID: "device", UserID: 1}
	grant, err := worker.grant(session, directory, "ro")
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"../outside.docx", "/outside.docx", "sub/file.docx", `sub\file.docx`, "file.txt"} {
		if err = docxFilename(name); err == nil {
			t.Errorf("accepted %s", name)
		}
	}
	if _, err = worker.openGrant(session, grant.ID, true); err == nil {
		t.Fatal("read-only write allowed")
	}
	other := session
	other.UserID = 2
	if _, err = worker.openGrant(other, grant.ID, false); err == nil {
		t.Fatal("cross-user grant allowed")
	}
	other = session
	other.DeviceID = "other"
	if _, err = worker.openGrant(other, grant.ID, false); err == nil {
		t.Fatal("cross-device grant allowed")
	}
}

func TestLocalDOCXRejectsLinksAndChangedDirectory(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("link creation needs Windows privileges")
	}
	directory := t.TempDir()
	outside := t.TempDir()
	worker := newLocalDOCX(t.TempDir())
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"allowed":true}`)) }))
	defer server.Close()
	session := docxSession{Base: server.URL, APIPath: "/api/devices/connect", DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
	data, _ := makeDOCX("不应读取")
	os.WriteFile(filepath.Join(outside, "outside.docx"), data, 0600)
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"symlink", "hardlink"} {
		name := kind + ".docx"
		if kind == "symlink" {
			err = os.Symlink(filepath.Join(outside, "outside.docx"), filepath.Join(directory, name))
		} else {
			err = os.Link(filepath.Join(outside, "outside.docx"), filepath.Join(directory, name))
		}
		if err != nil {
			t.Fatal(err)
		}
		_, err = worker.run(session, docxRequest{SourceGrant: grant.ID, Source: name, OutputGrant: grant.ID, Output: "out.docx", Instruction: "整理"}, func() error { return nil })
		if err == nil {
			t.Fatalf("accepted %s", kind)
		}
		if _, err = os.Stat(filepath.Join(directory, "out.docx")); !os.IsNotExist(err) {
			t.Fatal("created output after link rejection")
		}
	}
	restored := newLocalDOCX(worker.home)
	if _, err = restored.openGrant(session, grant.ID, false); err != nil {
		t.Fatal("did not restore original grant", err)
	}
	for _, r := range restored.roots {
		r.Close()
	}
	if err = os.Rename(directory, directory+"-old"); err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(directory + "-old")
	if err = os.Mkdir(directory, 0700); err != nil {
		t.Fatal(err)
	}
	if _, err = worker.openGrant(session, grant.ID, false); err == nil {
		t.Fatal("cached grant accepted replacement directory")
	}
	if _, err = newLocalDOCX(worker.home).openGrant(session, grant.ID, false); err == nil {
		t.Fatal("restored replacement directory")
	}
}

func TestLocalDOCXChecksWritePermissionAfterModelAndSessionBeforeWriting(t *testing.T) {
	for _, test := range []string{"permission", "session"} {
		t.Run(test, func(t *testing.T) {
			modelCalled := false
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasSuffix(r.URL.Path, "/generate") {
					modelCalled = true
					w.Write([]byte(`{"text":"结果"}`))
					return
				}
				var body struct {
					Action string `json:"action"`
				}
				json.NewDecoder(r.Body).Decode(&body)
				if body.Action == "create" && test == "permission" {
					w.WriteHeader(http.StatusForbidden)
					w.Write([]byte(`{"error":{"message":"已撤销写入权限"}}`))
					return
				}
				w.Write([]byte(`{"allowed":true}`))
			}))
			defer server.Close()
			directory := t.TempDir()
			data, _ := makeDOCX("原文")
			os.WriteFile(filepath.Join(directory, "input.docx"), data, 0600)
			worker := newLocalDOCX(t.TempDir())
			session := docxSession{Base: server.URL, APIPath: "/api/devices/connect", DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
			grant, err := worker.grant(session, directory, "rw")
			if err != nil {
				t.Fatal(err)
			}
			_, err = worker.run(session, docxRequest{SourceGrant: grant.ID, Source: "input.docx", OutputGrant: grant.ID, Output: "out.docx", Instruction: "整理"}, func() error {
				if modelCalled && test == "session" {
					return fmt.Errorf("已断开设备")
				}
				return nil
			})
			if err == nil || !modelCalled {
				t.Fatalf("model=%v err=%v", modelCalled, err)
			}
			if _, err = os.Stat(filepath.Join(directory, "out.docx")); !os.IsNotExist(err) {
				t.Fatal("created output after denial")
			}
		})
	}
}

func TestLocalDOCXDispatchRejectsExecuteAndPathsInGrant(t *testing.T) {
	for _, body := range []string{`{"action":"execute","command":"touch bad"}`, `{"action":"grant","mode":"rw","path":"/"}`, `{"action":"list"}{"action":"execute"}`} {
		if _, err := parseDOCXRequest(url.QueryEscape(body)); err == nil {
			t.Fatal("accepted unsafe action", body)
		}
	}
}

func TestLocalDOCXDispatchAcceptsIntentButNotWebConfirmation(t *testing.T) {
	for _, action := range []string{"overwrite", "delete"} {
		t.Run(action, func(t *testing.T) {
			body := fmt.Sprintf(`{"action":%q,"request_id":"0123456789abcdef0123456789abcdef","output_grant":"grant","output":"report.docx"}`, action)
			if request, err := parseDOCXRequest(url.QueryEscape(body)); err != nil || request.Action != action {
				t.Fatalf("valid intent was rejected: %+v, %v", request, err)
			}
			confirmed := strings.TrimSuffix(body, "}") + `,"confirmed":true}`
			if _, err := parseDOCXRequest(url.QueryEscape(confirmed)); err == nil {
				t.Fatal("web request asserted native confirmation")
			}
			if _, err := parseDOCXRequest(url.QueryEscape(fmt.Sprintf(`{"action":%q}`, action))); err == nil {
				t.Fatal("destructive intent without a replay key was accepted")
			}
		})
	}
}

func TestLocalDOCXDestructiveActionDoesNotFallThroughToCreate(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/generate") {
			w.Write([]byte(`{"text":"unexpected result"}`))
			return
		}
		w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()
	dir := t.TempDir()
	data, err := makeDOCX("original")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, "source.docx"), data, 0600); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	session := docxSession{Base: server.URL, UserID: 1, DeviceID: "device", Secret: "secret", Context: context.Background()}
	grant, err := worker.grant(session, dir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		for _, root := range worker.roots {
			root.Close()
		}
	}()
	for _, action := range []string{"overwrite", "delete"} {
		request := docxRequest{Action: action, SourceGrant: grant.ID, Source: "source.docx", OutputGrant: grant.ID, Output: action + ".docx", Instruction: "summarize"}
		if _, err = worker.run(session, request, func() error { return nil }); err == nil {
			t.Fatalf("direct %s without a confirmation intent was executed", action)
		}
		if _, err = os.Stat(filepath.Join(dir, request.Output)); !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("direct %s created a file: %v", action, err)
		}
	}
}

func TestLocalDOCXSessionSecretNeverReachesDashboard(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var message deviceMessage
	client := deviceClient{generation: 1, docx: docxSession{Base: "https://example.test", Generation: 1, Context: ctx}, emit: func(m deviceMessage) { message = m }}
	client.publish(1, deviceMessage{Type: "status", Status: "approved", Online: true, DeviceID: "device", SessionToken: "secret", UserID: 12})
	if message.SessionToken != "" || message.UserID != 0 {
		t.Fatal("private identity leaked")
	}
	session, err := client.docxSession()
	if err != nil || session.Secret != "secret" || session.UserID != 12 {
		t.Fatalf("%+v %v", session, err)
	}
	client.disconnect()
	if client.currentDOCX(session) == nil {
		t.Fatal("disconnected session can execute")
	}
}

func TestLocalDOCXLimitsAndRestartState(t *testing.T) {
	if _, err := makeDOCX(strings.Repeat("中", docxTextLimit+1)); err == nil {
		t.Fatal("accepted oversized output")
	}
	if _, err := readDOCX(bytes.NewReader([]byte("not zip")), 7); err == nil {
		t.Fatal("accepted invalid docx")
	}
	session := docxSession{Base: "https://example.test", DeviceID: "device", UserID: 1}
	worker := newLocalDOCX(t.TempDir())
	if err := worker.record(session, docxTask{ID: "interrupted", Status: "running", Output: "成果.docx"}); err != nil {
		t.Fatal(err)
	}
	state, err := newLocalDOCX(worker.home).state(session)
	if err != nil {
		t.Fatal(err)
	}
	if len(state.Tasks) != 1 || state.Tasks[0].Status != "interrupted" {
		t.Fatalf("lost interrupted task: %+v", state)
	}
}

func TestLocalDOCXRefreshesTokenAfterGenerationAndPreservesEncodedPrefix(t *testing.T) {
	client := &deviceClient{generation: 1, renewals: make(chan string, 1)}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/企业 空间/api/devices/device/docx/") {
			t.Errorf("incorrect prefix: %s", r.URL.Path)
		}
		if strings.HasSuffix(r.URL.Path, "/generate") {
			client.refresh("renewed-token")
			w.Write([]byte(`{"text":"结果"}`))
			return
		}
		var body struct {
			Action string `json:"action"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		expected := "old-token"
		if body.Action == "create" {
			expected = "renewed-token"
		}
		if r.Header.Get("Authorization") != "Bearer "+expected {
			t.Errorf("%s used stale token", body.Action)
			w.WriteHeader(401)
			return
		}
		w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()
	client.docx = docxSession{Base: server.URL, APIPath: "/%E4%BC%81%E4%B8%9A%20%E7%A9%BA%E9%97%B4/api/devices/connect", DeviceID: "device", UserID: 1, Token: "old-token", Secret: "secret", Generation: 1, Context: context.Background()}
	session, err := client.docxSession()
	if err != nil {
		t.Fatal(err)
	}
	directory := t.TempDir()
	data, _ := makeDOCX("原文")
	os.WriteFile(filepath.Join(directory, "input.docx"), data, 0600)
	worker := newLocalDOCX(t.TempDir())
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}
	_, err = worker.run(session, docxRequest{SourceGrant: grant.ID, Source: "input.docx", OutputGrant: grant.ID, Output: "out.docx", Instruction: "整理"}, func() error { return client.currentDOCX(session) })
	if err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(filepath.Join(directory, "out.docx")); err != nil {
		t.Fatal(err)
	}
}
