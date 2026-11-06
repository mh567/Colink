package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

func TestConcurrentDeviceConnectionsKeepOneIdentity(t *testing.T) {
	path := filepath.Join(t.TempDir(), "device-key")
	start := make(chan struct{})
	errors := make(chan error, 40)
	keys := make(chan ed25519.PrivateKey, 40)
	var workers sync.WaitGroup
	for range 40 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			<-start
			key, err := loadDevicePrivateKey(path)
			if err != nil {
				errors <- err
			} else {
				keys <- key
			}
		}()
	}
	close(start)
	workers.Wait()
	close(errors)
	close(keys)
	for err := range errors {
		t.Errorf("concurrent connection saw a partial identity: %v", err)
	}
	var first ed25519.PrivateKey
	for key := range keys {
		if first == nil {
			first = key
		} else if !bytes.Equal(first, key) {
			t.Fatal("concurrent connections created different identities")
		}
	}
}

func TestDeviceIdentityPersistsWithoutExposingPrivateKey(t *testing.T) {
	path := filepath.Join(t.TempDir(), "device-key")
	first, err := loadDevicePrivateKey(path)
	if err != nil {
		t.Fatal(err)
	}
	second, err := loadDevicePrivateKey(path)
	if err != nil || !bytes.Equal(first, second) {
		t.Fatalf("identity changed after restart: %v", err)
	}
	if len(first) != ed25519.PrivateKeySize {
		t.Fatal("invalid private key")
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("key permissions = %o", info.Mode().Perm())
	}
}

func TestDeviceIdentityRejectsCorruptFileWithoutReplacingIt(t *testing.T) {
	_, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	key[len(key)-1] ^= 1
	for _, bad := range [][]byte{[]byte("broken"), key} {
		path := filepath.Join(t.TempDir(), "device-key")
		if err := os.WriteFile(path, bad, 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := loadDevicePrivateKey(path); err == nil {
			t.Fatal("corrupt identity was accepted")
		}
		got, err := os.ReadFile(path)
		if err != nil || !bytes.Equal(got, bad) {
			t.Fatalf("corrupt identity was silently replaced: %v", err)
		}
	}
}

func TestDeviceSessionSignsServerChallengeAndSendsHeartbeat(t *testing.T) {
	_, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	heartbeat := make(chan error, 1)
	closed := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(closed)
		if r.URL.Path != "/api/devices/connect" || r.URL.RawQuery != "" || r.Header.Get("Authorization") != "" {
			heartbeat <- errors.New("credentials escaped the hello frame")
			return
		}
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			heartbeat <- err
			return
		}
		defer conn.CloseNow()
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		var hello struct {
			Type        string `json:"type"`
			AccessToken string `json:"access_token"`
			PublicKey   string `json:"public_key"`
			Name        string `json:"name"`
			Platform    string `json:"platform"`
		}
		if err := wsjson.Read(ctx, conn, &hello); err != nil {
			heartbeat <- err
			return
		}
		if hello.Type != "hello" || hello.AccessToken != "employee-token" || hello.Name == "" || hello.Platform != runtime.GOOS+"/"+runtime.GOARCH {
			heartbeat <- errors.New("wrong hello identity")
			return
		}
		pub, err := base64.StdEncoding.DecodeString(hello.PublicKey)
		if err != nil || !bytes.Equal(pub, key.Public().(ed25519.PublicKey)) {
			heartbeat <- errors.New("public key did not match native identity")
			return
		}
		message := "colink-device-v1:7:test-nonce"
		if err := wsjson.Write(ctx, conn, map[string]string{"type": "challenge", "message": message}); err != nil {
			heartbeat <- err
			return
		}
		var proof struct {
			Type      string `json:"type"`
			Signature string `json:"signature"`
		}
		if err := wsjson.Read(ctx, conn, &proof); err != nil {
			heartbeat <- err
			return
		}
		sig, err := base64.StdEncoding.DecodeString(proof.Signature)
		if err != nil || proof.Type != "proof" || !ed25519.Verify(pub, []byte(message), sig) {
			heartbeat <- errors.New("invalid native signature")
			return
		}
		if err := wsjson.Write(ctx, conn, map[string]any{"type": "status", "device_id": "device-1", "status": "approved", "online": true}); err != nil {
			heartbeat <- err
			return
		}
		var beat map[string]string
		err = wsjson.Read(ctx, conn, &beat)
		if err == nil && beat["type"] != "heartbeat" {
			err = errors.New("missing native heartbeat")
		}
		heartbeat <- err
		_, _, _ = conn.Read(ctx)
	}))
	defer srv.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	statuses := make(chan deviceMessage, 2)
	go func() {
		done <- runDeviceSession(ctx, srv.URL+"/dashboard", "employee-token", key, func(m deviceMessage) { statuses <- m })
	}()
	select {
	case state := <-statuses:
		if state.Type != "status" || state.DeviceID != "device-1" || !state.Online {
			t.Fatalf("wrong status: %+v", state)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("native session did not report status")
	}
	select {
	case err := <-heartbeat:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(8 * time.Second):
		t.Fatal("native client did not send heartbeat")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("logout did not cancel native connection")
	}
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("logout left server connection open")
	}
}

func TestDeviceSessionRejectsRedirectWithoutLeakingToken(t *testing.T) {
	targetCalls := make(chan struct{}, 1)
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { targetCalls <- struct{}{} }))
	defer target.Close()
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer source.Close()
	_, key, _ := ed25519.GenerateKey(rand.Reader)
	err := runDeviceSession(context.Background(), source.URL, "secret-access-token", key, func(deviceMessage) {})
	if err == nil || strings.Contains(err.Error(), "secret-access-token") {
		t.Fatalf("redirect result leaked credentials: %v", err)
	}
	select {
	case <-targetCalls:
		t.Fatal("native connection followed a cross-origin redirect")
	default:
	}
}

func TestDeviceSessionReportsServerErrorAndRefusesArbitraryChallenge(t *testing.T) {
	for _, message := range []map[string]string{
		{"type": "error", "code": "FORBIDDEN", "message": "not your device"},
		{"type": "challenge", "message": "sign arbitrary content"},
	} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			conn, err := websocket.Accept(w, r, nil)
			if err != nil {
				return
			}
			defer conn.CloseNow()
			ctx, cancel := context.WithTimeout(r.Context(), time.Second)
			defer cancel()
			var hello map[string]any
			if wsjson.Read(ctx, conn, &hello) != nil {
				return
			}
			_ = wsjson.Write(ctx, conn, message)
			var proof map[string]any
			if err := wsjson.Read(ctx, conn, &proof); err == nil {
				t.Error("client signed a rejected handshake")
			}
		}))
		_, key, _ := ed25519.GenerateKey(rand.Reader)
		err := runDeviceSession(context.Background(), srv.URL, "token", key, func(deviceMessage) {})
		srv.Close()
		if err == nil {
			t.Fatal("rejected handshake did not fail")
		}
		if message["type"] == "error" && !strings.Contains(err.Error(), "not your device") {
			t.Fatalf("server error was hidden: %v", err)
		}
	}
}

func TestDeviceClientIgnoresOldGenerationStatus(t *testing.T) {
	got := make(chan deviceMessage, 2)
	client := &deviceClient{emit: func(m deviceMessage) { got <- m }}
	if err := client.setBase("https://company.example"); err != nil {
		t.Fatal(err)
	}
	old := client.generation
	client.disconnect()
	if state := <-got; state.Type != "disconnected" || state.Online {
		t.Fatalf("logout did not report disconnected: %+v", state)
	}
	client.publish(old, deviceMessage{Type: "status", Status: "approved", Online: true})
	select {
	case state := <-got:
		t.Fatalf("old connection replaced disconnected state: %+v", state)
	default:
	}
}
