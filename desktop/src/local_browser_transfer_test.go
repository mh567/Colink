package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// Use a preinstalled local browser and Python runtime; the fixture never calls an external site.
func TestLocalBrowserNativeFileTransfer(t *testing.T) {
	python := os.Getenv("COLINK_TEST_BROWSER_PYTHON")
	if python == "" {
		t.Skip("set COLINK_TEST_BROWSER_PYTHON to a Python runtime with octop-browser installed")
	}
	runner, err := filepath.Abs(filepath.Join("..", "browser", "runner.py"))
	if err != nil {
		t.Fatal(err)
	}
	received := make(chan string, 1)
	var downloads atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/":
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			fmt.Fprint(w, `<!doctype html><title>File transfer</title>
<input id="attachment" type="file">
<a id="report" href="/report">Report</a>
<script>
document.getElementById("attachment").addEventListener("change", async event => {
  await fetch("/received", {method: "POST", body: await event.target.files[0].text()});
});
</script>`)
		case "/received":
			data, err := io.ReadAll(io.LimitReader(r.Body, 64))
			if err != nil {
				http.Error(w, "read failed", http.StatusBadRequest)
				return
			}
			select {
			case received <- string(data):
			default:
				t.Error("unexpected duplicate upload")
			}
			w.WriteHeader(http.StatusNoContent)
		case "/report":
			downloads.Add(1)
			w.Header().Set("Content-Disposition", `attachment; filename="report.txt"`)
			fmt.Fprint(w, "approved report bytes")
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(server.Close)
	home, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	service := newLocalBrowser(home)
	t.Cleanup(service.close)
	service.authorize = func(docxSession, string) error { return nil }
	service.start = func(ctx context.Context, session docxSession, profile string) (browserProcess, error) {
		cmd := exec.Command(python, "-I", runner, "--private-debug-pipe", "--profile", profile)
		cmd.Env = browserWorkerEnvironment()
		return startBrowserProcess(ctx, session, cmd, func() {})
	}
	session := docxSession{Base: "https://colink.test", UserID: 1, DeviceID: "laptop", Secret: "session", Context: context.Background()}
	current := func() error { return nil }
	run := func(request browserRequest) (browserResult, error) { return service.run(session, request, current) }
	address := server.URL + "/"
	fullAddress := address + "?order=fixture"
	if _, err := run(browserRequest{browserAction: browserAction{Action: "open", URL: fullAddress}}); err != nil {
		t.Fatal(err)
	}
	var tab string
	for deadline := time.Now().Add(8 * time.Second); time.Now().Before(deadline) && tab == ""; {
		list, err := run(browserRequest{browserAction: browserAction{Action: "list"}})
		if err != nil {
			t.Fatal(err)
		}
		for _, candidate := range *list.Tabs {
			if candidate.URL == address {
				tab = candidate.ID
			}
		}
		if tab == "" {
			time.Sleep(50 * time.Millisecond)
		}
	}
	if tab == "" {
		t.Fatal("test page did not open")
	}
	directory := t.TempDir()
	if err := os.WriteFile(filepath.Join(directory, "sample.txt"), []byte("sample data"), 0600); err != nil {
		t.Fatal(err)
	}
	grant, err := service.docx.grant(session, directory, "rw")
	if err != nil {
		t.Fatal(err)
	}
	request := browserRequest{
		browserAction: browserAction{Action: "upload", URL: address, TabID: tab, Selector: "#attachment"},
		GrantID:       grant.ID, File: "sample.txt",
	}
	approve := false
	service.confirmDestructive = func(_ context.Context, _ Locale, action, details string) (bool, error) {
		if !strings.HasPrefix(details, fullAddress+" → ") {
			t.Errorf("%s confirmation did not show the full page address: %s", action, details)
		}
		return approve, nil
	}
	if _, err := run(request); err != errDestructiveCancelled {
		t.Fatalf("rejected upload returned %v", err)
	}
	select {
	case contents := <-received:
		t.Fatalf("cancelled upload sent bytes: %q", contents)
	case <-time.After(150 * time.Millisecond):
	}
	approve = true
	if result, err := run(request); err != nil || result.File != "sample.txt" {
		t.Fatalf("approved upload: %+v %v", result, err)
	}
	select {
	case contents := <-received:
		if contents != "sample data" {
			t.Fatalf("expected original bytes, got %q", contents)
		}
	case <-time.After(4 * time.Second):
		t.Fatal("browser did not send selected file to the test page")
	}
	request.browserAction.Action = "download"
	request.Selector = "#report"
	request.File = "saved-report.txt"
	approve = false
	if _, err := run(request); err != errDestructiveCancelled {
		t.Fatalf("rejected download returned %v", err)
	}
	if _, err := os.Stat(filepath.Join(directory, request.File)); !os.IsNotExist(err) {
		t.Fatalf("cancelled download changed destination: %v", err)
	}
	approve = true
	if result, err := run(request); err != nil || result.File != request.File || result.Size != 21 {
		t.Fatalf("download: %+v %v", result, err)
	}
	if contents, err := os.ReadFile(filepath.Join(directory, request.File)); err != nil || string(contents) != "approved report bytes" {
		t.Fatalf("saved file: %q %v", contents, err)
	}
	if count := downloads.Load(); count != 1 {
		t.Fatalf("expected a single request for the approved download, got %d", count)
	}
}
