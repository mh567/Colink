package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

type deviceMessage struct {
	Type     string `json:"type"`
	DeviceID string `json:"device_id,omitempty"`
	Status   string `json:"status,omitempty"`
	Online   bool   `json:"online"`
	Code     string `json:"code,omitempty"`
	Message  string `json:"message,omitempty"`
}

type deviceServerError struct{ message deviceMessage }

func (e deviceServerError) Error() string { return e.message.Code + ": " + e.message.Message }

var deviceKeyMu sync.Mutex

func loadDevicePrivateKey(path string) (ed25519.PrivateKey, error) {
	deviceKeyMu.Lock()
	defer deviceKeyMu.Unlock()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err == nil {
		_, key, err := ed25519.GenerateKey(rand.Reader)
		if err == nil {
			_, err = file.Write(key)
		}
		closeErr := file.Close()
		if err != nil {
			_ = os.Remove(path)
			return nil, err
		}
		if closeErr != nil {
			return nil, closeErr
		}
		return key, nil
	}
	if !errors.Is(err, os.ErrExist) {
		return nil, err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("device key must be a regular file")
	}
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		return nil, errors.New("device key permissions must be 0600")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(data) != ed25519.PrivateKeySize || !bytes.Equal(data, ed25519.NewKeyFromSeed(data[:ed25519.SeedSize])) {
		return nil, errors.New("device key is corrupt; it was not replaced")
	}
	return ed25519.PrivateKey(data), nil
}

type deviceClient struct {
	mu         sync.Mutex
	base       string
	generation uint64
	cancel     context.CancelFunc
	emit       func(deviceMessage)
}

func (c *deviceClient) setBase(raw string) error {
	base, err := normalizeServerURL(raw)
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cancel != nil {
		c.cancel()
		c.cancel = nil
	}
	c.generation++
	c.base = base
	return err
}

func (c *deviceClient) disconnect() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cancel != nil {
		c.cancel()
		c.cancel = nil
	}
	c.generation++
	if c.emit != nil {
		c.emit(deviceMessage{Type: "disconnected"})
	}
}

func (c *deviceClient) publish(generation uint64, message deviceMessage) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.generation == generation && c.emit != nil {
		c.emit(message)
	}
}

func (c *deviceClient) connect(token string) {
	c.mu.Lock()
	if c.cancel != nil {
		c.cancel()
	}
	c.generation++
	generation, base := c.generation, c.base
	ctx, cancel := context.WithCancel(context.Background())
	c.cancel = cancel
	c.mu.Unlock()
	go func() {
		defer cancel()
		var err error
		if strings.TrimSpace(token) == "" || base == "" {
			err = errors.New("sign in to the configured server before connecting this device")
		} else {
			var key ed25519.PrivateKey
			key, err = loadDevicePrivateKey(filepath.Join(octopHome(), "device-key"))
			if err == nil {
				err = runDeviceSession(ctx, base, token, key, func(m deviceMessage) { c.publish(generation, m) })
			}
		}
		if err != nil && ctx.Err() == nil {
			var remote deviceServerError
			if errors.As(err, &remote) {
				c.publish(generation, remote.message)
			} else {
				c.publish(generation, deviceMessage{Type: "error", Code: "DEVICE_CLIENT_ERROR", Message: err.Error()})
			}
		}
	}()
}

func runDeviceSession(ctx context.Context, base, token string, key ed25519.PrivateKey, emit func(deviceMessage)) error {
	normalized, err := normalizeServerURL(base)
	if err != nil {
		return err
	}
	u, err := url.Parse(normalized)
	if err != nil {
		return err
	}
	u.Path, u.RawPath = "/api/devices/connect", ""
	if u.Scheme == "https" {
		u.Scheme = "wss"
	} else {
		u.Scheme = "ws"
	}
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error {
		return errors.New("device connection redirects are not allowed")
	}}
	dialCtx, cancelDial := context.WithTimeout(ctx, 10*time.Second)
	conn, _, err := websocket.Dial(dialCtx, u.String(), &websocket.DialOptions{HTTPClient: client})
	cancelDial()
	if err != nil {
		return fmt.Errorf("connect device: %w", err)
	}
	defer conn.CloseNow()
	conn.SetReadLimit(64 * 1024)
	name, err := os.Hostname()
	if err != nil {
		return err
	}
	if len(key) != ed25519.PrivateKeySize {
		return errors.New("invalid native device key")
	}
	handshakeCtx, cancelHandshake := context.WithTimeout(ctx, 10*time.Second)
	defer cancelHandshake()
	if err := wsjson.Write(handshakeCtx, conn, map[string]string{
		"type": "hello", "access_token": token,
		"public_key": base64.StdEncoding.EncodeToString(key.Public().(ed25519.PublicKey)),
		"name":       name, "platform": runtime.GOOS + "/" + runtime.GOARCH,
	}); err != nil {
		return err
	}
	var challenge deviceMessage
	if err := wsjson.Read(handshakeCtx, conn, &challenge); err != nil {
		return err
	}
	if challenge.Type == "error" {
		return deviceServerError{challenge}
	}
	parts := strings.Split(challenge.Message, ":")
	if challenge.Type != "challenge" || len(parts) != 3 || parts[0] != "colink-device-v1" || parts[2] == "" {
		return errors.New("invalid device authentication challenge")
	}
	userID, err := strconv.Atoi(parts[1])
	if err != nil || userID <= 0 {
		return errors.New("invalid device authentication user")
	}
	sig := ed25519.Sign(key, []byte(challenge.Message))
	if err := wsjson.Write(handshakeCtx, conn, map[string]string{"type": "proof", "signature": base64.StdEncoding.EncodeToString(sig)}); err != nil {
		return err
	}
	sessionCtx, stop := context.WithCancel(ctx)
	defer stop()
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-sessionCtx.Done():
				return
			case <-ticker.C:
				if wsjson.Write(sessionCtx, conn, map[string]string{"type": "heartbeat"}) != nil {
					stop()
					return
				}
			}
		}
	}()
	for {
		readCtx, cancelRead := context.WithTimeout(sessionCtx, 15*time.Second)
		var message deviceMessage
		err := wsjson.Read(readCtx, conn, &message)
		cancelRead()
		if err != nil {
			return err
		}
		if message.Type == "error" {
			return deviceServerError{message}
		}
		if message.Type != "status" || message.DeviceID == "" || (message.Status != "pending" && message.Status != "approved" && message.Status != "revoked") {
			return errors.New("invalid device status response")
		}
		emit(message)
		if message.Status == "revoked" {
			return nil
		}
	}
}
