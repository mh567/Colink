package main

import (
	"strings"
	"testing"

	"github.com/wailsapp/wails/v3/pkg/application"
)

func TestBrowserBridgeChecksDashboardOrigin(t *testing.T) {
	const base = "https://COLINK.test:443/office"
	for _, test := range []struct {
		name     string
		platform string
		origin   *application.OriginInfo
		allowed  bool
	}{
		{"dashboard through proxy", "windows", &application.OriginInfo{Origin: "https://colink.test/office/devices", TopOrigin: "https://colink.test"}, true},
		{"different scheme", "windows", &application.OriginInfo{Origin: "http://colink.test/office"}, false},
		{"different port", "windows", &application.OriginInfo{Origin: "https://colink.test:444/office"}, false},
		{"different site", "windows", &application.OriginInfo{Origin: "https://colink.test.example/office"}, false},
		{"untrusted frame", "windows", &application.OriginInfo{Origin: "https://example.test/embed", TopOrigin: "https://colink.test"}, false},
		{"untrusted top page", "windows", &application.OriginInfo{Origin: "https://colink.test", TopOrigin: "https://example.test"}, false},
		{"mac dashboard", "darwin", &application.OriginInfo{Origin: "https://colink.test/office", IsMainFrame: true}, true},
		{"mac embedded frame", "darwin", &application.OriginInfo{Origin: "https://colink.test/office"}, false},
		{"linux reports the current page", "linux", &application.OriginInfo{Origin: "https://colink.test:443/office"}, true},
		{"no origin", "linux", nil, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, allowed := browserMessageOrigin(base, test.origin, test.platform); allowed != test.allowed {
				t.Fatalf("allowed = %v, want %v", allowed, test.allowed)
			}
		})
	}
}

func TestBrowserBridgeCapabilityBelongsToCurrentNativeSession(t *testing.T) {
	a := &App{browserBridgeKey: [32]byte{1, 2, 3}}
	session := docxSession{Base: "https://colink.test", DeviceID: "laptop", UserID: 1, Secret: "live-session"}
	capability := a.browserCapability(session)
	if len(capability) != 64 || !a.hasBrowserCapability(session, capability) {
		t.Fatal("current device could not use its capability")
	}
	for _, value := range []string{"", strings.Repeat("x", 64)} {
		if a.hasBrowserCapability(session, value) {
			t.Fatal("invalid capability allowed an operation")
		}
	}
	changed := session
	changed.Generation++
	if a.hasBrowserCapability(changed, capability) {
		t.Fatal("reconnected device accepted its previous capability")
	}
	changed = session
	changed.Secret = "rotated"
	if a.hasBrowserCapability(changed, capability) {
		t.Fatal("new native session accepted its previous capability")
	}
	changed = session
	changed.UserID++
	if a.hasBrowserCapability(changed, capability) {
		t.Fatal("different employee accepted another employee's capability")
	}
}
