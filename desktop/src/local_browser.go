package main

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
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
	Tabs  *[]browserTab `json:"tabs,omitempty"`
	Text  string        `json:"text,omitempty"`
	Title string        `json:"title,omitempty"`
	URL   string        `json:"url,omitempty"`
	Image string        `json:"image,omitempty"`
	Error string        `json:"error,omitempty"`
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
	start      func(docxSession, string) (browserProcess, error)
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
	if err := deviceTaskCall(s, "browser", "authorize", map[string]string{"action": action}, &reply); err != nil || !reply.Allowed {
		return errors.New("BROWSER_NOT_AUTHORIZED")
	}
	return nil
}

func queryLocalBrowser(s docxSession, instruction, text string) (string, error) {
	var reply struct {
		Text string `json:"text"`
	}
	payload := map[string]string{"instruction": instruction, "text": text}
	if err := deviceTaskCall(s, "browser", "query", payload, &reply); err != nil || strings.TrimSpace(reply.Text) == "" {
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
		return browserResult{}, errors.New("BROWSER_NOT_AUTHORIZED")
	}
	action := request.browserAction
	if b.process != nil && (b.connection.Generation != s.Generation ||
		b.connection.scope() != s.scope() || b.connection.Secret != s.Secret) {
		b.stopLocked()
	}
	checkPermission := func() error {
		if current() != nil || b.authorize(s, action.Action) != nil || current() != nil {
			b.stopLocked()
			return errors.New("BROWSER_NOT_AUTHORIZED")
		}
		return nil
	}
	if err := checkPermission(); err != nil {
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
		process, err := b.start(s, profile)
		if err != nil {
			return browserResult{}, err
		}
		b.process, b.connection = process, s
		// Preparing the local runtime can outlast the device session.
		if err := checkPermission(); err != nil {
			return browserResult{}, err
		}
	}
	result, err := b.process.perform(s.Context, action)
	if err != nil {
		b.stopLocked()
		return browserResult{}, err
	}
	if result.Error != "" {
		if action.Action == "open" || result.Error == "BROWSER_NOT_OPEN" || result.Error == "BROWSER_TASK_FAILED" {
			b.stopLocked()
		}
		return browserResult{}, errors.New(result.Error)
	}
	if err := checkPermission(); err != nil {
		return browserResult{}, err
	}
	if action.Action == "query" {
		result.Text, err = b.query(s, request.Instruction, result.Text)
		if err != nil {
			return browserResult{}, err
		}
		if current() != nil {
			b.stopLocked()
			return browserResult{}, errors.New("BROWSER_NOT_AUTHORIZED")
		}
	}
	return result, nil
}
