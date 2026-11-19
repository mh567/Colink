//go:build linux

package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"octop.desktop/internal/fileops"
)

func liveManagedLogin(server string, body []byte, transport http.RoundTripper) (string, error) {
	server, err := fileops.NormalizeServerURL(server)
	if err != nil {
		return "", err
	}
	client := http.Client{
		Transport:     transport,
		Timeout:       10 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	response, err := client.Post(server+"/api/auth/login", "application/json", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("test login failed: status %d", response.StatusCode)
	}
	var login struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.NewDecoder(response.Body).Decode(&login); err != nil || login.AccessToken == "" {
		return "", errors.New("test login did not return a token")
	}
	return login.AccessToken, nil
}

func useLiveManagedDeviceKey(t *testing.T, keyPath string) error {
	t.Helper()
	if !filepath.IsAbs(keyPath) {
		return errors.New("provide an absolute path to a separately approved test device key")
	}
	keyInfo, err := os.Lstat(keyPath)
	if err != nil {
		return err
	}
	if !keyInfo.Mode().IsRegular() || keyInfo.Mode().Perm() != 0600 || keyInfo.Size() != ed25519.PrivateKeySize {
		return errors.New("test device key must be a regular 0600 Ed25519 key file")
	}
	key, err := os.ReadFile(keyPath)
	if err != nil {
		return err
	}
	if len(key) != ed25519.PrivateKeySize || !bytes.Equal(key, ed25519.NewKeyFromSeed(key[:ed25519.SeedSize])) {
		return errors.New("invalid test device key")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	for _, dir := range []string{octopHome(), filepath.Join(home, ".octop")} {
		desktopKey, err := os.ReadFile(filepath.Join(dir, "device-key"))
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return err
		}
		if bytes.Equal(key, desktopKey) {
			return errors.New("the test device key must not be the current desktop key")
		}
	}
	isolatedHome := t.TempDir()
	if err := os.WriteFile(filepath.Join(isolatedHome, "device-key"), key, 0600); err != nil {
		return err
	}
	t.Setenv("OCTOP_HOME", isolatedHome)
	return nil
}

// Run only in a dedicated Linux test session with an installed file service.
func TestLiveManagedDirectoryReturnsAbsolutePath(t *testing.T) {
	server := os.Getenv("COLINK_FILEOPS_TEST_SERVER")
	if server == "" {
		t.Skip("set COLINK_FILEOPS_TEST_SERVER to run the managed service integration test")
	}
	server, err := fileops.NormalizeServerURL(server)
	if err != nil {
		t.Fatal(err)
	}
	endpoint, err := url.Parse(server)
	if err != nil {
		t.Fatal(err)
	}
	if err := useLiveManagedDeviceKey(t, os.Getenv("COLINK_FILEOPS_TEST_DEVICE_KEY_FILE")); err != nil {
		t.Fatal(err)
	}
	password, err := os.ReadFile(os.Getenv("COLINK_FILEOPS_TEST_PASSWORD_FILE"))
	if err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(map[string]string{
		"username": os.Getenv("COLINK_FILEOPS_TEST_USERNAME"),
		"password": strings.TrimSpace(string(password)),
	})
	if err != nil {
		t.Fatal(err)
	}
	token, err := liveManagedLogin(server, body, nil)
	if err != nil {
		t.Fatal(err)
	}
	events := make(chan deviceMessage, 1)
	client := &deviceClient{emit: func(message deviceMessage) {
		select {
		case events <- message:
		default:
		}
	}}
	if err := client.setBase(server); err != nil {
		t.Fatal(err)
	}
	client.connect(token, endpoint.Path+"/api/devices/connect")
	defer client.disconnect()
	select {
	case message := <-events:
		if message.Type != "status" || message.Status != "approved" || !message.Online {
			t.Fatalf("device not approved and online: %+v", message)
		}
	case <-time.After(20 * time.Second):
		t.Fatal("device connection timed out")
	}
	session, err := client.docxSession()
	if err != nil {
		t.Fatal(err)
	}
	path, err := linuxManagedDirectory(session)
	if err != nil {
		t.Fatalf("managed directory unavailable: %v", err)
	}
	expected := filepath.Join("/var/lib/colink-fileops", strconv.Itoa(os.Getuid()))
	if path != expected {
		t.Fatalf("got %q, want %q", path, expected)
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	if err := managedRootIdentity(root); err != nil {
		t.Fatal(err)
	}
}
