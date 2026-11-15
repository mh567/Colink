package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/wailsapp/wails/v3/pkg/application"
)

// desktopIPCPrefix marks shell RPCs sent through the native IPC channel. The
// bundled pages use this instead of the Wails HTTP runtime transport so the
// shell also works where the WebKit build cannot deliver request bodies.
const desktopIPCPrefix = "octop:ipc:"

type desktopIPCMessage struct {
	ID     string            `json:"id"`
	Method string            `json:"method"`
	Args   []json.RawMessage `json:"args"`
}

func (a *App) handleRawMessage(window application.Window, message string, _ *application.OriginInfo) {
	if !strings.HasPrefix(message, desktopIPCPrefix) {
		return
	}

	var msg desktopIPCMessage
	if err := json.Unmarshal([]byte(strings.TrimPrefix(message, desktopIPCPrefix)), &msg); err != nil {
		return
	}
	if msg.ID == "" {
		return
	}

	data, err := a.dispatchDesktopIPC(msg)
	reply := map[string]any{"ok": err == nil}
	if err != nil {
		reply["error"] = err.Error()
	} else if data != nil {
		reply["data"] = data
	}
	payload, err := json.Marshal(reply)
	if err != nil {
		return
	}
	window.ExecJS(fmt.Sprintf("window.__octopDesktopReply(%s,%s);", jsonString(msg.ID), payload))
}

func (a *App) dispatchDesktopIPC(msg desktopIPCMessage) (any, error) {
	switch msg.Method {
	case "GetSettingsStatus":
		settings, err := a.GetSettingsStatus()
		return settings, err
	case "SaveSettings":
		if len(msg.Args) == 0 {
			return nil, errors.New("missing settings")
		}
		var next Settings
		if err := json.Unmarshal(msg.Args[0], &next); err != nil {
			return nil, err
		}
		saved, err := a.SaveSettings(next)
		return saved, err
	case "ShowMain":
		a.ShowMain()
		return nil, nil
	case "HideSettings":
		a.HideSettings()
		return nil, nil
	case "Quit":
		a.Quit()
		return nil, nil
	default:
		return nil, fmt.Errorf("unknown desktop method %q", msg.Method)
	}
}
