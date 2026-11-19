//go:build linux

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

// This test needs an installed file service and an approved test device.
func TestLiveManagedDirectoryReturnsAbsolutePath(t *testing.T) {
	server := os.Getenv("COLINK_FILEOPS_TEST_SERVER")
	if server == "" {
		t.Skip("set COLINK_FILEOPS_TEST_SERVER to run the managed service integration test")
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
	response, err := http.Post(server+"/api/auth/login", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	data, err := io.ReadAll(response.Body)
	if err != nil || response.StatusCode != http.StatusOK {
		t.Fatalf("test login failed: status %d, error %v", response.StatusCode, err)
	}
	var login struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.Unmarshal(data, &login); err != nil || login.AccessToken == "" {
		t.Fatal("test login did not return a token")
	}
	events := make(chan deviceMessage, 8)
	client := &deviceClient{emit: func(message deviceMessage) { events <- message }}
	if err := client.setBase(server); err != nil {
		t.Fatal(err)
	}
	client.connect(login.AccessToken, "/api/devices/connect")
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
	name := "colink-acceptance-" + strconv.FormatInt(time.Now().UnixNano(), 10) + ".docx"
	if configured := os.Getenv("COLINK_FILEOPS_TEST_NAME"); configured != "" {
		name = configured
	}
	content := []byte("Colink isolated acceptance fixture")
	if fixture := os.Getenv("COLINK_FILEOPS_TEST_FIXTURE"); fixture != "" {
		content, err = os.ReadFile(fixture)
		if err != nil {
			t.Fatal(err)
		}
	}
	handled, err := createLocalOutput(session, root, name, content)
	if err != nil || !handled {
		t.Fatalf("create managed file: handled=%t, error=%v", handled, err)
	}
	got, err := os.ReadFile(filepath.Join(path, name))
	if err != nil || !bytes.Equal(got, content) {
		t.Fatalf("read managed file: error=%v", err)
	}
	t.Logf("created isolated test file %s", filepath.Join(path, name))
	if targetName := os.Getenv("COLINK_FILEOPS_TEST_PREPARE"); targetName != "" {
		target, err := prepareDestructiveTarget(session, root, targetName, "delete")
		if err != nil {
			t.Fatalf("prepare managed delete: %v", err)
		}
		defer target.close()
		if os.Getenv("COLINK_FILEOPS_TEST_CONFIRM") != "" {
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			if err := target.confirm(ctx, nil, nil); err != nil {
				t.Fatalf("confirm managed delete: %v", err)
			}
		}
	}
}
