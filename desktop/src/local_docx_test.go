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
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestLocalDOCXTaskCreatesEditableDocumentWithoutUploadingOriginal(t *testing.T) {
	var sent, sentAgent string
	var receipts []map[string]string
	available := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Colink-Device-Session") != "secret" {
			t.Error("missing native session")
		}
		if strings.HasSuffix(r.URL.Path, "/file-events") {
			var body map[string]string
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			want := 5
			if body["event"] == "failed" && body["error_code"] == "failed" {
				want++
			}
			if len(body) != want || !validDestructiveID(body["request_id"]) || body["format"] != "docx" || body["operation"] != "create" || body["path"] != "成果.docx" {
				t.Errorf("unexpected audit body: %v", body)
			}
			if !available {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			receipts = append(receipts, body)
			w.Write([]byte(`{"recorded":true}`))
		} else if strings.HasSuffix(r.URL.Path, "/generate") {
			var body struct {
				Text    string `json:"text"`
				AgentID string `json:"agent_id"`
			}
			json.NewDecoder(r.Body).Decode(&body)
			sent = body.Text
			sentAgent = body.AgentID
			json.NewEncoder(w).Encode(map[string]string{"text": "整理结果\n第二段"})
		} else if strings.HasSuffix(r.URL.Path, "/authorize") {
			var body map[string]string
			json.NewDecoder(r.Body).Decode(&body)
			if body["agent_id"] != "private-expert" {
				t.Error("expert not checked before file operation")
			}
			w.Write([]byte(`{"allowed":true}`))
		} else {
			t.Errorf("unexpected request: %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
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
	defer worker.close()
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
	available = true
	worker = newLocalDOCX(worker.home)
	defer worker.close()
	retried := make(chan struct{})
	client := &deviceClient{docx: session, retryAudit: func(s docxSession) {
		worker.retryFileAudit(s)
		close(retried)
	}}
	client.publish(0, deviceMessage{Type: "status", Status: "approved", Online: true, DeviceID: session.DeviceID, UserID: session.UserID, SessionToken: session.Secret})
	select {
	case <-retried:
	case <-time.After(5 * time.Second):
		t.Fatal("approved device reconnect did not retry audit")
	}
	if len(receipts) != 1 || receipts[0]["request_id"] != task.ID || receipts[0]["event"] != "completed" {
		t.Fatalf("missing completed receipt after reconnect: %v", receipts)
	}
	failed, err := worker.run(session, docxRequest{AgentID: "private-expert", SourceGrant: grant.ID, Source: "输入.docx", OutputGrant: grant.ID, Output: "成果.docx", Instruction: "整理"}, func() error { return nil })
	if err == nil {
		t.Fatal("overwrote existing file")
	}
	if len(receipts) != 2 || receipts[0]["request_id"] != task.ID || receipts[0]["event"] != "completed" || receipts[1]["request_id"] != failed.ID || receipts[1]["event"] != "failed" {
		t.Fatalf("missing ordered receipts after restart: %v", receipts)
	}
}

func TestLocalDOCXRejectsUnauthorizedPathsAndWrites(t *testing.T) {
	directory := t.TempDir()
	worker := newLocalDOCX(t.TempDir())
	defer worker.close()
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
	defer worker.close()
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
	checkWorker := newLocalDOCX(worker.home)
	defer checkWorker.close()
	if _, err = checkWorker.openGrant(session, grant.ID, false); err == nil {
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
				if body.Action == "create" && modelCalled && test == "permission" {
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
			defer worker.close()
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

func TestLocalDOCXOutputWriteHoldsGrantLock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/generate") {
			w.Write([]byte(`{"text":"结果"}`))
			return
		}
		w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()

	directory := t.TempDir()
	source, err := makeDOCX("原文")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(directory, "input.docx"), source, 0600); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	defer worker.close()
	session := docxSession{Base: server.URL, APIPath: "/api/devices/connect", DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}
	applyCalls := 0
	session.Apply = func(apply func() error) error {
		applyCalls++
		if worker.mu.TryLock() {
			worker.mu.Unlock()
			t.Error("output write did not hold the grant lock")
		}
		return apply()
	}
	_, err = worker.run(session, docxRequest{SourceGrant: grant.ID, Source: "input.docx", OutputGrant: grant.ID, Output: "output.docx", Instruction: "整理"}, func() error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if applyCalls != 1 {
		t.Fatalf("output write ran through Apply %d times, want 1", applyCalls)
	}
}

func TestLocalDOCXStopsBeforeSendingOrWritingAfterRevocation(t *testing.T) {
	for _, revoke := range []string{"source", "device"} {
		t.Run(revoke, func(t *testing.T) {
			var calls atomic.Int32
			var deviceDenied atomic.Bool
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case strings.HasSuffix(r.URL.Path, "/generate"):
					calls.Add(1)
					w.Write([]byte(`{"text":"结果"}`))
				case strings.HasSuffix(r.URL.Path, "/file-events"):
					w.Write([]byte(`{"recorded":true}`))
				case deviceDenied.Load():
					w.WriteHeader(http.StatusForbidden)
				default:
					w.Write([]byte(`{"allowed":true}`))
				}
			}))
			defer server.Close()
			dir := t.TempDir()
			data, err := makeDOCX("原文")
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(filepath.Join(dir, "input.docx"), data, 0600); err != nil {
				t.Fatal(err)
			}
			worker := newLocalDOCX(t.TempDir())
			defer worker.close()
			session := docxSession{Base: server.URL, DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
			source, err := worker.grant(session, dir, "ro")
			if err != nil {
				t.Fatal(err)
			}
			outDir := t.TempDir()
			output, err := worker.grant(session, outDir, "rw")
			if err != nil {
				t.Fatal(err)
			}
			preserved := filepath.Join(outDir, "previous.docx")
			if err = os.WriteFile(preserved, data, 0600); err != nil {
				t.Fatal(err)
			}
			refreshes := 0
			current := func() error {
				if revoke == "device" && calls.Load() > 0 {
					deviceDenied.Store(true)
				}
				return nil
			}
			session.Refresh = func() (docxSession, error) {
				refreshes++
				if revoke == "source" && refreshes == 3 {
					if err := worker.revoke(session, source.ID); err != nil {
						t.Fatal(err)
					}
				}
				return session, nil
			}
			req := docxRequest{SourceGrant: source.ID, Source: "input.docx", OutputGrant: output.ID, Output: "output.docx", Instruction: "整理"}
			task, err := worker.run(session, req, current)
			if err == nil || task.Status != "failed" {
				t.Errorf("revoked task must fail: %+v, %v", task, err)
			}
			if revoke == "source" && calls.Load() != 0 {
				t.Error("started a model request after the directory was revoked")
			}
			if _, err := os.Stat(filepath.Join(outDir, req.Output)); !os.IsNotExist(err) {
				t.Errorf("created output after revocation: %v", err)
			}
			if got, err := os.ReadFile(preserved); err != nil || !bytes.Equal(got, data) {
				t.Errorf("changed completed output: %v", err)
			}
		})
	}
}

func TestLocalDOCXRevokeDuringModelCallStopsOutput(t *testing.T) {
	for _, revokedGrant := range []string{"source", "output"} {
		t.Run(revokedGrant, func(t *testing.T) {
			enteredGenerate := make(chan struct{})
			releaseGenerate := make(chan struct{})
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasSuffix(r.URL.Path, "/generate") {
					close(enteredGenerate)
					<-releaseGenerate
					w.Write([]byte(`{"text":"结果"}`))
					return
				}
				w.Write([]byte(`{"allowed":true}`))
			}))
			t.Cleanup(server.Close)

			session := docxSession{Base: server.URL, APIPath: "/api/devices/connect", DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Generation: 1, Context: context.Background()}
			otherSession := session
			otherSession.DeviceID = "other-device"
			sourceDir, outputDir, otherDir := t.TempDir(), t.TempDir(), t.TempDir()
			source, err := makeDOCX("原文")
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(filepath.Join(sourceDir, "input.docx"), source, 0600); err != nil {
				t.Fatal(err)
			}
			completed, err := makeDOCX("已完成内容")
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(filepath.Join(outputDir, "completed.docx"), completed, 0600); err != nil {
				t.Fatal(err)
			}
			worker := newLocalDOCX(t.TempDir())
			defer worker.close()
			sourceGrant, err := worker.grant(session, sourceDir, "ro")
			if err != nil {
				t.Fatal(err)
			}
			outputGrant, err := worker.grant(session, outputDir, "rw")
			if err != nil {
				t.Fatal(err)
			}
			otherGrant, err := worker.grant(otherSession, otherDir, "rw")
			if err != nil {
				t.Fatal(err)
			}

			events := make(chan docxEvent, 8)
			app := &App{
				store:  &settingsStore{cur: defaultSettings()},
				device: &deviceClient{generation: 1, docx: session},
				docx:   worker,
				docxStatusSink: func(event docxEvent) {
					events <- event
				},
			}
			encode := func(request docxRequest) string {
				data, marshalErr := json.Marshal(request)
				if marshalErr != nil {
					t.Fatal(marshalErr)
				}
				return url.QueryEscape(string(data))
			}
			done := make(chan struct{})
			go func() {
				app.handleDOCX(encode(docxRequest{Action: "run", SourceGrant: sourceGrant.ID, Source: "input.docx", OutputGrant: outputGrant.ID, Output: "result.docx", Instruction: "整理"}))
				close(done)
			}()
			var releaseOnce sync.Once
			release := func() { releaseOnce.Do(func() { close(releaseGenerate) }) }
			t.Cleanup(func() {
				release()
				select {
				case <-done:
				case <-time.After(5 * time.Second):
					t.Error("running task did not stop during cleanup")
				}
			})
			select {
			case <-enteredGenerate:
			case <-time.After(5 * time.Second):
				t.Fatal("model request did not start")
			}

			grant := sourceGrant
			if revokedGrant == "output" {
				grant = outputGrant
			}
			revokeDone := make(chan struct{})
			go func() {
				app.handleDOCX(encode(docxRequest{Action: "revoke", GrantID: grant.ID}))
				close(revokeDone)
			}()
			select {
			case <-revokeDone:
			case <-time.After(5 * time.Second):
				t.Fatal("revocation handler did not finish while a task was running")
			}
			if _, err = worker.openGrant(session, grant.ID, false); err == nil {
				t.Fatalf("revoked %s grant remained usable", revokedGrant)
			}
			if revokedGrant == "source" {
				if _, err = worker.openGrant(session, outputGrant.ID, true); err != nil {
					t.Fatalf("source grant revocation removed output grant: %v", err)
				}
			} else if _, err = worker.openGrant(session, sourceGrant.ID, false); err != nil {
				t.Fatalf("output grant revocation removed source grant: %v", err)
			}
			if _, err = worker.openGrant(otherSession, otherGrant.ID, true); err != nil {
				t.Fatalf("revocation affected another device: %v", err)
			}
			select {
			case <-done:
			case <-time.After(5 * time.Second):
				t.Fatal("revocation did not stop the task while the model was still running")
			}
			release()
			state, err := worker.state(session)
			if err != nil || len(state.Tasks) != 1 || state.Tasks[0].Status != "failed" || state.Tasks[0].Error == "" {
				t.Fatalf("revoked task was not recorded as failed: state=%+v err=%v", state, err)
			}
			if state.Tasks[0].Output != "" {
				t.Fatalf("revoked task retained output path %q", state.Tasks[0].Output)
			}
			if _, err = os.Stat(filepath.Join(outputDir, "result.docx")); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("created output after revocation: %v", err)
			}
			got, err := os.ReadFile(filepath.Join(outputDir, "completed.docx"))
			if err != nil || !bytes.Equal(got, completed) {
				t.Fatalf("changed completed output: err=%v equal=%v", err, bytes.Equal(got, completed))
			}
			var last docxEvent
			for {
				select {
				case last = <-events:
				default:
					if last.Busy || len(last.Tasks) != 1 || last.Tasks[0].Status != "failed" {
						t.Fatalf("final status event is incomplete: %+v", last)
					}
					return
				}
			}
		})
	}
}

func TestLocalDOCXStopsWhenTaskServiceDisconnects(t *testing.T) {
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Connection", "close")
		w.Write([]byte(`{"allowed":true}`))
		if err := server.Listener.Close(); err != nil {
			t.Errorf("close task service listener: %v", err)
		}
	}))
	t.Cleanup(server.Close)

	session := docxSession{Base: server.URL, APIPath: "/api/devices/connect", DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
	directory := t.TempDir()
	source, err := makeDOCX("原文")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(directory, "input.docx"), source, 0600); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	defer worker.close()
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}
	task, err := worker.run(session, docxRequest{SourceGrant: grant.ID, Source: "input.docx", OutputGrant: grant.ID, Output: "result.docx", Instruction: "整理"}, func() error { return nil })
	if err == nil || task.Status != "failed" || task.Error != "无法连接任务服务" {
		t.Fatalf("disconnect was not reported: task=%+v err=%v", task, err)
	}
	state, stateErr := worker.state(session)
	if stateErr != nil || len(state.Tasks) != 1 || state.Tasks[0].Status != "failed" || state.Tasks[0].Error != "无法连接任务服务" {
		t.Fatalf("failed task state was not recorded: %+v err=%v", state, stateErr)
	}
	if _, err = os.Stat(filepath.Join(directory, "result.docx")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("created output after disconnect: %v", err)
	}
}

func TestLocalDOCXEmitsFailedTaskAfterDeviceSessionDisconnects(t *testing.T) {
	enteredGenerate := make(chan struct{})
	releaseGenerate := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/generate") {
			close(enteredGenerate)
			<-releaseGenerate
			w.Write([]byte(`{"text":"结果"}`))
			return
		}
		w.Write([]byte(`{"allowed":true}`))
	}))
	t.Cleanup(server.Close)

	session := docxSession{Base: server.URL, APIPath: "/api/devices/connect", DeviceID: "device", UserID: 1, Token: "token", Secret: "secret", Generation: 1, Context: context.Background()}
	directory := t.TempDir()
	source, err := makeDOCX("原文")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(directory, "input.docx"), source, 0600); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	defer worker.close()
	sourceGrant, err := worker.grant(session, directory, "ro")
	if err != nil {
		t.Fatal(err)
	}
	outputGrant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}
	client := &deviceClient{generation: 1, docx: session}
	events := make(chan docxEvent, 4)
	app := &App{
		store:  &settingsStore{cur: defaultSettings()},
		device: client,
		docx:   worker,
		docxStatusSink: func(event docxEvent) {
			events <- event
		},
	}
	done := make(chan struct{})
	go func() {
		app.handleDOCX(url.QueryEscape(fmt.Sprintf(`{"action":"run","source_grant":%q,"source":"input.docx","output_grant":%q,"output":"result.docx","instruction":"整理"}`, sourceGrant.ID, outputGrant.ID)))
		close(done)
	}()
	t.Cleanup(func() {
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Error("task did not stop during cleanup")
		}
	})
	select {
	case <-enteredGenerate:
	case <-time.After(5 * time.Second):
		t.Fatal("model request did not start")
	}

	client.publish(1, deviceMessage{Type: "error", Code: "DEVICE_CLIENT_ERROR", Message: "connection closed"})
	client.mu.Lock()
	client.docx = docxSession{}
	client.mu.Unlock()
	close(releaseGenerate)
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("task did not finish after disconnect")
	}

	var last docxEvent
	for {
		select {
		case last = <-events:
		default:
			if len(last.Tasks) == 0 || last.Tasks[len(last.Tasks)-1].Status != "failed" || last.Tasks[len(last.Tasks)-1].Error == "" {
				t.Fatalf("failed task was not emitted after disconnect: %+v", last)
			}
			if last.Busy {
				t.Fatalf("task remained busy after disconnect: %+v", last)
			}
			return
		}
	}
}

func TestLocalDOCXDispatchRejectsExecuteAndPathsInGrant(t *testing.T) {
	for _, body := range []string{`{"action":"execute","command":"touch bad"}`, `{"action":"grant","mode":"rw","path":"/"}`, `{"action":"list"}{"action":"execute"}`} {
		if _, err := parseDOCXRequest(url.QueryEscape(body)); err == nil {
			t.Fatal("accepted unsafe action", body)
		}
	}
}

func TestLocalDOCXRevokeErrorsUseLocalizedCopy(t *testing.T) {
	_, missingIDErr := parseDOCXRequest(url.QueryEscape(`{"action":"revoke"}`))
	if missingIDErr == nil {
		t.Fatal("revoke without grant ID was accepted")
	}
	worker := newLocalDOCX(t.TempDir())
	session := docxSession{Base: "https://example.test", DeviceID: "device", UserID: 1}
	notFoundErr := worker.revoke(session, "missing")
	if notFoundErr == nil {
		t.Fatal("unknown grant revoke succeeded")
	}

	for _, locale := range []Locale{LocaleZH, LocaleEN} {
		wantMissingID := map[Locale]string{LocaleZH: "缺少目录授权标识。", LocaleEN: "Directory grant ID is missing."}[locale]
		wantNotFound := map[Locale]string{LocaleZH: "目录授权不存在。", LocaleEN: "The directory grant does not exist."}[locale]
		if got, want := presentFileopsError(locale, missingIDErr).Error(), wantMissingID; got != want {
			t.Errorf("missing grant ID in %s = %q, want %q", locale, got, want)
		}
		if got, want := presentFileopsError(locale, notFoundErr).Error(), wantNotFound; got != want {
			t.Errorf("unknown grant in %s = %q, want %q", locale, got, want)
		}
	}
}

func TestLocalDOCXStatusUsesCurrentBusyState(t *testing.T) {
	session := docxSession{Base: "https://example.test", DeviceID: "device", UserID: 1, Secret: "secret", Generation: 1, Context: context.Background()}
	worker := newLocalDOCX(t.TempDir())
	client := &deviceClient{generation: 1, docx: session}
	emitted := false
	var event docxEvent
	app := &App{
		device: client,
		docx:   worker,
		docxStatusSink: func(got docxEvent) {
			emitted = true
			event = got
		},
	}

	app.emitDOCX(session, nil)

	if !emitted {
		t.Fatal("status event was not emitted")
	}
	if event.Busy {
		t.Fatal("status used stale busy state after task completion")
	}
}

func TestKnowledgeImportBusyErrorBelongsOnlyToItsRequest(t *testing.T) {
	session := docxSession{Base: "https://example.test", DeviceID: "device", UserID: 1, Secret: "secret", Generation: 1, Context: context.Background()}
	worker := newLocalDOCX(t.TempDir())
	worker.busy = true
	var events []docxEvent
	app := &App{
		device: &deviceClient{generation: 1, docx: session},
		store:  &settingsStore{},
		docx:   worker,
		docxStatusSink: func(got docxEvent) {
			events = append(events, got)
		},
	}
	const requestID = "0123456789abcdef0123456789abcdef"
	app.handleDOCX(url.QueryEscape(`{"action":"knowledge_import","request_id":"` + requestID + `"}`))
	app.handleDOCX(url.QueryEscape(`{"action":"list"}`))
	if len(events) != 2 || events[0].KnowledgeImportRequestID != requestID || !events[0].Busy || events[0].Error == "" {
		t.Fatalf("busy rejection did not identify the import request: %+v", events)
	}
	if events[1].KnowledgeImportRequestID != "" || events[1].Error != "" {
		t.Fatalf("unrelated status looks like an import result: %+v", events[1])
	}
}

func TestKnowledgeImportWaitsForNativeConfirmation(t *testing.T) {
	for _, choice := range []string{"accept", "decline", "disconnect"} {
		t.Run(choice, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			shown := make(chan [2]func(), 1)
			result := make(chan bool, 1)
			go func() {
				result <- waitForKnowledgeImportConfirmation(ctx, func(accept, decline func()) {
					shown <- [2]func(){accept, decline}
				})
			}()
			callbacks := <-shown
			switch choice {
			case "accept":
				callbacks[0]()
			case "decline":
				callbacks[1]()
			case "disconnect":
				cancel()
				callbacks[0]()
			}
			if approved := <-result; approved != (choice == "accept") {
				t.Fatalf("%s got approval=%v", choice, approved)
			}
		})
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
	defer worker.close()
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
	defer worker.close()
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
	var createTokens []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if handleFileAudit(w, r) {
			return
		}
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
		token := r.Header.Get("Authorization")
		if body.Action == "create" {
			createTokens = append(createTokens, token)
		} else if token != "Bearer old-token" {
			t.Errorf("%s used unexpected token", body.Action)
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
	defer worker.close()
	grant, err := worker.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}
	_, err = worker.run(session, docxRequest{SourceGrant: grant.ID, Source: "input.docx", OutputGrant: grant.ID, Output: "out.docx", Instruction: "整理"}, func() error { return client.currentDOCX(session) })
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(createTokens, ",") != "Bearer old-token,Bearer renewed-token" {
		t.Fatalf("write authorizations did not use current tokens: %v", createTokens)
	}
	if _, err = os.Stat(filepath.Join(directory, "out.docx")); err != nil {
		t.Fatal(err)
	}
}
