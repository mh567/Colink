package main

import (
	"context"
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
	DeadlineMS  int64  `json:"deadline_ms,omitempty"`
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

func (a *App) withBrowserMessages(options application.Options) application.Options {
	existing := options.RawMessageHandler
	options.RawMessageHandler = func(window application.Window, message string, info *application.OriginInfo) {
		if strings.HasPrefix(message, browserHelloPrefix) || strings.HasPrefix(message, browserMessagePrefix) {
			a.handleBrowserMessage(window, message, info)
		} else if existing != nil {
			existing(window, message, info)
		}
	}
	return options
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
			a.emitBrowserEvent(session, origin, "colink:browser-ready", map[string]any{
				"request_id": hello, "device_id": session.DeviceID, "capability": a.browserCapability(session),
				"operation_deadline": true,
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

func validBrowserFormID(id string) bool {
	if len(id) == 0 || len(id) > 128 {
		return false
	}
	for _, c := range id {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
			return false
		}
	}
	return true
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
	if decoder.Decode(&request) != nil || decoder.Decode(&extra) != io.EOF || !validDestructiveID(request.ID) || request.DeadlineMS < 0 {
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
	selected := request.FormID != "" || request.SubmitID != ""
	if (selected && request.Action != "suggest_form" && request.Action != "submit_form") ||
		((request.SnapshotID != "" || request.Values != nil) && request.Action != "submit_form") {
		return request, invalid
	}
	page := request.TabID != "" && request.URL != ""
	instruction := strings.TrimSpace(request.Instruction) != "" && utf8.RuneCountInString(request.Instruction) <= 8000
	switch request.Action {
	case "open", "new_tab":
		if request.TabID == "" && request.Instruction == "" {
			return request, nil
		}
	case "list", "close", "operations":
		if request.TabID == "" && request.URL == "" && request.Instruction == "" {
			return request, nil
		}
	case "select_tab", "close_tab":
		if request.TabID != "" && request.URL == "" && request.Instruction == "" {
			return request, nil
		}
	case "query":
		if page && instruction {
			return request, nil
		}
	case "capture", "forms":
		if page && request.Instruction == "" {
			return request, nil
		}
	case "suggest_form":
		if page && instruction && validBrowserFormID(request.FormID) && validBrowserFormID(request.SubmitID) {
			return request, nil
		}
	case "submit_form":
		if !page || request.Instruction != "" || !validBrowserFormID(request.FormID) ||
			!validBrowserFormID(request.SubmitID) || !validBrowserFormID(request.SnapshotID) || len(request.Values) == 0 {
			return request, invalid
		}
		for id, value := range request.Values {
			if !validBrowserFormID(id) {
				return request, invalid
			}
			switch value.(type) {
			case string, bool:
			default:
				return request, invalid
			}
		}
		return request, nil
	}
	return request, invalid
}

func browserSubmitConfirmation(locale Locale, snapshot browserResult, values map[string]any) string {
	displayValue := func(value any) string {
		if text, ok := value.(string); ok {
			return strconv.Quote(text)
		}
		if value == true {
			return desktopText(locale, copyBrowserChecked)
		}
		return desktopText(locale, copyBrowserUnchecked)
	}
	changes := make([]string, 0, len(values))
	for _, field := range snapshot.Fields {
		if value, ok := values[field.ID]; ok {
			changes = append(changes, desktopText(locale, copyBrowserConfirmChange,
				strconv.Quote(field.Label), displayValue(field.Value), displayValue(value)))
		}
	}
	return desktopText(locale, copyBrowserConfirmMessage, strconv.Quote(snapshot.Title), strconv.Quote(snapshot.URL),
		strconv.Quote(snapshot.ActionURL), strconv.Quote(strings.ToUpper(snapshot.Method)), strconv.Quote(snapshot.Target),
		strconv.Quote(snapshot.FormLabel), strconv.Quote(snapshot.SubmitLabel), strings.Join(changes, "\n"))
}

func (a *App) confirmLocalBrowserSubmit(ctx context.Context, locale Locale, message string) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if a.app == nil || a.window == nil {
		return false, errBrowserService
	}
	answer := make(chan bool, 1)
	dialog := a.app.Dialog.Question().
		SetTitle(desktopText(locale, copyBrowserConfirmTitle)).
		SetMessage(message).
		AttachToWindow(a.window)
	approve, cancel := desktopText(locale, copyBrowserConfirmYes), desktopText(locale, copyBrowserConfirmNo)
	if runtime.GOOS == "windows" {
		// Wails maps these labels to the standard Windows MessageBox responses.
		approve, cancel = "Yes", "No"
	}
	dialog.AddButton(approve).OnClick(func() { answer <- true })
	no := dialog.AddButton(cancel).OnClick(func() { answer <- false })
	dialog.SetDefaultButton(no).SetCancelButton(no)
	dialog.Show()
	select {
	case approved := <-answer:
		return approved, ctx.Err()
	case <-ctx.Done():
		return false, ctx.Err()
	}
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
