package main

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestPDFRequestAndReadOnlyGrant(t *testing.T) {
	if _, err := parseDOCXRequest(url.QueryEscape(`{"action":"run","format":"pdf"}`)); err != nil {
		t.Fatalf("PDF request rejected: %v", err)
	}
	worker := newLocalDOCX(t.TempDir())
	defer closePPTXTestGrants(worker)
	session := docxSession{UserID: 1, DeviceID: "mine", Context: context.Background()}
	dir := t.TempDir()
	grant, err := worker.grant(session, dir, "ro")
	if err != nil {
		t.Fatal(err)
	}
	request := docxRequest{Format: "pdf", SourceGrant: grant.ID, OutputGrant: grant.ID, Source: "资料.pdf", Output: "成果.pdf", Instruction: "整理"}
	if _, err = worker.run(session, request, func() error { return nil }); err == nil || err.Error() != "PDF_OUTPUT_FOLDER" {
		t.Fatalf("read-only output: %v", err)
	}
	if _, err = os.Stat(filepath.Join(dir, request.Output)); !os.IsNotExist(err) {
		t.Fatalf("unexpected output: %v", err)
	}
}

func TestPDFTaskWithRealWorker(t *testing.T) {
	root := usePDFTestRuntime(t)
	original, err := makeLocalPDF(context.Background(), "项目完成 12 项")
	if err != nil {
		t.Fatal(err)
	}
	worker, session, request, output := newPPTXTestTask(t, original)
	request.Format, request.Source, request.Output, request.AgentID = "pdf", "资料.pdf", "成果.pdf", "expert"
	if err := os.Rename(filepath.Join(filepath.Dir(output), "资料.docx"), filepath.Join(filepath.Dir(output), request.Source)); err != nil {
		t.Fatal(err)
	}
	output = filepath.Join(filepath.Dir(output), request.Output)
	var createChecks int
	mockPPTXTestServer(t, func(r *http.Request, body map[string]string) (int, string) {
		if !strings.Contains(r.URL.Path, "/pdf/") || body["agent_id"] != "expert" {
			t.Errorf("incorrect PDF request: %s %v", r.URL.Path, body)
		}
		if strings.HasSuffix(r.URL.Path, "/generate") {
			if !strings.Contains(body["text"], "项目完成 12 项") {
				t.Errorf("source text: %v", body)
			}
			return 200, `{"text":"整理后 12 项"}`
		}
		if body["action"] == "create" {
			createChecks++
		}
		return 200, `{"allowed":true}`
	})
	task, err := worker.run(session, request, func() error { return nil })
	if err != nil || task.Status != "completed" {
		t.Fatalf("task=%+v err=%v", task, err)
	}
	data, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	text, err := readLocalPDF(context.Background(), bytes.NewReader(data), int64(len(data)))
	if err != nil || !strings.Contains(text, "整理后 12 项") {
		t.Fatalf("text=%q err=%v", text, err)
	}
	if createChecks != 2 {
		t.Fatalf("create checks=%d", createChecks)
	}
	if _, err := worker.run(session, request, func() error { return nil }); err == nil || err.Error() != "PDF_CREATE_ONLY" {
		t.Fatalf("collision: %v", err)
	}
	got, _ := os.ReadFile(output)
	if !bytes.Equal(got, data) {
		t.Fatal("existing output changed")
	}
	request.Output = "撤销.pdf"
	createChecks = 0
	mockPPTXTestServer(t, func(r *http.Request, body map[string]string) (int, string) {
		if strings.HasSuffix(r.URL.Path, "/generate") {
			return 200, `{"text":"整理"}`
		}
		if body["action"] == "create" {
			createChecks++
			if createChecks == 2 {
				return 403, `{"error":{"message":"revoked"}}`
			}
		}
		return 200, `{"allowed":true}`
	})
	task, err = worker.run(session, request, func() error { return nil })
	if err == nil || task.Status != "failed" {
		t.Fatalf("revocation task=%+v err=%v", task, err)
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(output), request.Output)); !os.IsNotExist(err) {
		t.Fatalf("revoked output exists: %v", err)
	}
	state, err := newLocalDOCX(worker.home).state(session)
	if err != nil || len(state.Tasks) != 3 || state.Tasks[0].Status != "completed" || state.Tasks[1].Status != "failed" || state.Tasks[2].Status != "failed" {
		t.Fatalf("persisted PDF tasks=%+v err=%v", state.Tasks, err)
	}
	for _, saved := range state.Tasks {
		if saved.Format != "pdf" || saved.AgentID != "expert" {
			t.Fatalf("lost PDF task identity: %+v", saved)
		}
	}
	unchanged, err := os.ReadFile(filepath.Join(filepath.Dir(output), request.Source))
	if err != nil || !bytes.Equal(unchanged, original) {
		t.Fatalf("source changed: %v", err)
	}
	if _, err := runLocalPDFAt(context.Background(), root, "read", strings.NewReader("broken")); err == nil || err.Error() != "PDF_INVALID_SOURCE" {
		t.Fatalf("invalid PDF: %v", err)
	}
	if _, err := runLocalPDFAt(context.Background(), t.TempDir(), "read", strings.NewReader("broken")); err == nil || err.Error() != "PDF_RUNTIME_UNAVAILABLE" {
		t.Fatalf("missing runtime: %v", err)
	}
}

func usePDFTestRuntime(t *testing.T) string {
	t.Helper()
	python := os.Getenv("COLINK_TEST_PDF_PYTHON")
	if _, err := os.Stat(python); err != nil {
		t.Skip("preinstalled PDF test runtime unavailable")
	}
	home := t.TempDir()
	root := filepath.Join(home, "portable")
	if err := os.MkdirAll(filepath.Dir(pythonExe(root)), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(python, pythonExe(root)); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	packages, err := exec.Command(python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))").Output()
	if err != nil {
		t.Fatalf("locate PDF test packages: %v", err)
	}
	if err := os.Symlink(strings.TrimSpace(string(packages)), filepath.Join(root, "packages")); err != nil {
		t.Skipf("package symlink unavailable: %v", err)
	}
	resources, err := filepath.Abs("../pdf")
	if err != nil {
		t.Fatal(err)
	}
	pdfDir := filepath.Join(root, "pdf")
	if err := os.Mkdir(pdfDir, 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"runner.py", "NotoSansSC-Regular.ttf"} {
		data, err := os.ReadFile(filepath.Join(resources, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(pdfDir, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "launch.py"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("OCTOP_HOME", home)
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", "")
	return root
}

func TestPDFWorkerOutputLimits(t *testing.T) {
	var pdf pdfWorkerOutput
	payload := bytes.Repeat([]byte{'x'}, 400001)
	if _, err := pdf.Write(payload); err != nil {
		t.Fatalf("PDF base64 was limited to OCR bound: %v", err)
	}
	var ocr localWorkerOutput
	if _, err := ocr.Write(payload); err == nil {
		t.Fatal("existing OCR output limit was relaxed")
	}
	if _, err := pdf.Write(make([]byte, (docxFileLimit+2)/3*4+4096)); err == nil {
		t.Fatal("PDF worker output is unbounded")
	}
}

func TestPDFRejectsOversizedDecodedDocument(t *testing.T) {
	usePDFTestRuntime(t)
	root := portableDir()
	script := "import sys, json, base64\nsys.stdout.write(json.dumps({'document':base64.b64encode(b'%PDF-'+b'x'*(20*1024*1024-4)).decode()}))\n"
	if err := os.WriteFile(filepath.Join(root, "pdf", "runner.py"), []byte(script), 0600); err != nil {
		t.Fatal(err)
	}
	if data, err := makeLocalPDF(context.Background(), "input"); err == nil || err.Error() != "PDF_TASK_FAILED" || len(data) != 0 {
		t.Fatalf("oversized decoded PDF accepted: len=%d err=%v", len(data), err)
	}
}

func TestPDFHTTPResponseFitsExistingLimit(t *testing.T) {
	mockPPTXTestServer(t, func(*http.Request, map[string]string) (int, string) {
		return 200, `{"text":"` + strings.Repeat(`\u4e2d`, docxTextLimit) + `"}`
	})
	session := docxSession{Base: "https://example.test", DeviceID: "mine", Token: "token", Secret: "secret", Format: "pdf", Context: context.Background()}
	var reply struct {
		Text string `json:"text"`
	}
	if err := docxCall(session, "generate", map[string]string{"text": "input", "instruction": "整理"}, &reply); err != nil {
		t.Fatal(err)
	}
	if reply.Text != strings.Repeat("中", docxTextLimit) {
		t.Fatal("full Chinese response was truncated")
	}
}

func TestPDFFilenamesAndDestructiveActions(t *testing.T) {
	for _, name := range []string{"../escape.pdf", "C:\\input.pdf", "nested/report.pdf", "input.docx", " input.pdf"} {
		if pdfFilename(name) == nil {
			t.Fatalf("accepted unsafe name %q", name)
		}
	}
	worker := newLocalDOCX(t.TempDir())
	for _, action := range []string{"overwrite", "delete"} {
		if _, err := worker.run(docxSession{}, docxRequest{Format: "pdf", Action: action}, func() error { return nil }); err == nil || err.Error() != "PDF_CREATE_ONLY" {
			t.Fatalf("%s: %v", action, err)
		}
	}
}

func TestPDFStopsPublicationAfterSessionOrDirectoryChanges(t *testing.T) {
	usePDFTestRuntime(t)
	original, err := makeLocalPDF(context.Background(), "原始内容")
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range []string{"session changed", "directory changed", "concurrent target", "read revoked"} {
		t.Run(scenario, func(t *testing.T) {
			worker, session, request, oldOutput := newPPTXTestTask(t, original)
			request.Format, request.Source, request.Output = "pdf", "source.pdf", "result.pdf"
			dir := filepath.Dir(oldOutput)
			if err := os.Rename(filepath.Join(dir, "资料.docx"), filepath.Join(dir, request.Source)); err != nil {
				t.Fatal(err)
			}
			output := filepath.Join(dir, request.Output)
			var readChecks int
			mockPPTXTestServer(t, func(r *http.Request, body map[string]string) (int, string) {
				if strings.HasSuffix(r.URL.Path, "/generate") {
					return 200, `{"text":"生成内容"}`
				}
				if body["action"] == "read" {
					readChecks++
					if scenario == "read revoked" && readChecks == 2 {
						return 403, `{"error":{"message":"read revoked"}}`
					}
				}
				return 200, `{"allowed":true}`
			})
			session.Apply = func(publish func() error) error {
				switch scenario {
				case "session changed":
					return errors.New("session changed")
				case "directory changed":
					if err := os.Rename(dir, dir+"-moved"); err != nil {
						t.Fatal(err)
					}
					if err := os.Mkdir(dir, 0700); err != nil {
						t.Fatal(err)
					}
				case "concurrent target":
					if err := os.WriteFile(output, []byte("other process"), 0600); err != nil {
						t.Fatal(err)
					}
				}
				return publish()
			}
			task, err := worker.run(session, request, func() error { return nil })
			expected := map[string]string{"session changed": "session changed", "directory changed": "PDF_OUTPUT_FOLDER", "concurrent target": "PDF_CREATE_ONLY", "read revoked": "read revoked"}[scenario]
			if err == nil || err.Error() != expected || task.Status != "failed" {
				t.Fatalf("task=%+v err=%v", task, err)
			}
			if scenario == "concurrent target" {
				data, _ := os.ReadFile(output)
				if string(data) != "other process" {
					t.Fatal("concurrent target changed")
				}
			} else if _, err := os.Stat(output); !os.IsNotExist(err) {
				t.Fatalf("unexpected output: %v", err)
			}
			checkDir := dir
			if scenario == "directory changed" {
				checkDir = dir + "-moved"
			}
			files, err := os.ReadDir(checkDir)
			if err != nil {
				t.Fatal(err)
			}
			for _, file := range files {
				if strings.HasPrefix(file.Name(), ".colink-") {
					t.Fatal("staging remains")
				}
			}
		})
	}
}

func TestPDFRejectsSourceAliases(t *testing.T) {
	for _, alias := range []string{"symlink", "hardlink"} {
		t.Run(alias, func(t *testing.T) {
			worker, session, request, oldOutput := newPPTXTestTask(t, []byte("private source"))
			request.Format, request.Source, request.Output = "pdf", "source.pdf", "result.pdf"
			dir := filepath.Dir(oldOutput)
			link := os.Link
			if alias == "symlink" {
				link = os.Symlink
			}
			if err := link(filepath.Join(dir, "资料.docx"), filepath.Join(dir, request.Source)); err != nil {
				t.Skipf("%s unavailable: %v", alias, err)
			}
			mockPPTXTestServer(t, func(r *http.Request, _ map[string]string) (int, string) {
				if strings.HasSuffix(r.URL.Path, "/generate") {
					t.Error("aliased source sent to model")
				}
				return 200, `{"allowed":true}`
			})
			task, err := worker.run(session, request, func() error { return nil })
			if err == nil || err.Error() != "PDF_SOURCE_UNAVAILABLE" || task.Status != "failed" {
				t.Fatalf("task=%+v err=%v", task, err)
			}
		})
	}
}

func TestLocalTaskServerErrorsUseSessionLocale(t *testing.T) {
	const english = "PDF generation failed. Check the intranet model connection and try again."
	const chinese = "PDF 生成失败，请检查内网模型连接后重试。"
	var expectedLocale Locale
	mockPPTXTestServer(t, func(r *http.Request, _ map[string]string) (int, string) {
		if r.Header.Get("Accept-Language") != string(expectedLocale) {
			t.Errorf("language header = %q, want %q", r.Header.Get("Accept-Language"), expectedLocale)
		}
		message := chinese
		if r.Header.Get("Accept-Language") == "en" {
			message = english
		}
		return http.StatusServiceUnavailable, `{"error":{"message":"` + message + `"}}`
	})
	for _, format := range []string{"pdf", "docx", "xlsx", "pptx", "ocr"} {
		for _, locale := range []Locale{LocaleEN, LocaleZH} {
			for _, action := range []string{"authorize", "generate"} {
				t.Run(format+"/"+string(locale)+"/"+action, func(t *testing.T) {
					expectedLocale = locale
					session := docxSession{Base: "https://example.test", DeviceID: "mine", Format: format, Locale: locale, Context: context.Background()}
					var err error
					if action == "authorize" {
						err = authorizeDOCX(session, "read")
					} else {
						err = docxCall(session, action, map[string]string{}, &struct{}{})
					}
					want := chinese
					if locale == LocaleEN {
						want = english
					}
					if err == nil || err.Error() != want {
						t.Fatalf("error = %v, want %q", err, want)
					}
				})
			}
		}
	}
}

func TestPDFWorkerContentLimitError(t *testing.T) {
	root := usePDFTestRuntime(t)
	script := "import sys\nsys.stdout.write('{\"error\":\"PDF_CONTENT_LIMIT\"}')\nsys.exit(1)\n"
	if err := os.WriteFile(filepath.Join(root, "pdf", "runner.py"), []byte(script), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := runLocalPDFAt(context.Background(), root, "read", strings.NewReader("input")); err == nil || err.Error() != "PDF_CONTENT_LIMIT" {
		t.Fatalf("error = %v", err)
	}
}
