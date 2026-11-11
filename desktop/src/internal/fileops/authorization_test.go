package fileops

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestManagedOperationsRecheckServerPermission(t *testing.T) {
	ctx := context.Background()
	proof := testCredentials
	allowed := false
	s := testStoreWithAuthorizer(func(context.Context, Credentials, authorizationRequest) error {
		if !allowed {
			return errors.New("server refused operation")
		}
		return nil
	})
	dir := t.TempDir()
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	path := filepath.Join(dir, "report.docx")
	if err = s.create(ctx, root, 1000, "report.docx", []byte("created"), proof, authorizationRequest{Action: "create"}); !errors.Is(err, errPermission) {
		t.Fatalf("unauthorized create: %v", err)
	}
	if _, err = os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("unauthorized create left a file: %v", err)
	}
	if err = os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = s.prepare(ctx, ":1.42", 1000, root, "report.docx", "delete", proof); !errors.Is(err, errPermission) {
		t.Fatalf("unauthorized prepare: %v", err)
	}
	allowed = true
	approve := func(context.Context, string, string) (bool, error) { return true, nil }
	token := testPrepare(t, s, dir, "report.docx", "delete")
	if err = s.confirm(ctx, ":1.42", token, "delete", proof, nil, approve); err != nil {
		t.Fatal(err)
	}
	allowed = false
	if err = s.commit(ctx, ":1.42", token, "delete", proof, nil); !errors.Is(err, errPermission) {
		t.Fatalf("revoked commit: %v", err)
	}
	allowed = true
	if err = s.commit(ctx, ":1.42", token, "delete", proof, nil); err == nil {
		t.Fatal("revoked approval was restored")
	}
	assertFile(t, path, "original")
}

func TestManagedApprovalRejectsCredentialAndActionChanges(t *testing.T) {
	ctx := context.Background()
	s := testStore()
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	approve := func(context.Context, string, string) (bool, error) { return true, nil }
	for _, mutation := range []struct {
		operation string
		mutate    func(*Credentials)
	}{
		{"overwrite", func(*Credentials) {}},
		{"delete", func(c *Credentials) { c.Format = "xlsx" }},
		{"delete", func(c *Credentials) { c.DeviceID = "new-device" }},
		{"delete", func(c *Credentials) { c.Session = "new-session" }},
	} {
		token := testPrepare(t, s, dir, "report.docx", "delete")
		if err := s.confirm(ctx, ":1.42", token, "delete", testCredentials, nil, approve); err != nil {
			t.Fatal(err)
		}
		wrong := testCredentials
		mutation.mutate(&wrong)
		if err := s.commit(ctx, ":1.42", token, mutation.operation, wrong, nil); err == nil {
			t.Fatal("another action, format, device or session used the approval")
		}
		assertFile(t, path, "original")
	}

	token := testPrepare(t, s, dir, "report.docx", "delete")
	if err := s.confirm(ctx, ":1.42", token, "delete", testCredentials, nil, approve); err != nil {
		t.Fatal(err)
	}
	refreshed := testCredentials
	refreshed.Bearer = "new-bearer"
	if err := s.commit(ctx, ":1.42", token, "delete", refreshed, nil); err != nil {
		t.Fatalf("bearer renewal blocked the same session: %v", err)
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("confirmed target still exists: %v", err)
	}
}

func TestManagedExpiryIsCheckedEvenBeforeTimerCallback(t *testing.T) {
	ctx := context.Background()
	s := testStore()
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	token := testPrepare(t, s, dir, "report.docx", "delete")
	if err := s.confirm(ctx, ":1.42", token, "delete", testCredentials, nil, func(context.Context, string, string) (bool, error) { return true, nil }); err != nil {
		t.Fatal(err)
	}
	s.mu.Lock()
	s.intents[token].deadline = time.Now().Add(-time.Second)
	s.mu.Unlock()
	if err := s.commit(ctx, ":1.42", token, "delete", testCredentials, nil); err == nil {
		t.Fatal("expired confirmation changed the file before the timer ran")
	}
	assertFile(t, path, "original")
}

func TestManagedExpiryAlsoAppliesAfterStagingReplacement(t *testing.T) {
	ctx := context.Background()
	s := testStore()
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	token := testPrepare(t, s, dir, "report.docx", "overwrite")
	if err := s.confirm(ctx, ":1.42", token, "overwrite", testCredentials, digestOf("replacement"), func(context.Context, string, string) (bool, error) { return true, nil }); err != nil {
		t.Fatal(err)
	}
	s.protect = func(*os.File, uint32) error {
		s.intents[token].deadline = time.Now().Add(-time.Second)
		return nil
	}
	if err := s.commit(ctx, ":1.42", token, "overwrite", testCredentials, []byte("replacement")); err == nil {
		t.Fatal("confirmation expired before the staged replacement reached the target")
	}
	assertFile(t, path, "original")
}

func TestManagedFilesStayWithinTheAuthorizedFormat(t *testing.T) {
	ctx := context.Background()
	s := testStore()
	dir := t.TempDir()
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	request := authorizationRequest{Action: "create"}
	if err = s.create(ctx, root, 1000, "report.xlsx", []byte("wrong"), testCredentials, request); err == nil {
		t.Fatal("DOCX permission created an XLSX file")
	}
	if err = os.WriteFile(filepath.Join(dir, "report.xlsx"), []byte("spreadsheet"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = s.prepare(ctx, ":1.42", 1000, root, "report.xlsx", "delete", testCredentials); err == nil {
		t.Fatal("DOCX permission prepared deletion of an XLSX file")
	}
	if err = s.create(ctx, root, 1000, "report.DOCX", []byte("document"), testCredentials, request); err != nil {
		t.Fatal(err)
	}
	ocr := testCredentials
	ocr.Format = "ocr"
	if err = s.create(ctx, root, 1000, "scan.txt", []byte("recognized text"), ocr, request); err != nil {
		t.Fatal(err)
	}
	if _, err = s.prepare(ctx, ":1.42", 1000, root, "scan.txt", "delete", ocr); err == nil {
		t.Fatal("OCR permission prepared destructive action")
	}
	assertFile(t, filepath.Join(dir, "report.xlsx"), "spreadsheet")
}

func TestManagedPPTXCanCreateButCannotDelete(t *testing.T) {
	ctx := context.Background()
	root, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	creds := testCredentials
	creds.Format = "pptx"
	if !validAuthorizationRequest(creds, "create") || validAuthorizationRequest(creds, "delete") {
		t.Error("PPTX authorization must allow create and deny delete")
	}
	s := testStore()
	const name = "项目汇报.pptx"
	request := authorizationRequest{Action: "create"}
	if err := s.create(ctx, root, 1000, name, []byte("missing source context"), creds, request); !errors.Is(err, errPermission) {
		t.Fatalf("PPTX without source context: %v", err)
	}
	request.SourceFormat = "pptx"
	if err := s.create(ctx, root, 1000, name, []byte("presentation"), creds, request); err != nil {
		t.Fatal(err)
	}
	if err := s.create(ctx, root, 1000, name, []byte("collision"), creds, request); !errors.Is(err, os.ErrExist) {
		t.Fatalf("PPTX collision: %v", err)
	}
	if _, err := s.prepare(ctx, ":1.42", 1000, root, name, "delete", creds); err == nil {
		t.Fatal("PPTX permission prepared deletion")
	}
	assertFile(t, filepath.Join(root.Name(), name), "presentation")
	if entries, err := os.ReadDir(root.Name()); err != nil || len(entries) != 1 {
		t.Fatalf("PPTX directory entries: %v, %v", entries, err)
	}
}

func TestManagedPPTXDirectorySyncFailureAllowsSameNameRetry(t *testing.T) {
	ctx := context.Background()
	root, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	creds := testCredentials
	creds.Format = "pptx"
	s := testStore()
	syncFailure := errors.New("simulated directory sync failure")
	s.syncDir = func(*os.Root) error { return syncFailure }
	const name = "项目汇报.pptx"
	data := []byte("presentation")
	request := authorizationRequest{Action: "create", SourceFormat: "pptx"}

	if err := s.create(ctx, root, 1000, name, data, creds, request); !errors.Is(err, syncFailure) {
		t.Fatalf("create error = %v, want directory sync failure", err)
	}
	if entries, err := os.ReadDir(root.Name()); err != nil || len(entries) != 0 {
		t.Fatalf("failed create left directory entries: %v, %v", entries, err)
	}
	s.syncDir = func(*os.Root) error { return nil }
	if err := s.create(ctx, root, 1000, name, data, creds, request); err != nil {
		t.Fatalf("same-name retry failed: %v", err)
	}
	assertFile(t, filepath.Join(root.Name(), name), "presentation")
}
