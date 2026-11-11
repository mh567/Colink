package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"net/url"
	"runtime"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/wailsapp/wails/v3/pkg/application"
	"golang.org/x/net/idna"
)

const (
	browserHelloPrefix   = "colink:browser-hello:"
	browserMessagePrefix = "colink:browser:"
)

type browserRequest struct {
	browserAction
	ID          string `json:"request_id"`
	Instruction string `json:"instruction,omitempty"`
	Capability  string `json:"capability,omitempty"`
}

func browserOrigin(raw string) string {
	address, err := url.Parse(raw)
	if err != nil || address.User != nil || address.Hostname() == "" {
		return ""
	}
	var defaultPort uint64
	switch address.Scheme {
	case "http":
		defaultPort = 80
	case "https":
		defaultPort = 443
	default:
		return ""
	}
	host := address.Hostname()
	if strings.Contains(host, ":") {
		ip, err := netip.ParseAddr(host)
		if err != nil || ip.Zone() != "" {
			return ""
		}
		host = "[" + ip.String() + "]"
	} else {
		host, err = idna.Lookup.ToASCII(host)
		if err != nil {
			return ""
		}
		host = strings.ToLower(host)
	}
	if rawPort := address.Port(); rawPort != "" {
		port, err := strconv.ParseUint(rawPort, 10, 16)
		if err != nil {
			return ""
		}
		if port != defaultPort {
			host += ":" + strconv.FormatUint(port, 10)
		}
	}
	return address.Scheme + "://" + host
}

func browserMessageOrigin(base string, info *application.OriginInfo, platform string) (string, bool) {
	expected := browserOrigin(base)
	return expected, expected != "" && info != nil && browserOrigin(info.Origin) == expected &&
		(info.TopOrigin == "" || browserOrigin(info.TopOrigin) == expected) &&
		(platform != "darwin" || info.IsMainFrame)
}

func (a *App) browserCapability(session docxSession) string {
	signer := hmac.New(sha256.New, a.browserBridgeKey[:])
	fmt.Fprintf(signer, "%d\n%s\n%s", session.Generation, session.scope(), session.Secret)
	return hex.EncodeToString(signer.Sum(nil))
}

func (a *App) hasBrowserCapability(session docxSession, value string) bool {
	return hmac.Equal([]byte(a.browserCapability(session)), []byte(value))
}

func (a *App) handleBrowserMessage(window application.Window, message string, info *application.OriginInfo) {
	hello, isHello := strings.CutPrefix(message, browserHelloPrefix)
	raw, isOperation := strings.CutPrefix(message, browserMessagePrefix)
	if (!isHello && !isOperation) || a.window == nil || window != a.window {
		return
	}
	session, err := a.device.docxSession()
	if err != nil {
		return
	}
	origin, allowed := browserMessageOrigin(session.Base, info, runtime.GOOS)
	if !allowed {
		return
	}
	if isHello {
		if validDestructiveID(hello) {
			a.emitBrowserEvent(session, origin, "colink:browser-ready", map[string]string{
				"request_id": hello, "device_id": session.DeviceID, "capability": a.browserCapability(session),
			})
		}
		return
	}
	request, err := parseBrowserRequest(raw)
	if validDestructiveID(request.ID) && a.hasBrowserCapability(session, request.Capability) {
		go a.handleLocalBrowser(session, origin, request, err)
	}
}

func browserHTTPURL(value string) bool {
	if len(value) > 4096 || strings.Contains(value, "\\") {
		return false
	}
	parsed, err := url.ParseRequestURI(value)
	return err == nil && parsed.Hostname() != "" && parsed.User == nil &&
		(parsed.Scheme == "http" || parsed.Scheme == "https")
}

func parseBrowserRequest(raw string) (browserRequest, error) {
	var request browserRequest
	invalid := errors.New("BROWSER_INVALID_REQUEST")
	if len(raw) > 120000 {
		return request, invalid
	}
	decoded, err := url.QueryUnescape(raw)
	if err != nil {
		return request, invalid
	}
	decoder := json.NewDecoder(strings.NewReader(decoded))
	decoder.DisallowUnknownFields()
	var extra any
	if decoder.Decode(&request) != nil || decoder.Decode(&extra) != io.EOF || !validDestructiveID(request.ID) {
		return request, invalid
	}
	if request.URL != "" && !browserHTTPURL(request.URL) {
		return request, errors.New("BROWSER_INVALID_URL")
	}
	if request.TabID != "" {
		if _, err := hex.DecodeString(request.TabID); len(request.TabID) != 32 || err != nil {
			return request, invalid
		}
	}
	switch request.Action {
	case "open", "new_tab":
		if request.TabID == "" && request.Instruction == "" {
			return request, nil
		}
	case "list", "close":
		if request.TabID == "" && request.URL == "" && request.Instruction == "" {
			return request, nil
		}
	case "select_tab", "close_tab":
		if request.TabID != "" && request.URL == "" && request.Instruction == "" {
			return request, nil
		}
	case "query":
		if request.TabID != "" && request.URL != "" && strings.TrimSpace(request.Instruction) != "" &&
			utf8.RuneCountInString(request.Instruction) <= 8000 {
			return request, nil
		}
	case "capture":
		if request.TabID != "" && request.URL != "" && request.Instruction == "" {
			return request, nil
		}
	}
	return request, invalid
}

func (a *App) handleLocalBrowser(session docxSession, origin string, request browserRequest, err error) {
	session.Locale = a.store.get().Locale
	current := func() error { return a.device.currentDOCX(session) }
	var result browserResult
	if err == nil {
		result, err = a.browser.run(session, request, current)
	}
	if err != nil {
		result = browserResult{Error: err.Error()}
	}
	event := struct {
		browserResult
		RequestID string `json:"request_id"`
		DeviceID  string `json:"device_id"`
	}{browserResult: result, RequestID: request.ID, DeviceID: session.DeviceID}
	a.emitBrowserEvent(session, origin, "colink:browser-status", event)
}

func (a *App) emitBrowserEvent(session docxSession, origin, name string, event any) {
	data, err := json.Marshal(event)
	if err != nil || a.window == nil || a.device.currentDOCX(session) != nil {
		return
	}
	// Linux reports the top URL for frame messages. Only the top page receives the capability.
	a.window.ExecJS(fmt.Sprintf(
		`if (window === window.top && window.location.origin === %s) {
			window.dispatchEvent(new CustomEvent(%s,{detail:%s}));
		}`, jsonString(origin), jsonString(name), data,
	))
}
