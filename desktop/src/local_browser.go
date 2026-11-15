package main

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var (
	errBrowserDenied  = errors.New("BROWSER_NOT_AUTHORIZED")
	errBrowserTimeout = errors.New("BROWSER_TIMEOUT")
	errBrowserService = errors.New("BROWSER_SERVICE_UNAVAILABLE")
)

type browserAction struct {
	Action string `json:"action"`
	URL    string `json:"url,omitempty"`
	TabID  string `json:"tab_id,omitempty"`
}

type browserTab struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	URL   string `json:"url"`
}

type browserResult struct {
	TabID      string        `json:"tab_id,omitempty"`
	Incomplete bool          `json:"incomplete,omitempty"`
	Tabs       *[]browserTab `json:"tabs,omitempty"`
	Text       string        `json:"text,omitempty"`
	Title      string        `json:"title,omitempty"`
	URL        string        `json:"url,omitempty"`
	Image      string        `json:"image,omitempty"`
	Error      string        `json:"error,omitempty"`
}

type browserProcess interface {
	perform(context.Context, browserAction) (browserResult, error)
	stop()
}

type localBrowser struct {
	mu         sync.Mutex
	home       string
	connection docxSession
	process    browserProcess
	authorize  func(docxSession, string) error
	query      func(docxSession, string, string) (string, error)
	start      func(context.Context, docxSession, string) (browserProcess, error)
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
		return "", errors.New("BROWSER_MODEL_FAILED")
	}
	if strings.TrimSpace(reply.Text) == "" {
		return "", errors.New("BROWSER_MODEL_FAILED")
	}
	return reply.Text, nil
}

func (b *localBrowser) stopLocked() {
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
	if !b.mu.TryLock() {
		return browserResult{}, errors.New("BROWSER_BUSY")
	}
	defer b.mu.Unlock()
	if current() != nil {
		return browserResult{}, errBrowserDenied
	}
	action := request.browserAction
	timeout := 110 * time.Second
	if action.Action == "query" {
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

	if b.process != nil && (b.connection.Generation != s.Generation ||
		b.connection.scope() != s.scope() || b.connection.Secret != s.Secret) {
		b.stopLocked()
	}
	checkCurrent := func() error {
		if current() != nil || errors.Is(s.Context.Err(), context.Canceled) {
			b.stopLocked()
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
			b.stopLocked()
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
			return browserResult{}, errors.New(result.Error)
		}
		return result, nil
	}

	if err := checkPermission(action.Action); err != nil {
		return browserResult{}, err
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
			return browserResult{}, errors.New("BROWSER_MODEL_FAILED")
		}
		if err != nil {
			return browserResult{}, err
		}
	}
	return result, nil
}
