package main

import (
	"context"
	"errors"
	"net/url"
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
