package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strings"

	"github.com/wailsapp/wails/v3/pkg/application"
)

const deviceConnectEventPrefix = "desktop:device-connect:"
const deviceRefreshEventPrefix = "desktop:device-refresh:"

type deviceEventListener struct{ client *deviceClient }

func (l *deviceEventListener) DispatchWailsEvent(event *application.CustomEvent) {
	if event == nil || l.client == nil {
		return
	}
	if event.Name == "desktop:device-disconnect" {
		l.client.disconnect()
		return
	}
	prefix := deviceConnectEventPrefix
	if strings.HasPrefix(event.Name, deviceRefreshEventPrefix) {
		prefix = deviceRefreshEventPrefix
	} else if !strings.HasPrefix(event.Name, deviceConnectEventPrefix) {
		return
	}
	raw, err := url.QueryUnescape(strings.TrimPrefix(event.Name, prefix))
	var body struct {
		AccessToken string `json:"access_token"`
		APIPath     string `json:"api_path"`
	}
	if err == nil {
		err = json.Unmarshal([]byte(raw), &body)
	}
	if err != nil {
		l.client.mu.Lock()
		defer l.client.mu.Unlock()
		if l.client.emit != nil {
			l.client.emit(deviceMessage{Type: "error", Code: "DEVICE_CLIENT_ERROR", Message: "invalid device connection request"})
		}
		return
	}
	if prefix == deviceRefreshEventPrefix {
		l.client.refresh(body.AccessToken)
	} else {
		l.client.connect(body.AccessToken, body.APIPath)
	}
}

func (a *App) emitDeviceStatus(message deviceMessage) {
	if a.window == nil {
		return
	}
	data, err := json.Marshal(message)
	if err != nil {
		return
	}
	a.window.ExecJS(fmt.Sprintf(`window.dispatchEvent(new CustomEvent("colink:device-status",{detail:%s}));`, data))
}
