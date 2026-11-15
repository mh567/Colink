package main

import (
	"encoding/json"
	"testing"
)

func TestDispatchDesktopIPCGetSettingsStatus(t *testing.T) {
	app := &App{store: &settingsStore{cur: Settings{
		Locale:         LocaleZH,
		MinimizeToTray: true,
		ServerURL:      "https://colink.example.com",
	}}}

	data, err := app.dispatchDesktopIPC(desktopIPCMessage{Method: "GetSettingsStatus"})
	if err != nil {
		t.Fatalf("dispatch GetSettingsStatus: %v", err)
	}
	settings, ok := data.(Settings)
	if !ok {
		t.Fatalf("got %T, want Settings", data)
	}
	if settings.Locale != LocaleZH || settings.ServerURL != "https://colink.example.com" {
		t.Fatalf("unexpected settings: %+v", settings)
	}
}

func TestDispatchDesktopIPCSaveSettingsRejectsMissingArgs(t *testing.T) {
	app := &App{store: &settingsStore{cur: defaultSettings()}}
	if _, err := app.dispatchDesktopIPC(desktopIPCMessage{Method: "SaveSettings"}); err == nil {
		t.Fatal("expected an error for missing settings")
	}
}

func TestDispatchDesktopIPCRejectsUnknownMethod(t *testing.T) {
	app := &App{store: &settingsStore{cur: defaultSettings()}}
	if _, err := app.dispatchDesktopIPC(desktopIPCMessage{Method: "OpenEverything"}); err == nil {
		t.Fatal("expected an error for an unknown method")
	}
}

func TestDesktopIPCRequestCarriesJSONArgs(t *testing.T) {
	args := []json.RawMessage{json.RawMessage(`{"locale":"zh","serverUrl":""}`)}
	raw, err := json.Marshal(map[string]any{
		"id":     "7",
		"method": "SaveSettings",
		"args":   args,
	})
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	var msg desktopIPCMessage
	if err := json.Unmarshal(raw, &msg); err != nil {
		t.Fatalf("unmarshal request: %v", err)
	}
	if msg.ID != "7" || msg.Method != "SaveSettings" || len(msg.Args) != 1 {
		t.Fatalf("unexpected message: %+v", msg)
	}
}
