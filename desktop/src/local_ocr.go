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
	"runtime"
	"strings"
	"time"
	"unicode/utf8"
)

func ocrFilename(name, extension string) error {
	if name == "" || !filepath.IsLocal(name) || filepath.Base(name) != name || strings.ContainsAny(name, "/\\:") || !strings.EqualFold(filepath.Ext(name), extension) || strings.TrimSpace(name) != name {
		if extension == ".pdf" {
			return errors.New("OCR_INPUT_NAME")
		}
		return errors.New("OCR_OUTPUT_NAME")
	}
	return nil
}
func ocrSourceFilename(name string) error { return ocrFilename(name, ".pdf") }
func ocrOutputFilename(name string) error { return ocrFilename(name, ".txt") }

// Bound subprocess output before buffering; stderr must never leak source text.
type ocrOutputBuffer struct{ bytes.Buffer }

func (b *ocrOutputBuffer) Write(p []byte) (int, error) {
	if b.Len()+len(p) > 400000 {
		return 0, errors.New("OCR_TEXT_LIMIT")
	}
	return b.Buffer.Write(p)
}

func readLocalOCR(ctx context.Context, reader io.ReaderAt, size int64) (string, error) {
	switch runtime.GOOS + "/" + runtime.GOARCH {
	case "linux/amd64", "linux/arm64", "windows/amd64", "darwin/arm64":
	default:
		return "", errors.New("OCR_UNSUPPORTED_PLATFORM")
	}
	if size <= 0 || size > docxFileLimit {
		return "", errors.New("OCR_FILE_LIMIT")
	}
	if err := ctx.Err(); err != nil {
		return "", errors.New("OCR_CANCELLED")
	}
	// Remote-server clients also need the bundled interpreter, never a system Python.
	if err := ensurePortable(LocaleZH, func(string) {}); err != nil {
		return "", errors.New("OCR_RESOURCES_MISSING")
	}
	return runLocalOCR(ctx, reader, size, portableDir())
}

func runLocalOCR(ctx context.Context, reader io.ReaderAt, size int64, root string) (string, error) {
	if size <= 0 || size > docxFileLimit {
		return "", errors.New("OCR_FILE_LIMIT")
	}
	script := filepath.Join(root, "ocr", "runner.py")
	if info, err := os.Stat(script); err != nil || !info.Mode().IsRegular() {
		return "", errors.New("OCR_RESOURCES_MISSING")
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, pythonExe(root), "-I", script)
	cmd.Dir = root
	cmd.Stdin = io.NewSectionReader(reader, 0, size)
	var stdout ocrOutputBuffer
	cmd.Stdout = &stdout
	cmd.Stderr = io.Discard
	// -I isolates Python imports; do not forward model or device credentials.
	cmd.Env = []string{"PYTHONUTF8=1"}
	if systemRoot := os.Getenv("SystemRoot"); systemRoot != "" {
		cmd.Env = append(cmd.Env, "SystemRoot="+systemRoot)
	}
	cmd.WaitDelay = 2 * time.Second
	runErr := cmd.Run()
	if errors.Is(ctx.Err(), context.Canceled) {
		return "", errors.New("OCR_CANCELLED")
	}
	if ctx.Err() != nil {
		return "", errors.New("OCR_TIMEOUT")
	}
	var result struct {
		Text  string `json:"text"`
		Error string `json:"error"`
	}
	decoder := json.NewDecoder(bytes.NewReader(stdout.Bytes()))
	decoder.DisallowUnknownFields()
	decodeErr := decoder.Decode(&result)
	var extra any
	if decodeErr != nil || decoder.Decode(&extra) != io.EOF {
		return "", errors.New("OCR_INVALID_RESULT")
	}
	if result.Error != "" {
		return "", errors.New(result.Error)
	}
	if runErr != nil {
		return "", errors.New("OCR_PROCESS_FAILED")
	}
	if !utf8.ValidString(result.Text) || strings.TrimSpace(result.Text) == "" || utf8.RuneCountInString(result.Text) > docxTextLimit {
		return "", errors.New("OCR_INVALID_RESULT")
	}
	return result.Text, nil
}
