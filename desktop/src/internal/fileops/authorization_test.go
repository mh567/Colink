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
	s := testStoreWithAuthorizer(func(_ context.Context, _ Credentials, _ string) error {
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
	if err = s.create(ctx, root, 1000, "report.docx", []byte("created"), proof); !errors.Is(err, errPermission) {
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
	if err = s.create(ctx, root, 1000, "report.xlsx", []byte("wrong"), testCredentials); err == nil {
		t.Fatal("DOCX permission created an XLSX file")
	}
	if err = os.WriteFile(filepath.Join(dir, "report.xlsx"), []byte("spreadsheet"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = s.prepare(ctx, ":1.42", 1000, root, "report.xlsx", "delete", testCredentials); err == nil {
		t.Fatal("DOCX permission prepared deletion of an XLSX file")
	}
	if err = s.create(ctx, root, 1000, "report.DOCX", []byte("document"), testCredentials); err != nil {
		t.Fatal(err)
	}
	ocr := testCredentials
	ocr.Format = "ocr"
	if err = s.create(ctx, root, 1000, "scan.txt", []byte("recognized text"), ocr); err != nil {
		t.Fatal(err)
	}
	if _, err = s.prepare(ctx, ":1.42", 1000, root, "scan.txt", "delete", ocr); err == nil {
		t.Fatal("OCR permission prepared destructive action")
	}
	assertFile(t, filepath.Join(dir, "report.xlsx"), "spreadsheet")
}
