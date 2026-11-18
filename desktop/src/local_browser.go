package main

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

var (
	errBrowserDenied      = errors.New("BROWSER_NOT_AUTHORIZED")
	errBrowserTimeout     = errors.New("BROWSER_TIMEOUT")
	errBrowserService     = errors.New("BROWSER_SERVICE_UNAVAILABLE")
	errBrowserFormStale   = errors.New("BROWSER_FORM_STALE")
	errBrowserModelFailed = errors.New("BROWSER_MODEL_FAILED")
)

type browserAction struct {
	Action     string         `json:"action"`
	URL        string         `json:"url,omitempty"`
	TabID      string         `json:"tab_id,omitempty"`
	FormID     string         `json:"form_id,omitempty"`
	SubmitID   string         `json:"submit_id,omitempty"`
	SnapshotID string         `json:"snapshot_id,omitempty"`
	Values     map[string]any `json:"values,omitempty"`
}

type browserTab struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	URL   string `json:"url"`
}

type browserFormOption struct {
	Label string `json:"label"`
	Value string `json:"value"`
}

type browserFormField struct {
	ID      string              `json:"id"`
	Label   string              `json:"label"`
	Type    string              `json:"type"`
	Value   any                 `json:"value"`
	Options []browserFormOption `json:"options"`
}

type browserSubmitter struct {
	ID        string `json:"id"`
	Label     string `json:"label"`
	ActionURL string `json:"action_url"`
	Method    string `json:"method"`
	Target    string `json:"target"`
}

type browserForm struct {
	ID         string             `json:"id"`
	Label      string             `json:"label"`
	Fields     []browserFormField `json:"fields"`
	Submitters []browserSubmitter `json:"submitters"`
}

type browserResult struct {
	TabID           string              `json:"tab_id,omitempty"`
	Incomplete      bool                `json:"incomplete,omitempty"`
	Tabs            *[]browserTab       `json:"tabs,omitempty"`
	Text            string              `json:"text,omitempty"`
	Title           string              `json:"title,omitempty"`
	URL             string              `json:"url,omitempty"`
	Image           string              `json:"image,omitempty"`
	Error           string              `json:"error,omitempty"`
	Forms           *[]browserForm      `json:"forms,omitempty"`
	FormID          string              `json:"form_id,omitempty"`
	SubmitID        string              `json:"submit_id,omitempty"`
	SnapshotID      string              `json:"snapshot_id,omitempty"`
	FormLabel       string              `json:"form_label,omitempty"`
	SubmitLabel     string              `json:"submit_label,omitempty"`
	ActionURL       string              `json:"action_url,omitempty"`
	Method          string              `json:"method,omitempty"`
	Target          string              `json:"target,omitempty"`
	Fields          []browserFormField  `json:"fields,omitempty"`
	Values          map[string]any      `json:"values,omitempty"`
	ClickDispatched bool                `json:"click_dispatched,omitempty"`
	Operation       *browserOperation   `json:"operation,omitempty"`
	Operations      *[]browserOperation `json:"operations,omitempty"`
}

type browserProcess interface {
	perform(context.Context, browserAction) (browserResult, error)
	stop()
}

type localBrowser struct {
	mu              sync.Mutex
	home            string
	connection      docxSession
	process         browserProcess
	authorize       func(docxSession, string) error
	query           func(docxSession, string, string) (string, error)
	start           func(context.Context, docxSession, string) (browserProcess, error)
	snapshot        *browserResult
	confirmSubmit   func(context.Context, Locale, string) (bool, error)
	activeOperation atomic.Pointer[string]
}

func newLocalBrowser(home string) *localBrowser {
	return &localBrowser{
		home: home, authorize: authorizeLocalBrowser, query: queryLocalBrowser, start: startBrowserWorker,
	}
}

func authorizeLocalBrowser(s docxSession, action string) error {
	var reply struct {
		Allowed bool `json:"allowed"`
	}
	err := deviceTaskCall(s, "browser", "authorize", map[string]string{"action": action}, &reply)
	if err != nil {
		var response *deviceTaskResponseError
		if errors.As(err, &response) && (response.status == http.StatusUnauthorized || response.status == http.StatusForbidden) {
			return errBrowserDenied
		}
		return errBrowserService
	}
	if !reply.Allowed {
		return errBrowserService
	}
	return nil
}

func queryLocalBrowser(s docxSession, instruction, text string) (string, error) {
	var reply struct {
		Text string `json:"text"`
	}
	payload := map[string]string{"instruction": instruction, "text": text}
	if err := deviceTaskCall(s, "browser", "query", payload, &reply); err != nil {
		var response *deviceTaskResponseError
		if errors.As(err, &response) && (response.status == http.StatusUnauthorized || response.status == http.StatusForbidden) {
			return "", errBrowserDenied
		}
		return "", errBrowserModelFailed
	}
	if strings.TrimSpace(reply.Text) == "" {
		return "", errBrowserModelFailed
	}
	return reply.Text, nil
}

func suggestLocalBrowserForm(s docxSession, instruction string, fields []browserFormField) (map[string]any, error) {
	var reply struct {
		Values map[string]any `json:"values"`
	}
	payload := struct {
		Instruction string             `json:"instruction"`
		Fields      []browserFormField `json:"fields"`
	}{instruction, fields}
	if err := deviceTaskCall(s, "browser", "form-suggest", payload, &reply); err != nil {
		return nil, errBrowserModelFailed
	}
	if !validBrowserFormValues(fields, reply.Values) {
		return nil, errBrowserModelFailed
	}
	return reply.Values, nil
}

func validBrowserFormValues(fields []browserFormField, values map[string]any) bool {
	if len(values) == 0 {
		return false
	}
	for id, value := range values {
		i := slices.IndexFunc(fields, func(field browserFormField) bool { return field.ID == id })
		if i == -1 {
			return false
		}
		switch fields[i].Type {
		case "checkbox":
			if _, ok := value.(bool); !ok {
				return false
			}
		case "text", "textarea":
			if _, ok := value.(string); !ok {
				return false
			}
		case "select", "radio":
			text, ok := value.(string)
			if !ok || !slices.ContainsFunc(fields[i].Options, func(option browserFormOption) bool { return option.Value == text }) {
				return false
			}
		default:
			return false
		}
	}
	return true
}

func (b *localBrowser) stopLocked() {
	b.snapshot = nil
	if b.process != nil {
		b.process.stop()
		b.process = nil
		b.connection = docxSession{}
	}
}

func (b *localBrowser) close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.stopLocked()
}

func (b *localBrowser) run(s docxSession, request browserRequest, current func() error) (browserResult, error) {
	action := request.browserAction
	locked := action.Action != "operations" && b.mu.TryLock()
	if locked {
		defer b.mu.Unlock()
	} else if action.Action != "operations" && action.Action != "submit_form" {
		return browserResult{}, errors.New("BROWSER_BUSY")
	}
	if current() != nil {
		return browserResult{}, errBrowserDenied
	}
	timeout := 110 * time.Second
	if action.Action == "query" || action.Action == "suggest_form" {
		timeout = 165 * time.Second
	}
	deadline := time.Now().Add(timeout)
	if clientDeadline := time.UnixMilli(request.DeadlineMS); request.DeadlineMS > 0 && clientDeadline.Before(deadline) {
		deadline = clientDeadline
	}
	ctx, cancel := context.WithDeadline(s.Context, deadline)
	defer cancel()
	operation := s
	operation.Context = ctx

	if locked && b.process != nil && (b.connection.Generation != s.Generation ||
		b.connection.scope() != s.scope() || b.connection.Secret != s.Secret) {
		b.stopLocked()
	}
	checkCurrent := func() error {
		if current() != nil || errors.Is(s.Context.Err(), context.Canceled) {
			if locked {
				b.stopLocked()
			}
			return errBrowserDenied
		}
		if ctx.Err() != nil {
			return errBrowserTimeout
		}
		return nil
	}
	checkPermission := func(name string) error {
		if err := checkCurrent(); err != nil {
			return err
		}
		authErr := b.authorize(operation, name)
		if err := checkCurrent(); err != nil {
			return err
		}
		if authErr != nil && !errors.Is(authErr, errBrowserService) {
			if locked {
				b.stopLocked()
			}
			return errBrowserDenied
		}
		return authErr
	}
	perform := func(action browserAction) (browserResult, error) {
		if err := checkCurrent(); err != nil {
			return browserResult{}, err
		}
		result, err := b.process.perform(ctx, action)
		if err != nil {
			b.stopLocked()
			if failure := checkCurrent(); failure != nil {
				return browserResult{}, failure
			}
			return browserResult{}, err
		}
		if result.Error != "" {
			if action.Action == "open" || result.Error == "BROWSER_NOT_OPEN" || result.Error == "BROWSER_TASK_FAILED" {
				b.stopLocked()
			}
			if result.Error == errBrowserFormStale.Error() {
				return browserResult{}, errBrowserFormStale
			}
			return browserResult{}, errors.New(result.Error)
		}
		return result, nil
	}

	if err := checkPermission(action.Action); err != nil {
		return browserResult{}, err
	}
	if action.Action == "operations" {
		operations, err := b.browserOperations(s)
		if failure := checkPermission(action.Action); failure != nil {
			return browserResult{}, failure
		}
		return browserResult{Operations: &operations}, err
	}
	if action.Action == "submit_form" {
		return b.submitForm(operation, request, locked, checkPermission, perform)
	}
	if action.Action == "close" {
		b.stopLocked()
		return browserResult{}, nil
	}
	if b.process == nil {
		if action.Action != "open" {
			return browserResult{}, errors.New("BROWSER_NOT_OPEN")
		}
		profile := filepath.Join(b.home, "browser-profiles", fmt.Sprintf("%x", sha256.Sum256([]byte(s.scope()))))
		process, err := b.start(ctx, s, profile)
		if err != nil {
			if failure := checkCurrent(); failure != nil {
				return browserResult{}, failure
			}
			return browserResult{}, err
		}
		b.process, b.connection = process, s
		// Runtime preparation can outlast the authorization or operation.
		if err := checkPermission(action.Action); err != nil {
			b.stopLocked()
			return browserResult{}, err
		}
	}
	if action.Action == "open" && action.URL != "" {
		if _, err := perform(browserAction{Action: "open"}); err != nil {
			return browserResult{}, err
		}
		action.Action = "new_tab"
		if err := checkPermission(action.Action); err != nil {
			return browserResult{}, err
		}
	}
	if action.Action == "forms" || action.Action == "suggest_form" || action.Action == "select_tab" || action.Action == "close_tab" || action.Action == "new_tab" {
		b.snapshot = nil
	}
	if action.Action == "suggest_form" {
		action.Action = "form_snapshot"
		result, err := perform(action)
		if err != nil {
			return browserResult{}, err
		}
		if err := checkPermission(request.Action); err != nil {
			return browserResult{}, err
		}
		if result.URL != action.URL || result.FormID != action.FormID || result.SubmitID != action.SubmitID ||
			!validBrowserFormID(result.SnapshotID) || !browserHTTPURL(result.ActionURL) ||
			strings.ContainsAny(result.ActionURL, "?#") || len(result.Fields) == 0 {
			return browserResult{}, errBrowserFormStale
		}
		values, err := suggestLocalBrowserForm(operation, request.Instruction, result.Fields)
		if failure := checkPermission(request.Action); failure != nil {
			return browserResult{}, failure
		}
		if err != nil {
			return browserResult{}, err
		}
		result.TabID = action.TabID
		snapshot := result
		b.snapshot = &snapshot
		result.Values = values
		return result, nil
	}
	result, err := perform(action)
	if err != nil {
		return browserResult{}, err
	}
	if err := checkPermission(action.Action); err != nil {
		return browserResult{}, err
	}
	if action.Action == "query" {
		result.Text, err = b.query(operation, request.Instruction, result.Text)
		if failure := checkCurrent(); failure != nil {
			return browserResult{}, failure
		}
		if errors.Is(err, errBrowserDenied) {
			// The model and the browser have separate grants.
			if failure := checkPermission(action.Action); failure != nil {
				return browserResult{}, failure
			}
			return browserResult{}, errBrowserModelFailed
		}
		if err != nil {
			return browserResult{}, err
		}
	}
	return result, nil
}
