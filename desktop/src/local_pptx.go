package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf8"
)

func pptxFilename(name string) error {
	if name == "" || !filepath.IsLocal(name) || filepath.Base(name) != name || strings.ContainsAny(name, "/\\:") || !strings.EqualFold(filepath.Ext(name), ".pptx") || strings.TrimSpace(name) != name {
		return errors.New("PPTX_FILENAME")
	}
	return nil
}

func pptxSourceFilename(name string) error {
	if strings.EqualFold(filepath.Ext(name), ".docx") {
		return docxFilename(name)
	}
	return pptxFilename(name)
}

type pptxResult struct {
	Text  string `json:"text"`
	Deck  []byte `json:"deck"`
	Error string `json:"error"`
}

func runLocalPPTX(ctx context.Context, mode string, input io.Reader) (pptxResult, error) {
	var result pptxResult
	if err := ctx.Err(); err != nil {
		return result, errors.New("PPTX_TASK_FAILED")
	}
	if err := ensurePortable(LocaleZH, func(string) {}); err != nil {
		return result, errors.New("PPTX_RUNTIME_UNAVAILABLE")
	}
	root := portableDir()
	script := filepath.Join(root, "pptx", "runner.py")
	if info, err := os.Stat(script); err != nil || !info.Mode().IsRegular() {
		return result, errors.New("PPTX_RUNTIME_UNAVAILABLE")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, pythonExe(root), "-I", script, mode)
	cmd.Dir = root
	cmd.Stdin = input
	cmd.Stderr = io.Discard
	cmd.Env = []string{"PYTHONUTF8=1"}
	if systemRoot := os.Getenv("SystemRoot"); systemRoot != "" {
		cmd.Env = append(cmd.Env, "SystemRoot="+systemRoot)
	}
	var stdout localWorkerOutput
	cmd.Stdout = &stdout
	cmd.WaitDelay = 2 * time.Second
	runErr := cmd.Run()
	if ctx.Err() != nil || json.Unmarshal(stdout.Bytes(), &result) != nil {
		return pptxResult{}, errors.New("PPTX_TASK_FAILED")
	}
	switch result.Error {
	case "PPTX_INVALID_SOURCE", "PPTX_SOURCE_LIMIT", "PPTX_NO_TEXT", "PPTX_INVALID_REPORT", "PPTX_RUNTIME_UNAVAILABLE":
		return pptxResult{}, errors.New(result.Error)
	}
	if runErr != nil || result.Error != "" {
		return pptxResult{}, errors.New("PPTX_TASK_FAILED")
	}
	return result, nil
}

func readLocalPPTX(ctx context.Context, reader io.ReaderAt, size int64) (string, error) {
	if size <= 0 || size > docxFileLimit {
		return "", errors.New("PPTX_SOURCE_LIMIT")
	}
	result, err := runLocalPPTX(ctx, "read", io.NewSectionReader(reader, 0, size))
	if err != nil {
		return "", err
	}
	if strings.TrimSpace(result.Text) == "" || utf8.RuneCountInString(result.Text) > docxTextLimit {
		return "", errors.New("PPTX_TASK_FAILED")
	}
	return result.Text, nil
}

func makeLocalPPTX(ctx context.Context, report string) ([]byte, error) {
	result, err := runLocalPPTX(ctx, "create", strings.NewReader(report))
	if err != nil {
		return nil, err
	}
	if !bytes.HasPrefix(result.Deck, []byte("PK\x03\x04")) {
		return nil, errors.New("PPTX_TASK_FAILED")
	}
	return result.Deck, nil
}
