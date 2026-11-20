package fileops

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestManagedPDFRequiresExplicitCreateContext(t *testing.T) {
	root, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	creds := testCredentials
	creds.Format = "pdf"
	if !validAuthorizationRequest(creds, "create") || validAuthorizationRequest(creds, "delete") {
		t.Fatal("invalid PDF permission scope")
	}
	s := testStore()
	if err := s.create(context.Background(), root, 1000, "result.pdf", []byte("pdf"), creds, authorizationRequest{Action: "create"}); !errors.Is(err, errPermission) {
		t.Fatalf("generic create bypass: %v", err)
	}
}

func TestManagedPDFPublicationBoundaries(t *testing.T) {
	for _, scenario := range []string{"success", "read revoked", "expert revoked", "cancelled", "directory changed", "collision"} {
		t.Run(scenario, func(t *testing.T) {
			dir := filepath.Join(t.TempDir(), "managed")
			if err := os.Mkdir(dir, 0700); err != nil {
				t.Fatal(err)
			}
			root, err := os.OpenRoot(dir)
			if err != nil {
				t.Fatal(err)
			}
			defer root.Close()
			creds := testCredentials
			creds.Format = "pdf"
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			var createChecks, readChecks int
			s := testStoreWithAuthorizer(func(_ context.Context, proof Credentials, request authorizationRequest) error {
				if proof != creds || request.AgentID != "selected-expert" {
					t.Errorf("lost PDF authorization context: %+v %+v", proof, request)
				}
				if request.Action == "read" {
					readChecks++
					if scenario == "read revoked" {
						return errors.New("revoked")
					}
					return nil
				}
				if request.Action != "create" {
					t.Errorf("unexpected action: %s", request.Action)
				}
				createChecks++
				if createChecks == 2 {
					entries, err := os.ReadDir(dir)
					if err != nil || len(entries) != 1 || !strings.HasPrefix(entries[0].Name(), ".colink-") {
						t.Errorf("not staged before final check: %v %v", entries, err)
					}
					switch scenario {
					case "expert revoked":
						return errors.New("revoked")
					case "cancelled":
						cancel()
					case "directory changed":
						if runtime.GOOS == "windows" {
							t.Skip("Windows prevents renaming directories containing open files")
						}
						if err := os.Rename(dir, dir+"-moved"); err != nil {
							t.Fatal(err)
						}
						if err := os.Mkdir(dir, 0700); err != nil {
							t.Fatal(err)
						}
					case "collision":
						if err := os.WriteFile(filepath.Join(dir, "result.pdf"), []byte("existing"), 0600); err != nil {
							t.Fatal(err)
						}
					}
				}
				return nil
			})
			request := authorizationRequest{Action: "create", AgentID: "selected-expert", pdfCreate: true}
			err = s.create(ctx, root, 1000, "result.pdf", []byte("generated"), creds, request)
			if scenario == "success" {
				if err != nil {
					t.Fatal(err)
				}
				assertFile(t, filepath.Join(dir, "result.pdf"), "generated")
			} else {
				if err == nil {
					t.Fatal("unsafe PDF published")
				}
				if scenario == "collision" {
					assertFile(t, filepath.Join(dir, "result.pdf"), "existing")
				} else if _, err := os.Stat(filepath.Join(dir, "result.pdf")); !os.IsNotExist(err) {
					t.Fatalf("unexpected published file: %v", err)
				}
			}
			if readChecks != 1 {
				t.Fatalf("read rechecks=%d", readChecks)
			}
			entries, err := root.ReadFile("result.pdf")
			if scenario == "directory changed" && !os.IsNotExist(err) {
				t.Fatalf("wrote stale directory: %q %v", entries, err)
			}
			files, err := os.ReadDir(root.Name())
			if err != nil {
				t.Fatal(err)
			}
			for _, file := range files {
				if strings.HasPrefix(file.Name(), ".colink-") {
					t.Fatal("staged file remains")
				}
			}
		})
	}
}

func TestManagedPDFRejectsInvalidTargetsAndDestructiveActions(t *testing.T) {
	root, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	creds := testCredentials
	creds.Format = "pdf"
	s := testStore()
	request := authorizationRequest{Action: "create", pdfCreate: true}
	for _, name := range []string{"../escape.pdf", "report.docx", "nested/report.pdf"} {
		if err := s.create(context.Background(), root, 1000, name, []byte("pdf"), creds, request); err == nil {
			t.Fatalf("accepted %s", name)
		}
	}
	for _, operation := range []string{"overwrite", "delete"} {
		if _, err := s.prepare(context.Background(), ":1.42", 1000, root, "report.pdf", operation, creds); err == nil {
			t.Fatalf("accepted %s", operation)
		}
	}
}

func TestManagedPDFDisconnectCancelsBeforePublication(t *testing.T) {
	root, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	clientCtx, clientCancel := context.WithCancel(context.Background())
	defer clientCancel()
	daemonCtx := context.Background()
	const sender = ":1.42"
	proof := testCredentials
	proof.Format = "pdf"
	var checks int
	var operationCtx context.Context
	allowed := make(chan struct{})
	resume := make(chan struct{})
	s := testStoreWithAuthorizer(func(ctx context.Context, _ Credentials, request authorizationRequest) error {
		if request.Action == "create" {
			checks++
			if checks == 2 {
				operationCtx = ctx
				close(allowed)
			}
		}
		return nil
	})
	s.protect = func(*os.File, uint32) error { <-resume; return nil }
	operation, finish := s.trackPDF(daemonCtx, sender)
	defer finish()
	result := make(chan error, 1)
	go func() {
		result <- s.create(operation, root, 1000, "result.pdf", []byte("%PDF-generated"), proof, authorizationRequest{Action: "create", AgentID: "expert", pdfCreate: true})
	}()
	<-allowed
	s.cancelPDF(":1.99")
	if operation.Err() != nil {
		t.Fatal("other sender cancelled operation")
	}
	clientCancel()
	disconnected := make(chan struct{})
	go func() { s.disconnect(sender); close(disconnected) }()
	select {
	case <-operationCtx.Done():
	case <-time.After(time.Second):
		t.Error("sender cancellation was blocked by publication or not propagated")
	}
	close(resume)
	createErr := <-result
	<-disconnected
	data, readErr := root.ReadFile("result.pdf")
	if clientCtx.Err() == nil || daemonCtx.Err() != nil || createErr == nil || !os.IsNotExist(readErr) {
		t.Fatalf("client=%v daemon=%v create=%v output=%q read=%v", clientCtx.Err(), daemonCtx.Err(), createErr, data, readErr)
	}
	files, err := os.ReadDir(root.Name())
	if err != nil || len(files) != 0 {
		t.Fatalf("staging remains: %v %v", files, err)
	}
}

func TestManagedPDFTrackingCleanupAndSenderIsolation(t *testing.T) {
	s := testStore()
	first, finishFirst := s.trackPDF(context.Background(), ":1.42")
	second, finishSecond := s.trackPDF(context.Background(), ":1.43")
	s.cancelPDF(":1.42")
	if first.Err() == nil || second.Err() != nil {
		t.Fatal("sender cancellation crossed operation boundaries")
	}
	finishFirst()
	finishSecond()
	s.pdfMu.Lock()
	defer s.pdfMu.Unlock()
	if len(s.pdfOps) != 0 {
		t.Fatalf("operations retained after completion: %d", len(s.pdfOps))
	}
}
