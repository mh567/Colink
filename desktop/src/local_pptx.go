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

func prepareLocalPPTXRunner(root string) (string, func(), error) {
	installed := filepath.Join(root, "pptx", "runner.py")
	info, statErr := os.Stat(installed)
	installedReady := statErr == nil && info.Mode().IsRegular()
	bundled := sameVersionBundledPPTXRunner(root)
	if installedReady {
		if bundled == nil {
			return installed, func() {}, nil
		}
		if info.Size() == int64(len(bundled)) {
			if content, err := os.ReadFile(installed); err == nil && bytes.Equal(content, bundled) {
				return installed, func() {}, nil
			}
		}
	}
	if bundled == nil {
		return "", nil, errors.New("PPTX_RUNTIME_UNAVAILABLE")
	}

	// Use the updated runner with existing packages without modifying the live runtime.
	dir, err := os.MkdirTemp(root, "pptx-worker-")
	if err != nil {
		return "", nil, errors.New("PPTX_RUNTIME_UNAVAILABLE")
	}
	cleanup := func() { _ = os.RemoveAll(dir) }
	script := filepath.Join(dir, "runner.py")
	if err := os.WriteFile(script, bundled, 0o600); err != nil {
		cleanup()
		return "", nil, errors.New("PPTX_RUNTIME_UNAVAILABLE")
	}
	return script, cleanup, nil
}

func sameVersionBundledPPTXRunner(root string) []byte {
	files, closeArchive, err := bundledPortableFiles()
	if err != nil {
		return nil
	}
	defer closeArchive()
	version, err := versionFromZip(files)
	installed := portableVersion(root)
	if err != nil || installed == "" || version == "" || compareVersions(version, installed) != 0 {
		return nil
	}
	if bundledPortableRevision(files) < installedPortableRevision(root) {
		return nil
	}

	return readPortableBundleFile(files, "pptx/runner.py", 64<<10)
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
	script, cleanup, err := prepareLocalPPTXRunner(root)
	if err != nil {
		return result, err
	}
	defer cleanup()
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
	case "PPTX_INVALID_SOURCE", "PPTX_SOURCE_LIMIT", "PPTX_NO_TEXT", "PPTX_UNSUPPORTED_CHART", "PPTX_INVALID_REPORT", "PPTX_RUNTIME_UNAVAILABLE":
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
