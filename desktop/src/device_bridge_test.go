package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"github.com/wailsapp/wails/v3/pkg/application"
)

func TestDeviceEventConnectsOnlyConfiguredOriginAndShutdownDisconnects(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	helloReceived := make(chan string, 1)
	closed := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(closed)
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.CloseNow()
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		var hello map[string]string
		if err := wsjson.Read(ctx, conn, &hello); err != nil {
			return
		}
		helloReceived <- hello["access_token"]
		_, _, _ = conn.Read(ctx)
	}))
	defer srv.Close()
	client := &deviceClient{}
	if err := client.setBase(srv.URL); err != nil {
		t.Fatal(err)
	}
	listener := &deviceEventListener{client: client}
	listener.DispatchWailsEvent(&application.CustomEvent{Name: deviceConnectEventPrefix + url.QueryEscape(`{"access_token":"native+token","url":"https://attacker.invalid"}`)})
	select {
	case token := <-helloReceived:
		if token != "native+token" {
			t.Fatalf("wrong token %q", token)
		}
	case <-time.After(time.Second):
		t.Fatal("event did not connect to configured server")
	}
	app := &App{sleep: &sleepGuard{}, device: client}
	if err := app.ServiceShutdown(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("application shutdown left device socket open")
	}
}

func TestDeviceDisconnectEventAndInvalidConnectReportTruthfulState(t *testing.T) {
	messages := make(chan deviceMessage, 4)
	client := &deviceClient{emit: func(message deviceMessage) { messages <- message }}
	listener := &deviceEventListener{client: client}
	listener.DispatchWailsEvent(&application.CustomEvent{Name: deviceConnectEventPrefix + "%invalid"})
	if message := <-messages; message.Type != "error" {
		t.Fatalf("invalid event was accepted: %+v", message)
	}
	listener.DispatchWailsEvent(&application.CustomEvent{Name: "desktop:device-disconnect"})
	if message := <-messages; message.Type != "disconnected" || message.Online {
		t.Fatalf("wrong disconnect: %+v", message)
	}
	listener.DispatchWailsEvent(&application.CustomEvent{Name: "desktop:unrelated"})
	select {
	case message := <-messages:
		t.Fatalf("unrelated event changed device: %+v", message)
	default:
	}
}

func TestDeviceEventPreservesDeploymentPrefix(t *testing.T) {
	for _, suffix := range []string{"/colink", "/colink/", "/colink/dashboard"} {
		t.Run(suffix, func(t *testing.T) {
			t.Setenv("OCTOP_HOME", t.TempDir())
			requests := make(chan string, 1)
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests <- r.URL.EscapedPath()
				http.NotFound(w, r)
			}))
			defer srv.Close()
			client := &deviceClient{}
			if err := client.setBase(srv.URL + suffix); err != nil {
				t.Fatal(err)
			}
			defer client.disconnect()
			listener := &deviceEventListener{client: client}
			listener.DispatchWailsEvent(&application.CustomEvent{Name: deviceConnectEventPrefix + url.QueryEscape(`{"access_token":"token","api_path":"/colink/api/devices/connect"}`)})
			select {
			case path := <-requests:
				if path != "/colink/api/devices/connect" {
					t.Fatalf("deployment prefix lost: %q", path)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("native device made no connection attempt")
			}
		})
	}
}

func TestDeviceTokenRefreshUpdatesLiveSessionWithoutReconnect(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	status := make(chan deviceMessage, 2)
	heartbeat := make(chan string, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.CloseNow()
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		var hello map[string]string
		if wsjson.Read(ctx, conn, &hello) != nil {
			return
		}
		if wsjson.Write(ctx, conn, map[string]string{"type": "challenge", "message": "colink-device-v1:7:nonce"}) != nil {
			return
		}
		var proof map[string]string
		if wsjson.Read(ctx, conn, &proof) != nil {
			return
		}
		if wsjson.Write(ctx, conn, deviceMessage{Type: "status", DeviceID: "device-1", Status: "approved", Online: true}) != nil {
			return
		}
		var beat map[string]string
		if wsjson.Read(ctx, conn, &beat) == nil {
			heartbeat <- beat["access_token"]
		}
		_, _, _ = conn.Read(ctx)
	}))
	defer srv.Close()
	client := &deviceClient{emit: func(message deviceMessage) { status <- message }}
	if err := client.setBase(srv.URL); err != nil {
		t.Fatal(err)
	}
	defer client.disconnect()
	listener := &deviceEventListener{client: client}
	listener.DispatchWailsEvent(&application.CustomEvent{Name: deviceConnectEventPrefix + url.QueryEscape(`{"access_token":"initial-token"}`)})
	select {
	case <-status:
	case <-time.After(3 * time.Second):
		t.Fatal("native device did not connect")
	}
	listener.DispatchWailsEvent(&application.CustomEvent{Name: "desktop:device-refresh:" + url.QueryEscape(`{"access_token":"renewed-token"}`)})
	select {
	case token := <-heartbeat:
		if token != "renewed-token" {
			t.Fatalf("live connection did not receive renewed credentials: %q", token)
		}
	case <-time.After(8 * time.Second):
		t.Fatal("renewal did not reach the live device connection")
	}
}
