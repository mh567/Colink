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

func pdfFilename(name string) error {
	if name == "" || !filepath.IsLocal(name) || filepath.Base(name) != name || strings.ContainsAny(name, "/\\:") || !strings.EqualFold(filepath.Ext(name), ".pdf") || strings.TrimSpace(name) != name {
		return errors.New("PDF_FILENAME")
	}
	return nil
}

type pdfWorkerOutput struct{ bytes.Buffer }

func (b *pdfWorkerOutput) Write(p []byte) (int, error) {
	if b.Len()+len(p) > (docxFileLimit+2)/3*4+4096 {
		return 0, errors.New("PDF_TASK_FAILED")
	}
	return b.Buffer.Write(p)
}

type pdfResult struct {
	Text     string `json:"text"`
	Document []byte `json:"document"`
	Error    string `json:"error"`
}

func runLocalPDFAt(ctx context.Context, root, mode string, input io.Reader) (pdfResult, error) {
	var result pdfResult
	script := filepath.Join(root, "pdf", "runner.py")
	if info, err := os.Stat(script); err != nil || !info.Mode().IsRegular() {
		return result, errors.New("PDF_RUNTIME_UNAVAILABLE")
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, pythonExe(root), "-I", script, mode)
	cmd.Dir, cmd.Stdin, cmd.Stderr = root, input, io.Discard
	cmd.Env = []string{"PYTHONUTF8=1"}
	if systemRoot := os.Getenv("SystemRoot"); systemRoot != "" {
		cmd.Env = append(cmd.Env, "SystemRoot="+systemRoot)
	}
	var stdout pdfWorkerOutput
	cmd.Stdout = &stdout
	cmd.WaitDelay = 2 * time.Second
	runErr := cmd.Run()
	decoder := json.NewDecoder(bytes.NewReader(stdout.Bytes()))
	decoder.DisallowUnknownFields()
	var extra any
	if ctx.Err() != nil || decoder.Decode(&result) != nil || decoder.Decode(&extra) != io.EOF {
		return pdfResult{}, errors.New("PDF_TASK_FAILED")
	}
	switch result.Error {
	case "PDF_FILE_LIMIT", "PDF_PAGE_LIMIT", "PDF_CONTENT_LIMIT", "PDF_TEXT_LIMIT", "PDF_INVALID_SOURCE", "PDF_ENCRYPTED", "PDF_NO_TEXT", "PDF_NO_TEXT_PAGE", "PDF_INVALID_TEXT", "PDF_UNSUPPORTED_GLYPH", "PDF_FONT_MISSING", "PDF_RUNTIME_UNAVAILABLE", "PDF_NETWORK_DENIED", "PDF_TASK_FAILED":
		return pdfResult{}, errors.New(result.Error)
	}
	if runErr != nil || result.Error != "" {
		return pdfResult{}, errors.New("PDF_TASK_FAILED")
	}
	return result, nil
}

func readLocalPDF(ctx context.Context, reader io.ReaderAt, size int64) (string, error) {
	if size <= 0 || size > docxFileLimit {
		return "", errors.New("PDF_FILE_LIMIT")
	}
	if err := ensurePortable(LocaleZH, func(string) {}); err != nil {
		return "", errors.New("PDF_RUNTIME_UNAVAILABLE")
	}
	result, err := runLocalPDFAt(ctx, portableDir(), "read", io.NewSectionReader(reader, 0, size))
	if err != nil {
		return "", err
	}
	if !utf8.ValidString(result.Text) || strings.TrimSpace(result.Text) == "" || utf8.RuneCountInString(result.Text) > docxTextLimit {
		return "", errors.New("PDF_TASK_FAILED")
	}
	return result.Text, nil
}

func makeLocalPDF(ctx context.Context, text string) ([]byte, error) {
	if err := ensurePortable(LocaleZH, func(string) {}); err != nil {
		return nil, errors.New("PDF_RUNTIME_UNAVAILABLE")
	}
	result, err := runLocalPDFAt(ctx, portableDir(), "create", strings.NewReader(text))
	if err != nil {
		return nil, err
	}
	if len(result.Document) > docxFileLimit || !bytes.HasPrefix(result.Document, []byte("%PDF-")) {
		return nil, errors.New("PDF_TASK_FAILED")
	}
	return result.Document, nil
}
