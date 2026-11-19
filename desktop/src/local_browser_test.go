package main

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
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
	actions       []browserAction
	stopped       bool
	afterDownload func()
}

func (p *browserTestProcess) perform(_ context.Context, action browserAction) (browserResult, error) {
	p.actions = append(p.actions, action)
	if action.Action == "query" {
		return browserResult{Text: "DEMO-42 库存 7", Title: "库存", URL: "https://intranet.test/result"}, nil
	}
	switch action.Action {
	case "prepare_upload":
		return browserResult{URL: action.URL, Target: "input[type=file]"}, nil
	case "prepare_download":
		return browserResult{URL: action.URL, Target: action.DownloadURL}, nil
	case "upload":
		return browserResult{File: filepath.Base(action.FilePath), URL: action.URL}, nil
	case "download":
		if err := os.WriteFile(filepath.Join(action.DownloadPath, "a-unrelated.txt"), []byte("unrelated"), 0600); err != nil {
			return browserResult{}, err
		}
		if err := os.WriteFile(filepath.Join(action.DownloadPath, "artifact"), []byte("downloaded-content"), 0600); err != nil {
			return browserResult{}, err
		}
		if p.afterDownload != nil {
			p.afterDownload()
		}
		return browserResult{URL: action.URL, File: "artifact", Size: 18}, nil
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

	validDownload := fmt.Sprintf(`{"request_id":"%s","action":"download","tab_id":"%s","url":"https://intranet.test","grant_id":"grant-2","file":"export.xlsx","download_url":"https://intranet.test/dl"}`, requestID, sampleBrowserTab)
	req, err = parseBrowserRequest(url.QueryEscape(validDownload))
	if err != nil || req.Action != "download" || req.GrantID != "grant-2" || req.File != "export.xlsx" || req.DownloadURL != "https://intranet.test/dl" {
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

func browserTransferTest(t *testing.T) (*localBrowser, *browserTestProcess, docxSession) {
	t.Helper()
	worker := &browserTestProcess{}
	service := newLocalBrowser(t.TempDir())
	t.Cleanup(service.close)
	service.authorize = func(_ docxSession, _ string) error { return nil }
	service.confirmDestructive = func(context.Context, Locale, string, string) (bool, error) {
		return true, nil
	}
	service.start = func(context.Context, docxSession, string) (browserProcess, error) {
		return worker, nil
	}
	session := docxSession{
		Base: "https://colink.test", UserID: 1, DeviceID: "laptop", Secret: "session",
		Context: context.Background(),
	}
	if _, err := service.run(session, browserRequest{browserAction: browserAction{Action: "open"}}, func() error { return nil }); err != nil {
		t.Fatal(err)
	}
	return service, worker, session
}

func TestLocalBrowserUploadRequiresDirectoryGrant(t *testing.T) {
	service, worker, session := browserTransferTest(t)
	current := func() error { return nil }
	grantDir := t.TempDir()
	source := filepath.Join(grantDir, "upload.pdf")
	if err := os.WriteFile(source, []byte("pdf-data"), 0600); err != nil {
		t.Fatal(err)
	}
	request := browserRequest{
		browserAction: browserAction{Action: "upload", TabID: sampleBrowserTab, URL: "https://intranet.test"},
		GrantID:       "nonexistent", File: "upload.pdf",
	}
	if _, err := service.run(session, request, current); err == nil || len(worker.actions) != 1 {
		t.Fatalf("upload without grant reached the browser: %v, %v", err, worker.actions)
	}
	grant, err := service.docx.grant(session, grantDir, "ro")
	if err != nil {
		t.Fatal(err)
	}
	request.GrantID = grant.ID
	missing := request
	missing.File = "missing.pdf"
	if _, err := service.run(session, missing, current); err == nil || err.Error() != "BROWSER_FILE_NOT_FOUND" {
		t.Fatalf("expected precise missing file error, got %v", err)
	}
	result, err := service.run(session, request, current)
	if err != nil || result.File != "upload.pdf" {
		t.Fatalf("valid upload failed: %v, %v", result, err)
	}
	staged := worker.actions[len(worker.actions)-1].FilePath
	data, err := os.ReadFile(staged)
	if staged == source || err != nil || string(data) != "pdf-data" {
		t.Fatalf("browser did not get an independent copy: %q %v", data, err)
	}
	service.close()
	if _, err := os.Stat(staged); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("temporary file retained after browser close: %v", err)
	}
}

func TestLocalBrowserDownloadOnlyPublishesItsReportedArtifact(t *testing.T) {
	service, worker, session := browserTransferTest(t)
	current := func() error { return nil }
	grantDir := t.TempDir()
	grant, err := service.docx.grant(session, grantDir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	request := browserRequest{
		browserAction: browserAction{Action: "download", TabID: sampleBrowserTab, URL: "https://intranet.test", DownloadURL: "https://intranet.test/export"},
		GrantID:       grant.ID, File: "export.xlsx",
	}
	target := filepath.Join(grantDir, request.File)
	result, err := service.run(session, request, current)
	data, readErr := os.ReadFile(target)
	if err != nil || readErr != nil || string(data) != "downloaded-content" || result.File != request.File {
		t.Fatalf("download output: %v %v %q %v", err, readErr, data, result)
	}

	request.File = "revoked.xlsx"
	worker.afterDownload = func() {
		if err := service.docx.revoke(session, grant.ID); err != nil {
			t.Error(err)
		}
	}
	if _, err := service.run(session, request, current); err == nil {
		t.Fatal("download saved after directory grant was revoked")
	}
	if _, err := os.Stat(filepath.Join(grantDir, request.File)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("revoked grant contains downloaded content: %v", err)
	}
}

func TestLocalBrowserDownloadDoesNotOverwriteWithoutConfirmation(t *testing.T) {
	service, worker, session := browserTransferTest(t)
	current := func() error { return nil }
	grantDir := t.TempDir()
	grant, err := service.docx.grant(session, grantDir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	request := browserRequest{
		browserAction: browserAction{Action: "download", TabID: sampleBrowserTab, URL: "https://intranet.test", DownloadURL: "https://intranet.test/export"},
		GrantID:       grant.ID, File: "target.bin",
	}
	target := filepath.Join(grantDir, request.File)
	worker.afterDownload = func() {
		if err := os.WriteFile(target, []byte("existing"), 0600); err != nil {
			t.Error(err)
		}
	}
	service.confirmDestructive = func(_ context.Context, _ Locale, action, _ string) (bool, error) {
		return action != "browser_overwrite", nil
	}
	_, err = service.run(session, request, current)
	if err == nil || (runtime.GOOS == "windows" && !errors.Is(err, errDestructiveCancelled)) {
		t.Fatalf("late-appearing file was not rejected: %v", err)
	}
	if content, err := os.ReadFile(target); err != nil || string(content) != "existing" {
		t.Fatalf("unconfirmed overwrite changed target: %q, %v", content, err)
	}
}
