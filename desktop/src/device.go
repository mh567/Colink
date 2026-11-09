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
	Type         string `json:"type"`
	DeviceID     string `json:"device_id,omitempty"`
	Status       string `json:"status,omitempty"`
	Online       bool   `json:"online"`
	Code         string `json:"code,omitempty"`
	Message      string `json:"message,omitempty"`
	SessionToken string `json:"session_token,omitempty"`
	UserID       int    `json:"-"`
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
	renewals   chan string
	emit       func(deviceMessage)
	docx       docxSession
}

func (c *deviceClient) setBase(raw string) error {
	base, err := normalizeServerURL(raw)
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cancel != nil {
		c.cancel()
		c.cancel = nil
	}
	c.docx = docxSession{}
	c.generation++
	c.renewals = nil
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
	c.docx = docxSession{}
	c.generation++
	c.renewals = nil
	if c.emit != nil {
		c.emit(deviceMessage{Type: "disconnected"})
	}
}

func (c *deviceClient) publish(generation uint64, message deviceMessage) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.generation == generation {
		if message.Type == "status" && message.Status == "approved" && message.Online {
			c.docx.DeviceID, c.docx.Secret, c.docx.UserID = message.DeviceID, message.SessionToken, message.UserID
		} else {
			c.docx.Secret = ""
		}
		message.SessionToken = ""
		message.UserID = 0
		if c.emit != nil {
			c.emit(message)
		}
	}
}

func (c *deviceClient) refresh(token string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.renewals == nil {
		return
	}
	select {
	case <-c.renewals:
	default:
	}
	c.renewals <- token
	c.docx.Token = token
}

func (c *deviceClient) connect(token, apiPath string) {
	c.mu.Lock()
	if c.cancel != nil {
		c.cancel()
	}
	c.docx = docxSession{}
	c.generation++
	generation, base := c.generation, c.base
	ctx, cancel := context.WithCancel(context.Background())
	c.cancel = cancel
	c.docx = docxSession{Base: base, APIPath: apiPath, Token: token, Generation: generation, Context: ctx}
	renewals := make(chan string, 1)
	c.renewals = renewals
	c.mu.Unlock()
	go func() {
		defer func() {
			cancel()
			c.mu.Lock()
			defer c.mu.Unlock()
			if c.generation == generation {
				c.cancel = nil
				c.renewals = nil
				c.docx = docxSession{}
			}
		}()
		var err error
		if strings.TrimSpace(token) == "" || base == "" {
			err = errors.New("sign in to the configured server before connecting this device")
		} else {
			var key ed25519.PrivateKey
			key, err = loadDevicePrivateKey(filepath.Join(octopHome(), "device-key"))
			if err == nil {
				err = runDeviceSession(ctx, base, token, apiPath, key, renewals, func(m deviceMessage) { c.publish(generation, m) })
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

func runDeviceSession(ctx context.Context, base, token, apiPath string, key ed25519.PrivateKey, renewals <-chan string, emit func(deviceMessage)) error {
	normalized, err := normalizeServerURL(base)
	if err != nil {
		return err
	}
	u, err := url.Parse(normalized)
	if err != nil {
		return err
	}
	if apiPath == "" {
		apiPath = "/api/devices/connect"
	}
	endpoint, err := url.ParseRequestURI(apiPath)
	if err != nil || endpoint.IsAbs() || endpoint.Host != "" || endpoint.RawQuery != "" || endpoint.ForceQuery || !strings.HasPrefix(endpoint.Path, "/") || strings.HasPrefix(endpoint.Path, "//") || !strings.HasSuffix(endpoint.Path, "/api/devices/connect") {
		return errors.New("invalid device API path")
	}
	// The dashboard knows its deployment prefix; the native client keeps the configured origin.
	u.Path, u.RawPath = endpoint.Path, endpoint.RawPath
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
			case token = <-renewals:
			case <-ticker.C:
			}
			select {
			case token = <-renewals:
			default:
			}
			if wsjson.Write(sessionCtx, conn, map[string]string{"type": "heartbeat", "access_token": token}) != nil {
				stop()
				return
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
		message.UserID = userID
		emit(message)
		if message.Status == "revoked" {
			return nil
		}
	}
}

func (c *deviceClient) docxSession() (docxSession, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.docx.Secret == "" || c.docx.Context == nil || c.docx.Context.Err() != nil {
		return docxSession{}, errors.New("请先接入已获批准的本人设备")
	}
	snapshot := c.docx
	snapshot.Refresh = c.docxSession
	return snapshot, nil
}
func (c *deviceClient) currentDOCX(s docxSession) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.currentDOCXLocked(s)
}

func (c *deviceClient) currentDOCXLocked(s docxSession) error {
	current := c.docx
	if current.Secret == "" || current.Context == nil || current.Context.Err() != nil {
		return errors.New("请先接入已获批准的本人设备")
	}
	if current.Generation != s.Generation || current.scope() != s.scope() || current.Secret != s.Secret {
		return errors.New("设备会话已改变，任务已停止")
	}
	return nil
}

func (c *deviceClient) applyDOCX(s docxSession, apply func() error) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := c.currentDOCXLocked(s); err != nil {
		return err
	}
	return apply()
}
