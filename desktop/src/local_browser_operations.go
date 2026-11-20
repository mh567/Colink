package main

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"time"
)

var errBrowserOperationStore = errors.New("BROWSER_TASK_FAILED")

type browserOperation struct {
	ID        string    `json:"id"`
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"created_at"`
	URL       string    `json:"url"`
}

func (b *localBrowser) browserOperationDirectory(s docxSession) string {
	return filepath.Join(b.home, "local-browser-operations", fmt.Sprintf("%x", sha256.Sum256([]byte(s.scope()))))
}

func (operation browserOperation) save(path string) error {
	data, err := json.Marshal(operation)
	if err != nil {
		return err
	}
	return saveLocalTaskJSON(filepath.Join(path, "state.json"), data)
}

func (b *localBrowser) readBrowserOperation(path string) (*browserOperation, error) {
	id := filepath.Base(path)
	data, err := os.ReadFile(filepath.Join(path, "state.json"))
	if errors.Is(err, os.ErrNotExist) {
		dir, statErr := os.Stat(path)
		if errors.Is(statErr, os.ErrNotExist) {
			return nil, nil
		}
		if statErr != nil || !dir.IsDir() {
			return nil, errBrowserOperationStore
		}
		// The exclusive ID reservation may survive an interrupted first write.
		data, err = os.ReadFile(filepath.Join(path, "state.json"))
		if errors.Is(err, os.ErrNotExist) {
			return b.recoverBrowserOperation(path, browserOperation{ID: id, Status: "awaiting_confirmation", CreatedAt: dir.ModTime().UTC()}), nil
		}
	}
	var operation browserOperation
	if err != nil || json.Unmarshal(data, &operation) != nil || operation.ID != id {
		return nil, errBrowserOperationStore
	}
	return b.recoverBrowserOperation(path, operation), nil
}

func (b *localBrowser) recoverBrowserOperation(path string, operation browserOperation) *browserOperation {
	if active := b.activeOperation.Load(); active == nil || *active != path {
		switch operation.Status {
		case "awaiting_confirmation":
			operation.Status = "interrupted"
		case "submitting":
			operation.Status = "outcome_unknown"
		}
	}
	return &operation
}

func (b *localBrowser) browserOperations(s docxSession) ([]browserOperation, error) {
	dir := b.browserOperationDirectory(s)
	entries, err := os.ReadDir(dir)
	operations := []browserOperation{}
	if errors.Is(err, os.ErrNotExist) {
		return operations, nil
	}
	if err != nil {
		return nil, errBrowserOperationStore
	}
	for _, entry := range entries {
		if !entry.IsDir() || !validDestructiveID(entry.Name()) {
			continue
		}
		operation, err := b.readBrowserOperation(filepath.Join(dir, entry.Name()))
		if err != nil {
			return nil, err
		}
		if operation != nil {
			operations = append(operations, *operation)
		}
	}
	slices.SortStableFunc(operations, func(a, b browserOperation) int { return b.CreatedAt.Compare(a.CreatedAt) })
	return operations, nil
}

func (b *localBrowser) submitForm(s docxSession, request browserRequest, locked bool, checkPermission func(string) error, perform func(browserAction) (browserResult, error)) (browserResult, error) {
	if !validDestructiveID(request.ID) {
		return browserResult{}, errors.New("BROWSER_INVALID_REQUEST")
	}
	dir := b.browserOperationDirectory(s)
	path := filepath.Join(dir, request.ID)
	checked := func(operation *browserOperation, err error) (browserResult, error) {
		if failure := checkPermission(request.Action); failure != nil {
			return browserResult{}, failure
		}
		return browserResult{Operation: operation}, err
	}
	if existing, err := b.readBrowserOperation(path); err != nil || existing != nil {
		return checked(existing, err)
	}
	if !locked {
		return browserResult{}, errors.New("BROWSER_BUSY")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return browserResult{}, errBrowserOperationStore
	}
	// This directory permanently reserves the ID across application processes.
	b.activeOperation.Store(&path)
	defer b.activeOperation.Store(nil)
	if err := os.Mkdir(path, 0700); errors.Is(err, os.ErrExist) {
		b.activeOperation.Store(nil)
		return checked(b.readBrowserOperation(path))
	} else if err != nil {
		return browserResult{}, errBrowserOperationStore
	}
	operation := browserOperation{
		ID: request.ID, Status: "awaiting_confirmation", CreatedAt: time.Now().UTC(), URL: browserOrigin(request.URL),
	}
	setStatus := func(status string) error {
		operation.Status = status
		return operation.save(path)
	}
	finish := func(status string) (browserResult, error) {
		if err := setStatus(status); err != nil {
			return browserResult{}, errBrowserOperationStore
		}
		return checked(&operation, nil)
	}
	snapshot := b.snapshot
	if snapshot == nil || snapshot.TabID != request.TabID || snapshot.URL != request.URL ||
		snapshot.FormID != request.FormID || snapshot.SubmitID != request.SubmitID || snapshot.SnapshotID != request.SnapshotID ||
		!validBrowserFormValues(snapshot.Fields, request.Values) {
		return finish("stale")
	}
	if b.confirmSubmit == nil {
		return finish("cancelled")
	}
	if err := operation.save(path); err != nil {
		return browserResult{}, errBrowserOperationStore
	}
	approved, confirmErr := b.confirmSubmit(s.Context, s.Locale, browserSubmitConfirmation(s.Locale, *snapshot, request.Values))
	if !approved || confirmErr != nil {
		return finish("cancelled")
	}
	if failure := checkPermission(request.Action); failure != nil {
		_ = setStatus("cancelled")
		return browserResult{}, failure
	}
	if err := setStatus("submitting"); err != nil {
		return browserResult{}, errBrowserOperationStore
	}
	b.snapshot = nil
	result, err := perform(request.browserAction)
	status := "outcome_unknown"
	if errors.Is(err, errBrowserFormStale) {
		status = "stale"
	} else if err == nil && result.ClickDispatched {
		status = "action_sent"
	}
	if err := setStatus(status); err != nil {
		// The prior durable submitting state recovers conservatively.
		operation.Status = "outcome_unknown"
	}
	return checked(&operation, nil)
}
