package main

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const sampleBrowserTab = "0123456789abcdef0123456789abcdef"

func TestBrowserRequestRejectsArbitraryBrowserActions(t *testing.T) {
	requestID := strings.Repeat("a", 32)
	for _, body := range []string{
		`{"request_id":"` + requestID + `","action":"eval","instruction":"document.cookie"}`,
		`{"request_id":"` + requestID + `","action":"new_tab","url":"file:///etc/passwd"}`,
		`{"request_id":"` + requestID + `","action":"query","tab_id":"` + sampleBrowserTab + `","instruction":"查询"}`,
		`{"request_id":"` + requestID + `","action":"capture","tab_id":"` + sampleBrowserTab + `","url":"https://intranet.test/result","path":"../output.png"}`,
	} {
		if _, err := parseBrowserRequest(url.QueryEscape(body)); err == nil {
			t.Fatalf("accepted invalid request: %s", body)
		}
	}
}

type browserTestProcess struct {
	actions []browserAction
	stopped bool
}

func (p *browserTestProcess) perform(_ context.Context, action browserAction) (browserResult, error) {
	p.actions = append(p.actions, action)
	if action.Action == "query" {
		return browserResult{Text: "DEMO-42 库存 7", Title: "库存", URL: "https://intranet.test/result"}, nil
	}
	if action.Action == "upload" {
		return browserResult{File: filepath.Base(action.FilePath), URL: action.URL, Title: "订单"}, nil
	}
	if action.Action == "download" {
		if action.DownloadPath != "" {
			_ = os.WriteFile(filepath.Join(action.DownloadPath, "export.xlsx"), []byte("downloaded-content"), 0600)
		}
		return browserResult{URL: action.URL, Title: "订单"}, nil
	}
	return browserResult{}, nil
}

func (p *browserTestProcess) stop() { p.stopped = true }

func TestLocalBrowserAuthorizesBeforeLaunchingAndBeforeReading(t *testing.T) {
	worker := &browserTestProcess{}
	allowed := false
	launches := 0
	modelCalls := 0
	service := newLocalBrowser(t.TempDir())
	service.authorize = func(_ docxSession, _ string) error {
		if !allowed {
			return errors.New("denied")
		}
		return nil
	}
	service.start = func(_ context.Context, _ docxSession, _ string) (browserProcess, error) {
		launches++
		return worker, nil
	}
	service.query = func(_ docxSession, instruction, body string) (string, error) {
		modelCalls++
		if instruction != "查询库存" || body != "DEMO-42 库存 7" {
			t.Fatalf("unexpected model input: %q / %q", instruction, body)
		}
		return "库存为 7 件", nil
	}
	session := docxSession{Base: "https://colink.test", UserID: 1, DeviceID: "laptop", Secret: "session", Context: context.Background()}
	current := func() error { return nil }
	open := browserRequest{browserAction: browserAction{Action: "open"}}
	if _, err := service.run(session, open, current); err == nil || launches != 0 {
		t.Fatalf("unauthorized launch: launches=%d, err=%v", launches, err)
	}
	allowed = true
	if _, err := service.run(session, open, current); err != nil || launches != 1 {
		t.Fatalf("opening browser: launches=%d, err=%v", launches, err)
	}
	query := browserRequest{
		browserAction: browserAction{Action: "query", TabID: sampleBrowserTab, URL: "https://intranet.test/result"},
		Instruction:   "查询库存",
	}
	result, err := service.run(session, query, current)
	if err != nil || result.Text != "库存为 7 件" || result.URL != query.URL || modelCalls != 1 {
		t.Fatalf("query result: %+v, calls=%d, err=%v", result, modelCalls, err)
	}
	allowed = false
	if _, err := service.run(session, query, current); err == nil || len(worker.actions) != 2 || modelCalls != 1 || !worker.stopped {
		t.Fatalf("revoked query touched browser or model: actions=%v, calls=%d, stopped=%v, err=%v", worker.actions, modelCalls, worker.stopped, err)
	}
}

func TestLocalBrowserProfilesFollowTheEmployeeAndDevice(t *testing.T) {
	service := newLocalBrowser(t.TempDir())
	service.authorize = func(_ docxSession, _ string) error { return nil }
	var profiles []string
	var workers []*browserTestProcess
	service.start = func(_ context.Context, _ docxSession, profile string) (browserProcess, error) {
		profiles = append(profiles, profile)
		worker := &browserTestProcess{}
		workers = append(workers, worker)
		return worker, nil
	}
	session := docxSession{
		Base: "https://colink.test", UserID: 1, DeviceID: "laptop", Secret: "first",
		Context: context.Background(),
	}
	open := browserRequest{browserAction: browserAction{Action: "open"}}
	openOn := func(s docxSession) {
		t.Helper()
		if _, err := service.run(s, open, func() error { return nil }); err != nil {
			t.Fatal(err)
		}
	}
	openOn(session)
	otherDevice := session
	otherDevice.DeviceID = "workstation"
	openOn(otherDevice)
	otherEmployee := session
	otherEmployee.UserID = 2
	openOn(otherEmployee)
	reconnected := session
	reconnected.Generation = 1
	reconnected.Secret = "new"
	openOn(reconnected)
	if _, err := service.run(otherEmployee, open, func() error { return errors.New("stale device session") }); err == nil || workers[3].stopped {
		t.Fatal("a stale employee request affected the current browser")
	}
	if len(profiles) != 4 || profiles[0] == profiles[1] || profiles[0] == profiles[2] || profiles[0] != profiles[3] {
		t.Fatalf("unexpected profile isolation: %v", profiles)
	}
	for _, worker := range workers[:3] {
		if !worker.stopped {
			t.Fatal("previous employee or device still has a running browser")
		}
	}
}

func TestBrowserRequestUploadAndDownloadValidation(t *testing.T) {
	requestID := strings.Repeat("a", 32)
	validUpload := fmt.Sprintf(`{"request_id":"%s","action":"upload","tab_id":"%s","url":"https://intranet.test","grant_id":"grant-1","file":"doc.pdf","selector":"input[type=file]"}`, requestID, sampleBrowserTab)
	req, err := parseBrowserRequest(url.QueryEscape(validUpload))
	if err != nil || req.Action != "upload" || req.GrantID != "grant-1" || req.File != "doc.pdf" || req.Selector != "input[type=file]" {
		t.Fatalf("valid upload failed: req=%+v, err=%v", req, err)
	}

	validDownload := fmt.Sprintf(`{"request_id":"%s","action":"download","tab_id":"%s","url":"https://intranet.test","grant_id":"grant-2","file":"export.xlsx","download_url":"https://intranet.test/dl","selector":"#download-btn"}`, requestID, sampleBrowserTab)
	req, err = parseBrowserRequest(url.QueryEscape(validDownload))
	if err != nil || req.Action != "download" || req.GrantID != "grant-2" || req.File != "export.xlsx" || req.DownloadURL != "https://intranet.test/dl" || req.Selector != "#download-btn" {
		t.Fatalf("valid download failed: req=%+v, err=%v", req, err)
	}

	invalidRequests := []string{
		fmt.Sprintf(`{"request_id":"%s","action":"upload","tab_id":"%s","url":"https://intranet.test","grant_id":"g1","file":"../escape.txt"}`, requestID, sampleBrowserTab),
		fmt.Sprintf(`{"request_id":"%s","action":"upload","tab_id":"%s","url":"https://intranet.test","grant_id":"g1","file":"/etc/passwd"}`, requestID, sampleBrowserTab),
		fmt.Sprintf(`{"request_id":"%s","action":"upload","tab_id":"%s","url":"https://intranet.test","grant_id":"g1","file":"sub/dir.txt"}`, requestID, sampleBrowserTab),
		fmt.Sprintf(`{"request_id":"%s","action":"upload","tab_id":"%s","url":"https://intranet.test","grant_id":"","file":"doc.pdf"}`, requestID, sampleBrowserTab),
		fmt.Sprintf(`{"request_id":"%s","action":"download","tab_id":"%s","url":"https://intranet.test","grant_id":"g1","file":".."}`, requestID, sampleBrowserTab),
		fmt.Sprintf(`{"request_id":"%s","action":"download","tab_id":"%s","url":"https://intranet.test","grant_id":"g1","file":""}`, requestID, sampleBrowserTab),
	}
	for _, body := range invalidRequests {
		if _, err := parseBrowserRequest(url.QueryEscape(body)); err == nil {
			t.Fatalf("accepted invalid browser file request: %s", body)
		}
	}
}

func TestLocalBrowserUploadRequiresDirectoryGrant(t *testing.T) {
	tempHome := t.TempDir()
	grantDir := t.TempDir()
	testFile := filepath.Join(grantDir, "upload.pdf")
	if err := os.WriteFile(testFile, []byte("pdf-data"), 0600); err != nil {
		t.Fatal(err)
	}

	worker := &browserTestProcess{}
	service := newLocalBrowser(tempHome)
	service.authorize = func(_ docxSession, _ string) error { return nil }
	service.start = func(_ context.Context, _ docxSession, _ string) (browserProcess, error) {
		return worker, nil
	}
	session := docxSession{
		Base: "https://colink.test", UserID: 1, DeviceID: "laptop", Secret: "session",
		Context: context.Background(),
	}
	current := func() error { return nil }

	// 1. Without grant -> fails
	reqNoGrant := browserRequest{
		browserAction: browserAction{Action: "upload", TabID: sampleBrowserTab, URL: "https://intranet.test"},
		GrantID:       "nonexistent-grant",
		File:          "upload.pdf",
	}
	if _, err := service.run(session, reqNoGrant, current); err == nil {
		t.Fatal("expected upload without grant to fail")
	}

	// Add grant
	grant, err := service.docx.grant(session, grantDir, "ro")
	if err != nil {
		t.Fatal(err)
	}

	// 2. Missing file inside valid grant -> fails
	reqMissingFile := browserRequest{
		browserAction: browserAction{Action: "upload", TabID: sampleBrowserTab, URL: "https://intranet.test"},
		GrantID:       grant.ID,
		File:          "missing.pdf",
	}
	if _, err := service.run(session, reqMissingFile, current); err == nil {
		t.Fatal("expected upload of missing file to fail")
	}

	// 3. Valid file inside grant -> succeeds
	reqValid := browserRequest{
		browserAction: browserAction{Action: "upload", TabID: sampleBrowserTab, URL: "https://intranet.test"},
		GrantID:       grant.ID,
		File:          "upload.pdf",
		Selector:      "input[type=file]",
	}
	res, err := service.run(session, reqValid, current)
	if err != nil {
		t.Fatalf("valid upload failed: %v", err)
	}
	if res.File != "upload.pdf" {
		t.Fatalf("expected result file upload.pdf, got %s", res.File)
	}
	if len(worker.actions) == 0 || worker.actions[len(worker.actions)-1].FilePath != testFile {
		t.Fatalf("worker did not receive full file path: %+v", worker.actions)
	}

	// 4. Permission denied by server
	service.authorize = func(_ docxSession, action string) error {
		if action == "upload" {
			return errBrowserDenied
		}
		return nil
	}
	if _, err := service.run(session, reqValid, current); !errors.Is(err, errBrowserDenied) {
		t.Fatalf("expected errBrowserDenied, got %v", err)
	}
}

func TestLocalBrowserDownloadConfirmsOverwriteAndWritesToGrant(t *testing.T) {
	tempHome := t.TempDir()
	grantDir := t.TempDir()

	worker := &browserTestProcess{}
	service := newLocalBrowser(tempHome)
	service.authorize = func(_ docxSession, _ string) error { return nil }
	service.start = func(_ context.Context, _ docxSession, _ string) (browserProcess, error) {
		return worker, nil
	}
	session := docxSession{
		Base: "https://colink.test", UserID: 1, DeviceID: "laptop", Secret: "session",
		Context: context.Background(),
	}
	current := func() error { return nil }

	// Add rw grant
	grant, err := service.docx.grant(session, grantDir, "rw")
	if err != nil {
		t.Fatal(err)
	}

	// 1. Initial download to new file
	reqDownload := browserRequest{
		browserAction: browserAction{Action: "download", TabID: sampleBrowserTab, URL: "https://intranet.test"},
		GrantID:       grant.ID,
		File:          "export.xlsx",
		DownloadURL:   "https://intranet.test/export.xlsx",
	}
	res, err := service.run(session, reqDownload, current)
	if err != nil {
		t.Fatalf("download failed: %v", err)
	}
	if res.File != "export.xlsx" || res.Size != int64(len("downloaded-content")) {
		t.Fatalf("unexpected download result: %+v", res)
	}
	savedContent, err := os.ReadFile(filepath.Join(grantDir, "export.xlsx"))
	if err != nil || string(savedContent) != "downloaded-content" {
		t.Fatalf("unexpected saved file content: %s, err: %v", string(savedContent), err)
	}

	// 2. Overwrite download with confirmation REJECTED
	confirmationCalled := false
	service.confirmDestructive = func(_ context.Context, _ Locale, action, path string) (bool, error) {
		confirmationCalled = true
		if action != "overwrite" || path != filepath.Join(grantDir, "export.xlsx") {
			t.Fatalf("unexpected confirm args: action=%s, path=%s", action, path)
		}
		return false, nil // user rejects overwrite
	}

	if _, err := service.run(session, reqDownload, current); !errors.Is(err, errDestructiveCancelled) {
		t.Fatalf("expected errDestructiveCancelled, got %v", err)
	}
	if !confirmationCalled {
		t.Fatal("confirmDestructive was not called on overwrite")
	}
	// Verify original file content is untouched!
	savedContent, err = os.ReadFile(filepath.Join(grantDir, "export.xlsx"))
	if err != nil || string(savedContent) != "downloaded-content" {
		t.Fatalf("file was modified after cancellation: %s", string(savedContent))
	}

	// 3. Overwrite download with confirmation ACCEPTED
	service.confirmDestructive = func(_ context.Context, _ Locale, action, path string) (bool, error) {
		return true, nil // user accepts
	}
	res, err = service.run(session, reqDownload, current)
	if err != nil {
		t.Fatalf("confirmed overwrite failed: %v", err)
	}
	if res.File != "export.xlsx" {
		t.Fatalf("unexpected result: %+v", res)
	}

	// 4. Read-only grant rejected for download
	roDir := t.TempDir()
	roGrant, err := service.docx.grant(session, roDir, "ro")
	if err != nil {
		t.Fatal(err)
	}
	reqRODownload := browserRequest{
		browserAction: browserAction{Action: "download", TabID: sampleBrowserTab, URL: "https://intranet.test"},
		GrantID:       roGrant.ID,
		File:          "export.xlsx",
	}
	if _, err := service.run(session, reqRODownload, current); err == nil {
		t.Fatal("expected download into read-only grant to fail")
	}
}
