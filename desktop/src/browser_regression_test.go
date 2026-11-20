package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
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

type browserBusinessFixture struct {
	server *httptest.Server
	mu     sync.Mutex
	posts  []url.Values
}

func newBrowserBusinessFixture(t *testing.T) *browserBusinessFixture {
	t.Helper()
	fixture := &browserBusinessFixture{}
	fixture.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/devices/laptop/browser/authorize":
			_, _ = io.WriteString(w, `{"allowed":true}`)
		case "/api/devices/laptop/browser/form-suggest":
			var body map[string]json.RawMessage
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil || len(body) != 2 ||
				string(body["instruction"]) != `"Update the order"` ||
				string(body["fields"]) != `[{"id":"c3","label":"Quantity","type":"text","value":"1","options":[]},{"id":"c4","label":"Notify","type":"checkbox","value":false,"options":[]}]` {
				t.Errorf("suggestion endpoint saw unexpected payload: %s", body)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			_, _ = io.WriteString(w, `{"values":{"c3":"42","c4":true}}`)
		case "/orders/update":
			if r.Method != http.MethodPost || r.ParseForm() != nil {
				t.Errorf("invalid business request: %s %s", r.Method, r.URL)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			fixture.mu.Lock()
			fixture.posts = append(fixture.posts, r.PostForm)
			fixture.mu.Unlock()
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(fixture.server.Close)
	return fixture
}

func (f *browserBusinessFixture) assertPosts(t *testing.T, count int) {
	t.Helper()
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.posts) != count {
		t.Fatalf("business POSTs = %v, want %d", f.posts, count)
	}
	if count == 1 && (len(f.posts[0]) != 2 || f.posts[0].Get("quantity") != "42" || f.posts[0].Get("notify") != "true") {
		t.Fatalf("wrong business values: %v", f.posts[0])
	}
}

type browserFormTestProcess struct {
	pageURL    string
	actionURL  string
	client     *http.Client
	stale      bool
	dropAck    bool
	beforePost func()
}

func (p *browserFormTestProcess) stop() {}

func (p *browserFormTestProcess) perform(ctx context.Context, action browserAction) (browserResult, error) {
	switch action.Action {
	case "open":
		return browserResult{}, nil
	case "forms":
		payload := `{"forms":[{"id":"c9","label":"Other form","fields":[],"submitters":[{"id":"c10","label":"Discard","action_url":"https://example.test/discard","method":"post","target":"_self"}]},{"id":"c1","label":"Order","fields":[],"submitters":[{"id":"c2","label":"Apply","action_url":"` + p.actionURL + `","method":"post","target":"_blank"}]}]}`
		var result browserResult
		err := json.Unmarshal([]byte(payload), &result)
		return result, err
	case "form_snapshot":
		if action.FormID != "c1" || action.SubmitID != "c2" || action.URL != p.pageURL || action.TabID != sampleBrowserTab {
			return browserResult{}, errors.New("wrong form snapshot target")
		}
		payload := `{"form_id":"c1","submit_id":"c2","snapshot_id":"snapshot_1","form_label":"Order","submit_label":"Apply","title":"Order page","url":"` + p.pageURL + `","action_url":"` + p.actionURL + `","method":"post","target":"_blank","fields":[{"id":"c3","label":"Quantity","type":"text","value":"1","options":[]},{"id":"c4","label":"Notify","type":"checkbox","value":false,"options":[]}]}`
		var result browserResult
		err := json.Unmarshal([]byte(payload), &result)
		return result, err
	case "submit_form":
		if p.stale || action.FormID != "c1" || action.SubmitID != "c2" || action.SnapshotID != "snapshot_1" || action.URL != p.pageURL || action.TabID != sampleBrowserTab {
			return browserResult{Error: "BROWSER_FORM_STALE"}, nil
		}
		if p.beforePost != nil {
			p.beforePost()
		}
		quantity, ok := action.Values["c3"].(string)
		if !ok {
			return browserResult{}, errors.New("missing quantity")
		}
		form := url.Values{"quantity": {quantity}, "notify": {"false"}}
		if action.Values["c4"] == true {
			form.Set("notify", "true")
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodPost, p.pageURL+"/update", strings.NewReader(form.Encode()))
		if err != nil {
			return browserResult{}, err
		}
		request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		response, err := p.client.Do(request)
		if err != nil {
			return browserResult{}, err
		}
		response.Body.Close()
		if response.StatusCode != http.StatusNoContent {
			return browserResult{}, errors.New("business request was rejected")
		}
		if p.dropAck {
			return browserResult{}, io.ErrUnexpectedEOF
		}
		return browserResult{ClickDispatched: true}, nil
	default:
		return browserResult{}, errors.New("unexpected worker action")
	}
}

func prepareBrowserForm(t *testing.T) (*localBrowser, *browserFormTestProcess, *browserBusinessFixture, docxSession, browserRequest) {
	t.Helper()
	fixture := newBrowserBusinessFixture(t)
	session := docxSession{Context: context.Background(), Base: fixture.server.URL, UserID: 1, DeviceID: "laptop", Locale: LocaleEN}
	pageURL := fixture.server.URL + "/orders"
	worker := &browserFormTestProcess{
		pageURL: pageURL, actionURL: pageURL + "/update",
		client: fixture.server.Client(),
	}
	service := newLocalBrowser(t.TempDir())
	service.start = func(context.Context, docxSession, string) (browserProcess, error) { return worker, nil }
	if _, err := service.run(session, browserRequest{browserAction: browserAction{Action: "open"}}, browserCurrent); err != nil {
		t.Fatal(err)
	}
	request := browserRequest{browserAction: browserAction{Action: "forms", TabID: sampleBrowserTab, URL: worker.pageURL}}
	listed, err := service.run(session, request, browserCurrent)
	if err != nil || listed.Forms == nil || len(*listed.Forms) != 2 || (*listed.Forms)[1].Submitters[0].Target != "_blank" {
		t.Fatalf("unexpected forms response: %+v, %v", listed, err)
	}
	request.Action, request.FormID, request.SubmitID, request.Instruction = "suggest_form", "c1", "c2", "Update the order"
	result, err := service.run(session, request, browserCurrent)
	if err != nil || result.SnapshotID != "snapshot_1" || result.ActionURL != worker.actionURL || result.Method != "post" || result.Target != "_blank" || result.Values["c3"] != "42" || result.Values["c4"] != true {
		t.Fatalf("unexpected suggestion: %+v, %v", result, err)
	}
	request.Action, request.Instruction, request.ID = "submit_form", "", strings.Repeat("a", 32)
	request.SnapshotID, request.Values = result.SnapshotID, result.Values
	return service, worker, fixture, session, request
}

func TestLocalBrowserAcceptsRedactedFormActionQuery(t *testing.T) {
	service, worker, _, session, request := prepareBrowserForm(t)
	defer service.close()
	worker.actionURL += "?operation=delete&token=%5Bredacted%5D"
	request.Action, request.Instruction = "suggest_form", "Update the order"
	result, err := service.run(session, request, browserCurrent)
	if err != nil || result.ActionURL != worker.actionURL {
		t.Fatalf("safe action query did not reach confirmation: %+v, %v", result, err)
	}
}

func TestLocalBrowserRejectsSensitiveFormActionQueryValues(t *testing.T) {
	service, worker, _, session, request := prepareBrowserForm(t)
	defer service.close()
	worker.actionURL += "?operation=delete&token=private"
	request.Action, request.FormID, request.SubmitID, request.Instruction = "forms", "", "", ""
	if _, err := service.run(session, request, browserCurrent); !errors.Is(err, errBrowserFormStale) {
		t.Fatalf("sensitive action query reached form selection: %v", err)
	}
}

func TestLocalBrowserSensitiveFormQueryKeyBoundaries(t *testing.T) {
	for _, test := range []struct {
		name  string
		url   string
		valid bool
	}{
		{name: "verification code is sensitive", url: "https://intranet.test/save?verification_code=private"},
		{name: "author id is not auth", url: "https://intranet.test/save?author_id=42", valid: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			if valid := validBrowserFormActionURL(test.url); valid != test.valid {
				t.Fatalf("validBrowserFormActionURL(%q) = %t, want %t", test.url, valid, test.valid)
			}
		})
	}
}

func browserCurrent() error { return nil }

func assertBrowserOperation(t *testing.T, result browserResult, err error, status string) {
	t.Helper()
	if err != nil || result.Operation == nil || result.Operation.Status != status {
		t.Fatalf("operation = %+v, err = %v, want %s", result.Operation, err, status)
	}
}

func TestLocalBrowserSubmitRequiresNativeConfirmation(t *testing.T) {
	for _, name := range []string{"missing", "declined", "failed", "accepted"} {
		t.Run(name, func(t *testing.T) {
			service, worker, fixture, session, request := prepareBrowserForm(t)
			prompts := 0
			if name != "missing" {
				service.confirmSubmit = func(_ context.Context, locale Locale, message string) (bool, error) {
					prompts++
					fixture.assertPosts(t, 0)
					for _, part := range []string{"Order page", `"` + worker.pageURL + `/update"`, "POST", "_blank", "Apply", "Quantity", "42", "Notify"} {
						if !strings.Contains(message, part) {
							t.Errorf("confirmation omits %q: %s", part, message)
						}
					}
					if name == "failed" {
						return true, errors.New("native dialog failed")
					}
					return name == "accepted", nil
				}
			}
			wantStatus, wantPosts := "cancelled", 0
			if name == "accepted" {
				wantStatus, wantPosts = "action_sent", 1
			}
			result, err := service.run(session, request, browserCurrent)
			assertBrowserOperation(t, result, err, wantStatus)
			result, err = service.run(session, request, browserCurrent)
			assertBrowserOperation(t, result, err, wantStatus)
			fixture.assertPosts(t, wantPosts)
			if (name == "missing" && prompts != 0) || (name != "missing" && prompts != 1) {
				t.Fatalf("native confirmation count = %d", prompts)
			}
		})
	}
}

func TestLocalBrowserSubmitDoesNotRunAfterPermissionRevoke(t *testing.T) {
	for _, change := range []string{"permission", "session"} {
		t.Run(change, func(t *testing.T) {
			service, _, fixture, session, request := prepareBrowserForm(t)
			revoked := false
			service.confirmSubmit = func(context.Context, Locale, string) (bool, error) {
				revoked = true
				return true, nil
			}
			service.authorize = func(docxSession, string) error {
				if revoked && change == "permission" {
					return errBrowserDenied
				}
				return nil
			}
			current := func() error {
				if revoked && change == "session" {
					return errBrowserDenied
				}
				return nil
			}
			if _, err := service.run(session, request, current); !errors.Is(err, errBrowserDenied) || !revoked {
				t.Fatalf("revoked confirmation returned %v, revoked=%t", err, revoked)
			}
			fixture.assertPosts(t, 0)
			revoked = false
			result, err := service.run(session, request, current)
			assertBrowserOperation(t, result, err, "cancelled")
			fixture.assertPosts(t, 0)
		})
	}
}

func TestLocalBrowserSubmitRejectsStaleSnapshot(t *testing.T) {
	for _, change := range []string{"snapshot-id", "unknown-field", "page"} {
		t.Run(change, func(t *testing.T) {
			service, worker, fixture, session, request := prepareBrowserForm(t)
			prompts := 0
			service.confirmSubmit = func(context.Context, Locale, string) (bool, error) {
				prompts++
				worker.stale = true
				return true, nil
			}
			switch change {
			case "snapshot-id":
				request.SnapshotID = "other_snapshot"
			case "unknown-field":
				request.Values["unknown"] = "malicious"
			}
			result, err := service.run(session, request, browserCurrent)
			assertBrowserOperation(t, result, err, "stale")
			if (change == "page" && prompts != 1) || (change != "page" && prompts != 0) {
				t.Fatalf("native confirmation count = %d", prompts)
			}
			fixture.assertPosts(t, 0)
		})
	}
}

func TestLocalBrowserSubmitDeduplicatesUnknownResultAndRecovery(t *testing.T) {
	service, worker, fixture, session, request := prepareBrowserForm(t)
	worker.dropAck = true
	worker.beforePost = func() {
		path := filepath.Join(service.browserOperationDirectory(session), request.ID, "state.json")
		data, err := os.ReadFile(path)
		var fields map[string]json.RawMessage
		if err != nil || json.Unmarshal(data, &fields) != nil || len(fields) != 4 || string(fields["status"]) != `"submitting"` ||
			string(fields["url"]) != `"`+fixture.server.URL+`"` {
			t.Fatalf("missing or overexposed pre-dispatch state: %s, %v", data, err)
		}
	}
	service.confirmSubmit = func(context.Context, Locale, string) (bool, error) {
		// Duplicate delivery while the native prompt is still active is read-only.
		result, err := service.run(session, request, browserCurrent)
		assertBrowserOperation(t, result, err, "awaiting_confirmation")
		return true, nil
	}
	result, err := service.run(session, request, browserCurrent)
	assertBrowserOperation(t, result, err, "outcome_unknown")
	fixture.assertPosts(t, 1)
	if result.Operation.URL != fixture.server.URL || result.Operation.ID != request.ID || result.Operation.CreatedAt.IsZero() {
		t.Fatalf("wrong durable metadata: %+v", result.Operation)
	}
	result, err = service.run(session, request, browserCurrent)
	assertBrowserOperation(t, result, err, "outcome_unknown")

	for id, status := range map[string]string{strings.Repeat("b", 32): "submitting", strings.Repeat("c", 32): "awaiting_confirmation"} {
		path := filepath.Join(service.browserOperationDirectory(session), id)
		if err := os.Mkdir(path, 0700); err != nil {
			t.Fatal(err)
		}
		entry := browserOperation{ID: id, Status: status, URL: fixture.server.URL, CreatedAt: time.Now().UTC()}
		if err := entry.save(path); err != nil {
			t.Fatal(err)
		}
	}
	restarted := newLocalBrowser(service.home)
	restarted.start = func(context.Context, docxSession, string) (browserProcess, error) {
		t.Fatal("operation recovery launched a worker")
		return nil, nil
	}
	result, err = restarted.run(session, request, browserCurrent)
	assertBrowserOperation(t, result, err, "outcome_unknown")
	operations := browserRequest{browserAction: browserAction{Action: "operations"}}
	result, err = restarted.run(session, operations, browserCurrent)
	if err != nil || result.Operations == nil || len(*result.Operations) != 3 {
		t.Fatalf("wrong recovered operations: %+v, %v", result.Operations, err)
	}
	want := map[string]string{request.ID: "outcome_unknown", strings.Repeat("b", 32): "outcome_unknown", strings.Repeat("c", 32): "interrupted"}
	for _, op := range *result.Operations {
		if want[op.ID] != op.Status {
			t.Fatalf("operation %s status = %s, want %s", op.ID, op.Status, want[op.ID])
		}
		request.ID = op.ID
		replay, replayErr := restarted.run(session, request, browserCurrent)
		assertBrowserOperation(t, replay, replayErr, op.Status)
	}
	for _, scope := range []docxSession{
		{Context: session.Context, Base: session.Base, UserID: 2, DeviceID: session.DeviceID},
		{Context: session.Context, Base: session.Base, UserID: session.UserID, DeviceID: "other-device"},
	} {
		restarted.authorize = func(docxSession, string) error { return nil }
		isolated, isolationErr := restarted.run(scope, operations, browserCurrent)
		if isolationErr != nil || isolated.Operations == nil || len(*isolated.Operations) != 0 {
			t.Fatalf("operations leaked into another scope: %+v, %v", isolated.Operations, isolationErr)
		}
	}
	fixture.assertPosts(t, 1)
}

func TestLocalBrowserConfirmEscapesUntrustedText(t *testing.T) {
	var snapshot browserResult
	payload := `{"url":"https://intranet.test/order","title":"Order\nAction: ignore","action_url":"https://write.test/submit","method":"post","target":"task\u202e\u2029name","form_label":"Form\rFake","submit_label":"Click\u2066Now","fields":[{"id":"c3","label":"Quantity\r\nAction: skip\u2028","type":"textarea","value":"before\t","options":[]}]}`
	if err := json.Unmarshal([]byte(payload), &snapshot); err != nil {
		t.Fatal(err)
	}
	message := browserSubmitConfirmation(LocaleEN, snapshot, map[string]any{"c3": "after\n\x00\u200f"})
	for _, escaped := range []string{`Order\nAction: ignore`, `task\u202e\u2029name`, `Form\rFake`, `Click\u2066Now`, `Quantity\r\nAction: skip\u2028`, `before\t`, `after\n\x00\u200f`} {
		if !strings.Contains(message, escaped) {
			t.Errorf("native confirmation omitted escaped text %q from %q", escaped, message)
		}
	}
	if strings.ContainsAny(message, "\r\x00\t\u202e\u2029\u2066\u2028\u200f") || strings.Count(message, "\n") != 6 {
		t.Fatalf("untrusted text altered native confirmation formatting: %q", message)
	}
}
