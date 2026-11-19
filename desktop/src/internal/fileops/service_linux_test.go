package fileops

import (
	"bufio"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/godbus/dbus/v5"
)

type testPolkit struct {
	fail bool
}

func (p testPolkit) CheckAuthorization(_ struct {
	Kind    string
	Details map[string]dbus.Variant
}, _ string, _ map[string]string, flags uint32, _ string) (struct {
	Authorized, Challenge bool
	Details               map[string]string
}, *dbus.Error) {
	result := struct {
		Authorized, Challenge bool
		Details               map[string]string
	}{Details: map[string]string{}}
	if flags == 0 {
		result.Challenge = true
	} else if p.fail {
		return result, dbus.NewError("org.freedesktop.PolicyKit1.Error.Failed", nil)
	}
	return result, nil
}

func TestInteractiveDenialLeavesManagedFileUnchanged(t *testing.T) {
	daemon, err := exec.LookPath("dbus-daemon")
	if err != nil {
		t.Skip("dbus-daemon is unavailable")
	}
	cmd := exec.Command(daemon, "--session", "--nofork", "--print-address=1")
	output, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	scanner := bufio.NewScanner(output)
	if !scanner.Scan() {
		t.Fatal("session bus did not start", scanner.Err())
	}
	address := scanner.Text()
	authority, err := dbus.Connect(address)
	if err != nil {
		t.Fatal(err)
	}
	defer authority.Close()
	if _, err = authority.RequestName("org.freedesktop.PolicyKit1", dbus.NameFlagDoNotQueue); err != nil {
		t.Fatal(err)
	}
	client, err := dbus.Connect(address)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	svc := &service{conn: client}
	for _, fail := range []bool{false, true} {
		if err := authority.Export(testPolkit{fail: fail}, "/org/freedesktop/PolicyKit1/Authority", "org.freedesktop.PolicyKit1.Authority"); err != nil {
			t.Fatal(err)
		}
		dir := t.TempDir()
		path := filepath.Join(dir, "report.docx")
		if err := os.WriteFile(path, []byte("before"), 0600); err != nil {
			t.Fatal(err)
		}
		store := testStore()
		token := testPrepare(t, store, dir, "report.docx", "delete")
		err := store.confirm(context.Background(), ":1.42", token, "delete", testCredentials, nil, func(ctx context.Context, op, path string) (bool, error) {
			return svc.authorize(ctx, ":1.42", op, path)
		})
		if fail && (err == nil || errors.Is(err, ErrCancelled)) {
			t.Fatalf("service failure reported as cancellation: %v", err)
		}
		if !fail && !errors.Is(err, ErrCancelled) {
			t.Fatalf("interactive denial did not cancel: %v", err)
		}
		if err := store.commit(context.Background(), ":1.42", token, "delete", testCredentials, nil); err == nil {
			t.Fatal("denied operation succeeded")
		}
		assertFile(t, path, "before")
	}
}

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
