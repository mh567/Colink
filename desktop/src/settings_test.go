package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestServerURLSettingsPersistAndOverrideEnvironment(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	store := &settingsStore{cur: defaultSettings()}
	next := store.get()
	next.ServerURL = "https://company.example/"
	if err := store.save(next); err != nil {
		t.Fatal(err)
	}
	loaded := loadSettings()
	got, err := configuredServerURL(loaded, "http://old.example")
	if err != nil || got != "https://company.example/" {
		t.Fatalf("url=%q err=%v", got, err)
	}
	data, err := json.Marshal(loaded)
	if err != nil {
		t.Fatal(err)
	}
	var stored map[string]any
	if err := json.Unmarshal(data, &stored); err != nil || stored["serverUrl"] != "https://company.example/" {
		t.Fatalf("missing serverUrl setting: %s, %v", data, err)
	}
	got, err = configuredServerURL(defaultSettings(), "http://legacy.example")
	if err != nil || got != "http://legacy.example" {
		t.Fatalf("legacy url=%q err=%v", got, err)
	}
}

func TestServerURLRejectsCredentialsAndNonOrigins(t *testing.T) {
	for _, raw := range []string{"file:///tmp/app", "javascript:alert(1)", "https://user:pass@example.com", "https://example.com?token=x", "https://example.com#token", "https://example.com?", "//example.com", "https:///missing"} {
		if _, err := normalizeServerURL(raw); err == nil {
			t.Errorf("accepted unsafe URL %q", raw)
		}
	}
	for _, raw := range []string{"http://127.0.0.1:8088", "https://company.example/dashboard"} {
		if _, err := normalizeServerURL(raw); err != nil {
			t.Errorf("rejected URL %q: %v", raw, err)
		}
	}
}

func TestLoadSettingsMigratesLegacyPreventSleepMac(t *testing.T) {
	temp := t.TempDir()
	t.Setenv("OCTOP_HOME", temp)
	if err := os.WriteFile(
		filepath.Join(temp, "desktop-settings.json"),
		[]byte(`{"preventSleepMac":true}`),
		0o644,
	); err != nil {
		t.Fatal(err)
	}

	if !loadSettings().PreventSleep {
		t.Fatal("legacy preventSleepMac should migrate to preventSleep")
	}
}
