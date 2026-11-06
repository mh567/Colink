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
