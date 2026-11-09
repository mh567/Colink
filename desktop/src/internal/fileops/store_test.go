package fileops

import (
	"context"
	"crypto/sha256"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var testCredentials = Credentials{Format: "docx", DeviceID: "01ARZ3NDEKTSV4RRFFQ69G5FAV", Bearer: "test-bearer", Session: "test-session"}

func testStore() *store {
	return testStoreWithAuthorizer(func(context.Context, Credentials, string) error { return nil })
}

func testStoreWithAuthorizer(authorizer ServerAuthorizer) *store {
	return newStore(
		func(*os.File, uint32) error { return nil },
		func(*os.File, uint32) error { return nil },
		func(*os.Root) error { return nil },
		authorizer,
	)
}

func testPrepare(t *testing.T, s *store, dir, name, kind string) string {
	t.Helper()
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	token, err := s.prepare(context.Background(), ":1.42", 1000, root, name, kind, testCredentials)
	if err != nil {
		root.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() { s.abort(":1.42", token) })
	return token
}

func assertFile(t *testing.T, path, want string) {
	t.Helper()
	got, err := os.ReadFile(path)
	if err != nil || string(got) != want {
		t.Fatalf("file = %q, %v; want %q", got, err, want)
	}
}

func digestOf(text string) []byte {
	digest := sha256.Sum256([]byte(text))
	return digest[:]
}

func TestManagedOperationIsBoundToConfirmationAndCaller(t *testing.T) {
	ctx := context.Background()
	s := testStore()
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte("before"), 0600); err != nil {
		t.Fatal(err)
	}
	token := testPrepare(t, s, dir, "report.docx", "overwrite")
	if err := s.commit(ctx, ":1.42", token, "overwrite", testCredentials, []byte("unconfirmed")); err == nil {
		t.Fatal("commit without confirmation succeeded")
	}
	assertFile(t, path, "before")
	if err := s.confirm(ctx, ":1.42", token, "overwrite", testCredentials, digestOf("after"), func(_ context.Context, op, target string) (bool, error) {
		if op != "overwrite" || target != path {
			t.Fatalf("prompt action and target = %q, %q", op, target)
		}
		return true, nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.commit(ctx, ":1.43", token, "overwrite", testCredentials, []byte("wrong caller")); err == nil {
		t.Fatal("another caller used confirmation")
	}
	assertFile(t, path, "before")
	if err := s.commit(ctx, ":1.42", token, "overwrite", testCredentials, []byte("after")); err != nil {
		t.Fatal(err)
	}
	if err := s.commit(ctx, ":1.42", token, "overwrite", testCredentials, []byte("replay")); err == nil {
		t.Fatal("confirmation was replayed")
	}
	assertFile(t, path, "after")
}

func TestManagedCancellationAndReplacementLeaveFileIntact(t *testing.T) {
	ctx := context.Background()
	s := testStore()
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte("before"), 0600); err != nil {
		t.Fatal(err)
	}
	token := testPrepare(t, s, dir, "report.docx", "delete")
	if err := s.confirm(ctx, ":1.42", token, "delete", testCredentials, nil, func(context.Context, string, string) (bool, error) {
		return false, nil
	}); !errors.Is(err, ErrCancelled) {
		t.Fatalf("cancel error = %v", err)
	}
	if err := s.commit(ctx, ":1.42", token, "delete", testCredentials, nil); err == nil {
		t.Fatal("commit after cancel succeeded")
	}
	assertFile(t, path, "before")
	token = testPrepare(t, s, dir, "report.docx", "delete")
	if err := s.confirm(ctx, ":1.42", token, "delete", testCredentials, nil, func(context.Context, string, string) (bool, error) {
		return true, nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(path, filepath.Join(dir, "renamed.docx")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := s.commit(ctx, ":1.42", token, "delete", testCredentials, nil); err == nil {
		t.Fatal("confirmed delete removed a replacement file")
	}
	assertFile(t, path, "replacement")
	assertFile(t, filepath.Join(dir, "renamed.docx"), "before")
}

func TestManagedCreationAndDeletionAreSeparateActions(t *testing.T) {
	ctx := context.Background()
	s := testStore()
	dir := t.TempDir()
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	if err = s.create(ctx, root, 1000, "report.docx", []byte("created"), testCredentials); err != nil {
		t.Fatal(err)
	}
	if err = s.create(ctx, root, 1000, "report.docx", []byte("collision"), testCredentials); err == nil {
		t.Fatal("create replaced a file")
	}
	path := filepath.Join(dir, "report.docx")
	assertFile(t, path, "created")
	token := testPrepare(t, s, dir, "report.docx", "delete")
	approve := func(context.Context, string, string) (bool, error) {
		return true, nil
	}
	if err = s.confirm(ctx, ":1.42", token, "delete", testCredentials, nil, approve); err != nil {
		t.Fatal(err)
	}
	if err = s.commit(ctx, ":1.42", token, "delete", testCredentials, []byte("wrong action")); err == nil {
		t.Fatal("delete confirmation accepted a replacement payload")
	}
	assertFile(t, path, "created")
	if err = s.commit(ctx, ":1.42", token, "delete", testCredentials, nil); err == nil {
		t.Fatal("mismatched payload did not invalidate approval")
	}
	token = testPrepare(t, s, dir, "report.docx", "delete")
	if err = s.confirm(ctx, ":1.42", token, "delete", testCredentials, nil, approve); err != nil {
		t.Fatal(err)
	}
	if err = s.commit(ctx, ":1.42", token, "delete", testCredentials, nil); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("confirmed file still present: %v", err)
	}
}

func TestManagedApprovalCannotAuthorizeDifferentContent(t *testing.T) {
	ctx := context.Background()
	s := testStore()
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte("before"), 0600); err != nil {
		t.Fatal(err)
	}
	prompts := 0
	approve := func(context.Context, string, string) (bool, error) {
		prompts++
		return true, nil
	}
	token := testPrepare(t, s, dir, "report.docx", "overwrite")
	if err := s.confirm(ctx, ":1.42", token, "overwrite", testCredentials, digestOf("approved"), approve); err != nil {
		t.Fatal(err)
	}
	if err := s.confirm(ctx, ":1.42", token, "overwrite", testCredentials, digestOf("approved"), approve); err != nil || prompts != 1 {
		t.Fatalf("identical confirmation repeated the prompt: %v; %d", err, prompts)
	}
	if err := s.confirm(ctx, ":1.42", token, "overwrite", testCredentials, digestOf("changed"), approve); err == nil || prompts != 1 {
		t.Fatalf("changed confirmation reused approval: %v; %d", err, prompts)
	}
	if err := s.commit(ctx, ":1.42", token, "overwrite", testCredentials, []byte("approved")); err == nil {
		t.Fatal("approval survived a changed confirmation")
	}
	token = testPrepare(t, s, dir, "report.docx", "overwrite")
	if err := s.confirm(ctx, ":1.42", token, "overwrite", testCredentials, digestOf("approved"), approve); err != nil {
		t.Fatal(err)
	}
	if err := s.commit(ctx, ":1.42", token, "overwrite", testCredentials, []byte("changed")); err == nil {
		t.Fatal("different content reached the target")
	}
	if err := s.commit(ctx, ":1.42", token, "overwrite", testCredentials, []byte("approved")); err == nil {
		t.Fatal("approval survived a mismatched commit")
	}
	assertFile(t, path, "before")
}

func TestManagedDirectoryIdentityMustMatchTheNamedPath(t *testing.T) {
	dir := t.TempDir()
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	if !SameDirectory(root, dir) || SameDirectory(root, t.TempDir()) {
		t.Fatal("directory identity did not distinguish the approved and replaced roots")
	}
}

func TestManagedPromptWrapsPathWithoutLosingBytes(t *testing.T) {
	path := "/var/lib/colink-fileops/1000/" + strings.Repeat("路径 file ", 20) + "-END.docx"
	wrapped := wrapPath(path)
	if !strings.Contains(wrapped, "\n") || strings.ReplaceAll(wrapped, "\n", "") != path {
		t.Fatalf("wrapped path does not preserve all bytes: %q", wrapped)
	}
	for _, line := range strings.Split(wrapped, "\n") {
		width := 0
		for _, char := range line {
			width++
			if char > 127 {
				width++
			}
		}
		if width > 30 {
			t.Errorf("prompt line is too wide: %q", line)
		}
	}
	if validName("report\n影响范围：只读.docx") || validName("report\u202e.docx") || validName("../report.docx") {
		t.Fatal("unsafe display or traversal name accepted")
	}
}

func TestManagedPromptShowsActionPathAndScope(t *testing.T) {
	path := DirectoryForUID(1000) + "/" + strings.Repeat("周报 2026", 12) + "-END.docx"
	for _, tc := range []struct {
		op, locale, action, scope string
	}{
		{"overwrite", "prompt_en", "Overwrite", "Replace only this file's contents."},
		{"delete", "prompt_en", "Delete", "Delete only this file."},
		{"overwrite", "prompt_zh", "覆盖", "仅替换这一个文件的内容。"},
		{"delete", "prompt_zh", "删除", "仅删除这一个文件。"},
	} {
		lines := strings.Split(promptDetails(tc.op, path)[tc.locale], "\n")
		if len(lines) < 5 || !strings.HasSuffix(lines[0], tc.action) || !strings.HasSuffix(lines[len(lines)-1], tc.scope) || strings.Join(lines[2:len(lines)-1], "") != path {
			t.Fatalf("%s/%s lost the action, path or scope: %q", tc.op, tc.locale, lines)
		}
	}
}
