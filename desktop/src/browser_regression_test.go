package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestBrowserRevokedBetweenLaunchAndNavigation(t *testing.T) {
	worker := &browserTestProcess{}
	service := newLocalBrowser(t.TempDir())
	service.start = func(context.Context, docxSession, string) (browserProcess, error) { return worker, nil }
	service.authorize = func(_ docxSession, action string) error {
		if action == "new_tab" {
			return errors.New("BROWSER_NOT_AUTHORIZED")
		}
		return nil
	}
	session := docxSession{Context: context.Background()}
	request := browserRequest{browserAction: browserAction{Action: "open", URL: "https://intranet.test/orders"}}
	_, err := service.run(session, request, func() error { return nil })
	if err == nil || len(worker.actions) != 1 || worker.actions[0].URL != "" || !worker.stopped {
		t.Fatalf("revoked navigation ran: actions=%+v, stopped=%v, err=%v", worker.actions, worker.stopped, err)
	}
}

func TestBrowserTemporaryAuthorizationFailureKeepsOpenBrowser(t *testing.T) {
	worker := &browserTestProcess{}
	service := newLocalBrowser(t.TempDir())
	service.start = func(context.Context, docxSession, string) (browserProcess, error) { return worker, nil }
	var authErr error
	service.authorize = func(docxSession, string) error { return authErr }
	session := docxSession{Context: context.Background()}
	current := func() error { return nil }
	if _, err := service.run(session, browserRequest{browserAction: browserAction{Action: "open"}}, current); err != nil {
		t.Fatal(err)
	}
	authErr = errBrowserService
	refresh := browserRequest{browserAction: browserAction{Action: "list"}}
	if _, err := service.run(session, refresh, current); err == nil || err.Error() != authErr.Error() || worker.stopped || len(worker.actions) != 1 {
		t.Fatalf("temporary failure changed browser: actions=%+v stopped=%v err=%v", worker.actions, worker.stopped, err)
	}
	authErr = nil
	if _, err := service.run(session, refresh, current); err != nil || worker.stopped || len(worker.actions) != 2 {
		t.Fatalf("refresh could not recover: actions=%+v stopped=%v err=%v", worker.actions, worker.stopped, err)
	}
}

func TestBrowserDoesNotLaunchExpiredRequest(t *testing.T) {
	service := newLocalBrowser(t.TempDir())
	service.authorize = func(docxSession, string) error {
		t.Fatal("expired operation reached authorization")
		return nil
	}
	service.start = func(context.Context, docxSession, string) (browserProcess, error) {
		t.Fatal("expired operation started a worker")
		return nil, nil
	}
	session := docxSession{Context: context.Background()}
	request := browserRequest{
		browserAction: browserAction{Action: "open"},
		DeadlineMS:    time.Now().Add(-time.Second).UnixMilli(),
	}
	_, err := service.run(session, request, func() error { return nil })
	if !errors.Is(err, errBrowserTimeout) {
		t.Fatalf("expired request returned %v", err)
	}
}

func TestBrowserAuthorizationClassifiesHTTPFailures(t *testing.T) {
	responses := make(chan int, 3)
	responses <- http.StatusBadGateway
	responses <- http.StatusForbidden
	responses <- http.StatusOK
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		status := <-responses
		w.WriteHeader(status)
		if status == http.StatusOK {
			_, _ = w.Write([]byte(`{"allowed":true}`))
		}
	}))
	defer server.Close()
	session := docxSession{Context: context.Background(), Base: server.URL, DeviceID: "test-device"}
	for _, expected := range []error{errBrowserService, errBrowserDenied, nil} {
		if err := authorizeLocalBrowser(session, "list"); err != expected {
			t.Fatalf("authorize error = %v, want %v", err, expected)
		}
	}
}

func TestBrowserModelDenialKeepsAuthorizedBrowser(t *testing.T) {
	worker := &browserTestProcess{}
	service := newLocalBrowser(t.TempDir())
	service.start = func(context.Context, docxSession, string) (browserProcess, error) { return worker, nil }
	service.authorize = func(docxSession, string) error { return nil }
	service.query = func(docxSession, string, string) (string, error) { return "", errBrowserDenied }
	session := docxSession{Context: context.Background()}
	current := func() error { return nil }
	if _, err := service.run(session, browserRequest{browserAction: browserAction{Action: "open"}}, current); err != nil {
		t.Fatal(err)
	}
	request := browserRequest{browserAction: browserAction{Action: "query", TabID: sampleBrowserTab, URL: "https://intranet.test/result"}, Instruction: "inventory"}
	_, err := service.run(session, request, current)
	if err == nil || err.Error() != "BROWSER_MODEL_FAILED" || worker.stopped {
		t.Fatalf("model denial closed browser: stopped=%v err=%v", worker.stopped, err)
	}
}
