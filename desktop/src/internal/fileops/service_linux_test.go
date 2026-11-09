package fileops

import (
	"os"
	"path/filepath"
	"testing"
)

func TestManagedFileAccessIsReadOnlyAndRejectsAliases(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	uid := uint32(65534)
	if uid == uint32(os.Geteuid()) {
		uid--
	}
	if err = protectManagedFile(f, uid); err != nil {
		t.Fatal(err)
	}
	if err = verifyManagedFile(f, uid); err != nil {
		t.Fatal(err)
	}
	if err = verifyManagedFile(f, uid-1); err == nil {
		t.Fatal("another reader identity passed the ACL check")
	}
	if err = os.Link(path, filepath.Join(dir, "alias.docx")); err != nil {
		t.Fatal(err)
	}
	if err = verifyManagedFile(f, uid); err == nil {
		t.Fatal("a multiply linked target passed the ACL check")
	}
}
