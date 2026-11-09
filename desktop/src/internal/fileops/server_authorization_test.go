package fileops

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func sampleServerCredentials() Credentials {
	return Credentials{Format: "xlsx", DeviceID: "01ARZ3NDEKTSV4RRFFQ69G5FAV", Bearer: "fixture.access-token.signature", Session: "fixture_native-session"}
}

func TestNormalizeServerURL(t *testing.T) {
	for raw, want := range map[string]string{
		"HTTPS://EXAMPLE.test:443/colink/": "https://example.test/colink",
		"https://example.test:8443/":       "https://example.test:8443",
		"https://[::1]:443/office/site":    "https://[::1]/office/site",
		"https://example.test/office\n":    "https://example.test/office",
	} {
		got, err := NormalizeServerURL(raw)
		if err != nil || got != want {
			t.Errorf("NormalizeServerURL(%q) = %q, %v; want %q", raw, got, err, want)
		}
	}
	for _, raw := range []string{
		"http://example.test", "//example.test", "https:///colink",
		"https://user:fixture-password@example.test", "https://example.test?", "https://example.test#",
		"https://example.test:70000", "https://example.test:", "https://example.test/a//b",
		"https://example.test/office/../colink", "https://example.test/%63olink", "https://example.test/a%2Fb",
		"https://example.test/a%0ab", "https://example.test/path with spaces",
		"https://example.test/office\nhttps://other.test", "https://example.test/office\n\n",
	} {
		got, err := NormalizeServerURL(raw)
		if err == nil || got != "" || strings.Contains(err.Error(), "fixture-password") {
			t.Error("invalid server URL was accepted or exposed")
		}
	}
}

func TestServerAuthorizerPostsActionOverTLSWithoutProxy(t *testing.T) {
	creds := sampleServerCredentials()
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil || string(body) != `{"action":"delete"}` || r.Method != http.MethodPost ||
			r.URL.RequestURI() != "/office/api/devices/"+creds.DeviceID+"/xlsx/authorize" ||
			r.Header.Get("Authorization") != "Bearer "+creds.Bearer ||
			r.Header.Get("X-Colink-Device-Session") != creds.Session || r.Header.Get("Content-Type") != "application/json" {
			t.Error("incorrect device authorization request")
			http.Error(w, "wrong request", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_, _ = io.WriteString(w, " {\"allowed\": true} \n")
	}))
	defer server.Close()
	transport := server.Client().Transport.(*http.Transport).Clone()
	transport.Proxy = func(*http.Request) (*url.URL, error) { return nil, errors.New("proxy must not be used") }
	authorize, err := newServerAuthorizer(server.URL+"/office/", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	if err := authorize(context.Background(), creds, "delete"); err != nil {
		t.Fatalf("native authorization failed: %v", err)
	}
}

func TestServerAuthorizerRejectsMalformedCredentialsAndActionBeforeNetwork(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("invalid credentials or action reached the server")
		http.Error(w, "unexpected request", http.StatusBadRequest)
	}))
	defer server.Close()
	authorize, err := newServerAuthorizer(server.URL, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*Credentials){
		func(c *Credentials) { c.Format = "../../xlsx" },
		func(c *Credentials) { c.DeviceID = "../" + c.DeviceID },
		func(c *Credentials) { c.DeviceID = strings.Repeat("O", 26) },
		func(c *Credentials) { c.Bearer = "fixture.access\r\nInjected: secret" },
		func(c *Credentials) { c.Bearer = strings.Repeat("a", 8193) },
		func(c *Credentials) { c.Session = "" },
		func(c *Credentials) { c.Session = "fixture native session" },
		func(c *Credentials) { c.Session = strings.Repeat("a", 129) },
	} {
		creds := sampleServerCredentials()
		mutate(&creds)
		if err := authorize(context.Background(), creds, "delete"); err == nil || strings.Contains(err.Error(), "fixture") {
			t.Error("invalid credentials were accepted or exposed")
		}
	}
	ocr := sampleServerCredentials()
	ocr.Format = "ocr"
	for _, action := range []string{"overwrite", "delete", "execute"} {
		if err := authorize(context.Background(), ocr, action); err == nil {
			t.Error("unsupported OCR action was accepted")
		}
	}
	if err := authorize(context.Background(), sampleServerCredentials(), "execute"); err == nil {
		t.Error("unsupported action was accepted")
	}
}

func TestServerAuthorizerRejectsUnauthorizedOrInvalidReplies(t *testing.T) {
	for _, reply := range []struct {
		status int
		media  string
		body   string
	}{
		{403, "application/json", `{"message":"fixture-native-session"}`},
		{201, "application/json", `{"allowed":true}`},
		{200, "text/html", `{"allowed":true}`},
		{200, "application/json", `{"allowed":false}`},
		{200, "application/json", `{"Allowed":true}`},
		{200, "application/json", `{"allowed":false,"allowed":true}`},
		{200, "application/json", `{"allowed":true,"other":1}`},
		{200, "application/json", `{"allowed":true}{}`},
		{200, "application/json", `{"allowed":true}` + strings.Repeat(" ", 10000)},
	} {
		server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", reply.media)
			w.WriteHeader(reply.status)
			_, _ = io.WriteString(w, reply.body)
		}))
		authorize, err := newServerAuthorizer(server.URL, server.Client())
		if err != nil {
			server.Close()
			t.Fatal(err)
		}
		err = authorize(context.Background(), sampleServerCredentials(), "create")
		server.Close()
		if err == nil || strings.Contains(err.Error(), "fixture-native-session") {
			t.Errorf("invalid authorization reply was accepted or exposed (status %d, length %d)", reply.status, len(reply.body))
		}
	}
}

func TestServerAuthorizerDoesNotFollowRedirects(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/redirected" {
			t.Error("authorization redirected to another endpoint")
			return
		}
		http.Redirect(w, r, "/redirected", http.StatusTemporaryRedirect)
	}))
	defer server.Close()
	authorize, err := newServerAuthorizer(server.URL, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if err := authorize(context.Background(), sampleServerCredentials(), "overwrite"); err == nil {
		t.Error("redirect did not deny authorization")
	}
}

func TestServerAuthorizerVerifiesSystemTLSAndHidesNetworkErrors(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("untrusted TLS server received authorization credentials")
	}))
	defer server.Close()
	authorize, err := NewServerAuthorizer(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	err = authorize(context.Background(), sampleServerCredentials(), "read")
	if err == nil || strings.Contains(err.Error(), server.URL) || strings.Contains(err.Error(), "certificate") {
		t.Error("untrusted TLS was accepted or raw network errors were exposed")
	}
}
