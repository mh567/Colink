package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
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

const browserTransferLimit = docxFileLimit

type browserAction struct {
	Action       string `json:"action"`
	URL          string `json:"url,omitempty"`
	TabID        string `json:"tab_id,omitempty"`
	FormID       string `json:"form_id,omitempty"`
	SubmitID     string `json:"submit_id,omitempty"`
	SnapshotID   string `json:"snapshot_id,omitempty"`
	Values       map[string]any `json:"values,omitempty"`
	FilePath     string `json:"file_path,omitempty"`
	DownloadPath string `json:"download_path,omitempty"`
	DownloadURL  string `json:"download_url,omitempty"`
	Selector     string `json:"selector,omitempty"`
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
	File            string              `json:"file,omitempty"`
	Size            int64               `json:"size,omitempty"`
}

type browserProcess interface {
	perform(context.Context, browserAction) (browserResult, error)
	stop()
}

type localBrowser struct {
	mu                 sync.Mutex
	home               string
	connection         docxSession
	process            browserProcess
	stagedUploads      []string
	docx               *localDOCX
	ownsDOCX           bool
	confirmDestructive func(context.Context, Locale, string, string) (bool, error)
	authorize          func(docxSession, string) error
	query              func(docxSession, string, string) (string, error)
	start              func(context.Context, docxSession, string) (browserProcess, error)
	snapshot           *browserResult
	confirmSubmit      func(context.Context, Locale, string) (bool, error)
	activeOperation    atomic.Pointer[string]
}

func newLocalBrowser(home string) *localBrowser {
	return &localBrowser{
		home: home, docx: newLocalDOCX(home), ownsDOCX: true, authorize: authorizeLocalBrowser, query: queryLocalBrowser, start: startBrowserWorker,
	}
}

func newLocalBrowserWithDOCX(home string, docx *localDOCX) *localBrowser {
	return &localBrowser{
		home: home, docx: docx, ownsDOCX: false, authorize: authorizeLocalBrowser, query: queryLocalBrowser, start: startBrowserWorker,
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
	for _, stage := range b.stagedUploads {
		_ = os.RemoveAll(stage)
	}
	b.stagedUploads = nil
}

func (b *localBrowser) close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.stopLocked()
	if b.ownsDOCX && b.docx != nil {
		b.docx.close()
	}
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
			if action.Action == "open" || result.Error == "BROWSER_NOT_OPEN" || result.Error == "BROWSER_TASK_FAILED" || result.Error == "BROWSER_TRANSFER_UNCERTAIN" {
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
			!validBrowserFormID(result.SnapshotID) || !validBrowserFormActionURL(result.ActionURL) || len(result.Fields) == 0 {
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
	if action.Action == "upload" || action.Action == "download" {
		write := action.Action == "download"
		if validateBrowserFilename(request.File) != nil {
			return browserResult{}, errors.New("BROWSER_INVALID_REQUEST")
		}
		w := b.docx
		if _, err := w.openGrant(operation, request.GrantID, write); err != nil {
			return browserResult{}, err
		}
		if b.confirmDestructive == nil {
			return browserResult{}, fileopsCopyError(copyFileConfirmationUnavailable)
		}
		stage, err := os.MkdirTemp(b.home, "browser-transfer-*")
		if err != nil {
			return browserResult{}, errors.New("BROWSER_TASK_FAILED")
		}
		keep := false
		defer func() {
			if !keep {
				_ = os.RemoveAll(stage)
			}
		}()

		if !write {
			action.FilePath = filepath.Join(stage, request.File)
			if err := w.withGrant(operation, request.GrantID, false, func(root *os.Root) error {
				data, err := readBrowserFile(root, request.File)
				if err != nil {
					return err
				}
				if err := os.WriteFile(action.FilePath, data, 0600); err != nil {
					return errors.New("BROWSER_TASK_FAILED")
				}
				return nil
			}); err != nil {
				return browserResult{}, err
			}
		}

		planned := action
		planned.Action = "prepare_" + action.Action
		planned.FilePath = ""
		prepared, err := perform(planned)
		if err != nil {
			return browserResult{}, err
		}
		if prepared.URL == "" || prepared.Target == "" {
			return browserResult{}, errors.New("BROWSER_TASK_FAILED")
		}
		accepted, err := b.confirmDestructive(ctx, s.Locale, "browser_"+action.Action,
			prepared.URL+" → "+prepared.Target+" → "+request.File)
		if err != nil {
			return browserResult{}, err
		}
		if !accepted {
			return browserResult{}, errDestructiveCancelled
		}
		if err := checkPermission(action.Action); err != nil {
			return browserResult{}, err
		}

		if !write {
			var result browserResult
			err := w.withGrant(operation, request.GrantID, false, func(*os.Root) error {
				if err := checkPermission("upload"); err != nil {
					return err
				}
				result, err = perform(action)
				// Chromium can read the selected File later; keep it until the browser closes.
				if b.process != nil {
					b.stagedUploads = append(b.stagedUploads, stage)
					keep = true
				}
				return err
			})
			if err == nil && result.File != request.File {
				err = errors.New("BROWSER_TASK_FAILED")
			}
			return result, err
		}

		if _, err := w.openGrant(operation, request.GrantID, true); err != nil {
			return browserResult{}, err
		}
		action.DownloadPath = stage
		result, err := perform(action)
		if err != nil {
			return browserResult{}, err
		}
		if validateBrowserFilename(result.File) != nil {
			return browserResult{}, errors.New("BROWSER_TASK_FAILED")
		}
		stagingRoot, err := os.OpenRoot(stage)
		if err != nil {
			return browserResult{}, errors.New("BROWSER_TASK_FAILED")
		}
		data, err := readBrowserFile(stagingRoot, result.File)
		_ = stagingRoot.Close()
		if err != nil || int64(len(data)) != result.Size {
			return browserResult{}, errors.New("BROWSER_TASK_FAILED")
		}
		if err := b.saveDownload(operation, request, data, checkPermission); err != nil {
			return browserResult{}, err
		}
		result.File = request.File
		return result, nil
	}
	result, err := perform(action)
	if err != nil {
		return browserResult{}, err
	}
	if err := checkPermission(action.Action); err != nil {
		return browserResult{}, err
	}
	if action.Action == "forms" && result.Forms != nil {
		for _, form := range *result.Forms {
			for _, submitter := range form.Submitters {
				if !validBrowserFormActionURL(submitter.ActionURL) {
					return browserResult{}, errBrowserFormStale
				}
			}
		}
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

// Read only the file opened relative to the authorized root; never reopen its path.
func readBrowserFile(root *os.Root, name string) ([]byte, error) {
	info, err := root.Lstat(name)
	if err != nil || !info.Mode().IsRegular() {
		return nil, errors.New("BROWSER_FILE_NOT_FOUND")
	}
	f, err := root.Open(name)
	if err != nil {
		return nil, errors.New("BROWSER_FILE_NOT_FOUND")
	}
	defer f.Close()
	current, err := f.Stat()
	if err != nil || !os.SameFile(info, current) {
		return nil, errors.New("BROWSER_FILE_NOT_FOUND")
	}
	if current.Size() > browserTransferLimit {
		return nil, errors.New("BROWSER_FILE_TOO_LARGE")
	}
	data, err := io.ReadAll(io.LimitReader(f, browserTransferLimit+1))
	if err != nil {
		return nil, errors.New("BROWSER_FILE_NOT_FOUND")
	}
	if len(data) > browserTransferLimit {
		return nil, errors.New("BROWSER_FILE_TOO_LARGE")
	}
	return data, nil
}

func (b *localBrowser) saveDownload(s docxSession, request browserRequest, data []byte, authorize func(string) error) error {
	s.Format = "browser"
	w := b.docx
	if err := authorize("download"); err != nil {
		return err
	}
	apply := func(write func() error) error {
		if s.Apply != nil {
			return s.Apply(write)
		}
		return write()
	}

	var overwrite bool
	err := w.withGrant(s, request.GrantID, true, func(root *os.Root) error {
		_, err := root.Lstat(request.File)
		switch {
		case err == nil:
			overwrite = true
			return nil
		case !errors.Is(err, os.ErrNotExist):
			return errors.New("BROWSER_FILE_SAVE_FAILED")
		}
		if handled, err := createLocalOutput(s, root, request.File, data); handled {
			return err
		}
		return apply(func() error {
			if err := s.Context.Err(); err != nil {
				return err
			}
			// Finish the new file before publishing it without replacing an existing entry.
			stage := ".colink-browser-" + rand.Text()
			f, err := root.OpenFile(stage, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if err != nil {
				return errors.New("BROWSER_FILE_SAVE_FAILED")
			}
			defer root.Remove(stage)
			if _, err = f.Write(data); err == nil {
				err = f.Sync()
			}
			if closeErr := f.Close(); err == nil {
				err = closeErr
			}
			if err != nil {
				return errors.New("BROWSER_FILE_SAVE_FAILED")
			}
			if err := s.Context.Err(); err != nil {
				return err
			}
			if err := root.Link(stage, request.File); err != nil {
				if errors.Is(err, os.ErrExist) {
					return errDestructiveTargetChanged
				}
				return errors.New("BROWSER_FILE_SAVE_FAILED")
			}
			return nil
		})
	})
	if err != nil || !overwrite {
		return err
	}
	if err := authorize("overwrite"); err != nil {
		return err
	}
	var target destructiveTarget
	err = w.withGrant(s, request.GrantID, true, func(root *os.Root) error {
		var err error
		target, err = prepareDestructiveTarget(s, root, request.File, "overwrite")
		return err
	})
	if err != nil {
		return err
	}
	defer target.close()
	if err := target.confirm(s.Context, func(ctx context.Context, _, path string) (bool, error) {
		return b.confirmDestructive(ctx, s.Locale, "browser_overwrite", path)
	}, data); err != nil {
		return err
	}
	if err := authorize("overwrite"); err != nil {
		return err
	}
	proof, err := refreshDOCXSession(s)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(s.Context, 15*time.Second)
	defer cancel()
	return w.withGrant(s, request.GrantID, true, func(*os.Root) error {
		return apply(func() error { return target.commit(ctx, data, proof) })
	})
}
