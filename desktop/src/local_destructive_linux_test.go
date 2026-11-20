package main

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
)

func TestLiveManagedLoginRejectsPlainHTTP(t *testing.T) {
	var received atomic.Bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		received.Store(true)
		w.WriteHeader(http.StatusBadRequest)
	}))
	defer server.Close()

	if _, err := liveManagedLogin(server.URL, []byte("{}"), nil); err == nil || received.Load() {
		t.Fatalf("HTTP login was not rejected before sending: %v", err)
	}
}

func TestLiveManagedLoginRejectsRedirect(t *testing.T) {
	var attempted, received atomic.Bool
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/office/api/auth/login":
			attempted.Store(true)
			http.Redirect(w, r, "/other", http.StatusTemporaryRedirect)
		case "/other":
			received.Store(true)
		default:
			t.Errorf("unexpected request path: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	if _, err := liveManagedLogin(server.URL+"/office", []byte("{}"), server.Client().Transport); err == nil || !attempted.Load() || received.Load() {
		t.Fatalf("expected login at the configured HTTPS path and no redirect: %v", err)
	}
}

func TestLiveManagedDeviceKeyIsIsolated(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	desktop := filepath.Join(t.TempDir(), "desktop")
	t.Setenv("OCTOP_HOME", desktop)
	for _, dir := range []string{desktop, filepath.Join(home, ".octop")} {
		keyPath := filepath.Join(dir, "device-key")
		if _, err := loadDevicePrivateKey(keyPath); err != nil {
			t.Fatal(err)
		}
		if err := useLiveManagedDeviceKey(t, keyPath); err == nil {
			t.Fatalf("accepted regular desktop key %s", keyPath)
		}
	}
	if err := useLiveManagedDeviceKey(t, ""); err == nil {
		t.Fatal("allowed implicit use of a regular desktop identity")
	}

	fixture := filepath.Join(t.TempDir(), "device-key")
	want, err := loadDevicePrivateKey(fixture)
	if err != nil {
		t.Fatal(err)
	}
	if err := useLiveManagedDeviceKey(t, fixture); err != nil {
		t.Fatal(err)
	}
	copyPath := filepath.Join(octopHome(), "device-key")
	if copyPath == fixture || octopHome() == desktop {
		t.Fatal("did not isolate the fixture key")
	}
	got, err := os.ReadFile(copyPath)
	if err != nil || !bytes.Equal(got, want) {
		t.Fatalf("isolated key differs from the approved fixture: %v", err)
	}
}

func TestLiveManagedDeviceKeyRejectsCopyOfDesktopKey(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	key, err := loadDevicePrivateKey(filepath.Join(octopHome(), "device-key"))
	if err != nil {
		t.Fatal(err)
	}
	copyPath := filepath.Join(t.TempDir(), "device-key")
	if err := os.WriteFile(copyPath, key, 0600); err != nil {
		t.Fatal(err)
	}
	if err := useLiveManagedDeviceKey(t, copyPath); err == nil {
		t.Fatal("accepted the regular desktop identity from another path")
	}
}
