package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

var (
	errDestructiveCancelled     = errors.New("file operation cancelled")
	errDestructiveTargetChanged = fileopsCopyError(copyFileTargetChanged)
)

type fileopsCopyError string

func (e fileopsCopyError) Error() string {
	return desktopText(LocaleEN, string(e))
}

func presentFileopsError(locale Locale, err error) error {
	var copyErr fileopsCopyError
	if errors.As(err, &copyErr) {
		return errors.New(desktopText(locale, string(copyErr)))
	}
	return err
}

type destructiveTarget interface {
	confirm(context.Context, func(context.Context, string, string) (bool, error), []byte) error
	commit(context.Context, []byte, docxSession) error
	close()
}

type destructiveRecord struct {
	Digest string   `json:"digest"`
	Task   docxTask `json:"task"`
}

func isDestructiveAction(action string) bool {
	return action == "overwrite" || action == "delete"
}

func validDestructiveID(id string) bool {
	if len(id) != 32 || strings.ToLower(id) != id {
		return false
	}
	_, err := hex.DecodeString(id)
	return err == nil
}

func safeDestructivePath(path string) bool {
	return utf8.ValidString(path) && strings.IndexFunc(path, func(r rune) bool {
		return unicode.IsControl(r) || unicode.Is(unicode.Bidi_Control, r) || r == '\u2028' || r == '\u2029'
	}) == -1
}

func (w *localDOCX) destructiveDirectory(s docxSession) string {
	return filepath.Join(w.home, "local-file-operations", fmt.Sprintf("%x", sha256.Sum256([]byte(s.scope()))))
}

func readDestructiveRecord(path string) (destructiveRecord, error) {
	var entry destructiveRecord
	data, err := os.ReadFile(path)
	if err == nil {
		err = json.Unmarshal(data, &entry)
	}
	return entry, err
}

func (w *localDOCX) destructiveTasks(s docxSession) ([]docxTask, error) {
	dir := w.destructiveDirectory(s)
	files, err := os.ReadDir(dir)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var tasks []docxTask
	for _, file := range files {
		if !validDestructiveID(file.Name()) {
			continue
		}
		entry, readErr := readDestructiveRecord(filepath.Join(dir, file.Name()))
		if readErr != nil {
			continue
		}
		task := entry.Task
		if !w.busy {
			markInterrupted(&task, s.Locale)
		}
		tasks = append(tasks, task)
	}
	return tasks, nil
}

func markInterrupted(task *docxTask, locale Locale) {
	if task.Status == "running" || task.Status == "awaiting_confirmation" {
		task.Status = "interrupted"
		task.Error = interruptedFileMessage(*task, locale)
	}
}

func interruptedFileMessage(task docxTask, locale Locale) string {
	if task.Recovery != "" {
		return desktopText(locale, copyFileCheckBackup, task.Recovery)
	}
	return desktopText(locale, copyFileCheckResult)
}

func (w *localDOCX) claimDestructive(s docxSession, request docxRequest, task docxTask) (path string, entry destructiveRecord, replay bool, err error) {
	dir := w.destructiveDirectory(s)
	if err = os.MkdirAll(dir, 0700); err != nil {
		return
	}
	path = filepath.Join(dir, request.RequestID)
	requestJSON, err := json.Marshal(request)
	if err != nil {
		return
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(requestJSON))
	entry = destructiveRecord{Digest: digest, Task: task}
	data, err := json.Marshal(entry)
	if err != nil {
		return
	}
	// A durable exclusive claim prevents another client process from repeating the mutation.
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if errors.Is(err, os.ErrExist) {
		entry, err = readDestructiveRecord(path)
		if err != nil {
			err = errors.New(desktopText(s.Locale, copyFileCheckResult))
		} else if entry.Digest != digest {
			err = errors.New(desktopText(s.Locale, copyFileIntentChanged))
		}
		return path, entry, true, err
	}
	if err != nil {
		return
	}
	if _, err = file.Write(data); err == nil {
		err = file.Sync()
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	return
}

func (w *localDOCX) runDestructive(s docxSession, request docxRequest, current func() error) (task docxTask, err error) {
	s.Format = request.Format
	if s.Format == "" {
		s.Format = "docx"
	}
	request.Format = s.Format
	s.AgentID = request.AgentID
	filename := docxFilename
	switch s.Format {
	case "docx":
	case "xlsx":
		filename = xlsxFilename
	default:
		return task, errors.New(desktopText(s.Locale, copyFileInvalidIntent))
	}
	if !isDestructiveAction(request.Action) || !validDestructiveID(request.RequestID) || request.OutputGrant == "" || request.Mode != "" {
		return task, errors.New(desktopText(s.Locale, copyFileInvalidIntent))
	}
	if err = filename(request.Output); err != nil {
		return task, err
	}
	if request.Action == "delete" && (request.Source != "" || request.SourceGrant != "" || request.Instruction != "" || request.AgentID != "") {
		return task, errors.New(desktopText(s.Locale, copyFileInvalidIntent))
	}
	if request.Action == "overwrite" {
		if err = filename(request.Source); err != nil {
			return task, err
		}
		if strings.TrimSpace(request.Instruction) == "" || utf8.RuneCountInString(request.Instruction) > 8000 {
			return task, errors.New(desktopText(s.Locale, copyFileInvalidIntent))
		}
	}
	if err = current(); err != nil {
		return task, err
	}
	task = docxTask{ID: request.RequestID, AgentID: s.AgentID, Format: s.Format, Operation: request.Action, Output: request.Output, Instruction: request.Instruction, Status: "running"}
	journal, entry, replay, err := w.claimDestructive(s, request, task)
	if err != nil {
		return task, err
	}
	if replay {
		task = entry.Task
		markInterrupted(&task, s.Locale)
		if task.Error != "" {
			err = errors.New(task.Error)
		}
		return task, err
	}
	persist := func() error {
		entry.Task = task
		data, marshalErr := json.Marshal(entry)
		if marshalErr != nil {
			return marshalErr
		}
		return saveLocalTaskJSON(journal, data)
	}
	defer func() {
		switch {
		case errors.Is(err, errDestructiveCancelled):
			task.Status = "cancelled"
			err = nil
		case err != nil:
			if task.Status != "interrupted" {
				task.Status = "failed"
			}
			err = presentFileopsError(s.Locale, err)
			task.Error = err.Error()
			if task.Recovery != "" {
				task.Error = interruptedFileMessage(task, s.Locale)
				err = errors.New(task.Error)
			}
		default:
			task.Status = "completed"
		}
		if saveErr := persist(); saveErr != nil {
			task.Status = "interrupted"
			task.Error = interruptedFileMessage(task, s.Locale)
			err = errors.New(task.Error)
		}
	}()
	if err = authorizeDOCX(s, request.Action); err != nil {
		return task, err
	}
	output, err := w.openGrant(s, request.OutputGrant, true)
	if err != nil {
		return task, err
	}
	task.Output = filepath.Join(output.Name(), request.Output)
	if !safeDestructivePath(task.Output) {
		return task, errors.New(desktopText(s.Locale, copyFileInvalidPath))
	}
	target, err := prepareDestructiveTarget(s, output, request.Output, request.Action)
	if err != nil {
		return task, err
	}
	defer target.close()
	var data []byte
	if request.Action == "overwrite" {
		source, sourceErr := w.openGrant(s, request.SourceGrant, false)
		if sourceErr != nil {
			return task, sourceErr
		}
		task.Source = filepath.Join(source.Name(), request.Source)
		data, err = generateLocalOutput(s, request, source, current)
		if err != nil {
			return task, err
		}
	}
	if err = current(); err != nil {
		return task, err
	}
	task.Status = "awaiting_confirmation"
	if err = persist(); err != nil {
		return task, err
	}
	if s.TaskChanged != nil {
		s.TaskChanged()
	}
	prompt := func(ctx context.Context, operation, path string) (bool, error) {
		if w.confirmDestructive == nil {
			return false, errors.New(desktopText(s.Locale, copyFileConfirmationUnavailable))
		}
		return w.confirmDestructive(ctx, s.Locale, operation, path)
	}
	if err = target.confirm(s.Context, prompt, data); err != nil {
		return task, err
	}
	if err = authorizeDOCX(s, request.Action); err != nil {
		return task, err
	}
	if _, err = w.openGrant(s, request.OutputGrant, true); err != nil {
		return task, err
	}
	if err = current(); err != nil {
		return task, err
	}
	proof, err := refreshDOCXSession(s)
	if err != nil {
		return task, err
	}
	task.Status = "running"
	if err = persist(); err != nil {
		return task, err
	}
	apply := func() error {
		ctx, cancel := context.WithTimeout(proof.Context, 15*time.Second)
		defer cancel()
		proof.RecordRecovery = func(path string) error {
			task.Recovery = path
			if path == "" {
				return nil
			}
			return persist()
		}
		return target.commit(ctx, data, proof)
	}
	if s.Apply != nil {
		err = s.Apply(apply)
	} else {
		err = apply()
	}
	if err != nil && !errors.Is(err, errDestructiveTargetChanged) {
		task.Status = "interrupted"
	}
	return task, err
}
