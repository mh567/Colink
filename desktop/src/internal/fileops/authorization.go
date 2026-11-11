package fileops

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type Credentials struct {
	Format, DeviceID, Bearer, Session string
}

type authorizationRequest struct {
	Action       string `json:"action"`
	SourceFormat string `json:"source_format,omitempty"`
	AgentID      string `json:"agent_id,omitempty"`
}

type ServerAuthorizer func(context.Context, Credentials, authorizationRequest) error

const (
	authorizationTimeout      = 10 * time.Second
	authorizationResponseSize = 1024
)

var (
	errInvalidServerURL         = errors.New("invalid authorization server URL")
	errServerAuthorization      = errors.New("file operation is not authorized")
	errServerAuthorizationCheck = errors.New("file authorization check unavailable")
)

func NormalizeServerURL(raw string) (string, error) {
	// The installer writes the configured address as one line.
	raw = strings.TrimSuffix(raw, "\n")
	if len(raw) > 2048 || strings.ContainsAny(raw, "?#%\\") {
		return "", errInvalidServerURL
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Opaque != "" || u.User != nil || u.Hostname() == "" || strings.HasSuffix(u.Host, ":") {
		return "", errInvalidServerURL
	}
	host := strings.ToLower(u.Hostname())
	if strings.HasPrefix(u.Host, "[") != strings.Contains(host, ":") {
		return "", errInvalidServerURL
	}
	if address, addrErr := netip.ParseAddr(host); addrErr == nil {
		host = address.String()
	} else if !validServerHostname(host) {
		return "", errInvalidServerURL
	}
	port := u.Port()
	if port != "" {
		value, portErr := strconv.Atoi(port)
		if portErr != nil || value < 1 || value > 65535 {
			return "", errInvalidServerURL
		}
		if value == 443 {
			port = ""
		} else {
			port = strconv.Itoa(value)
		}
	}
	u.Host = host
	if port != "" {
		u.Host = net.JoinHostPort(host, port)
	} else if strings.Contains(host, ":") {
		u.Host = "[" + host + "]"
	}
	u.Path = strings.TrimSuffix(u.Path, "/")
	if u.Path != "" {
		for _, part := range strings.Split(strings.TrimPrefix(u.Path, "/"), "/") {
			if part == "." || part == ".." || !validAuthorizationToken(part, 2048, "-._~") {
				return "", errInvalidServerURL
			}
		}
	}
	return u.String(), nil
}

func validServerHostname(host string) bool {
	if len(host) > 254 {
		return false
	}
	for _, label := range strings.Split(strings.TrimSuffix(host, "."), ".") {
		if strings.HasPrefix(label, "-") || strings.HasSuffix(label, "-") || !validAuthorizationToken(label, 63, "-") {
			return false
		}
	}
	return true
}

func NewServerAuthorizer(raw string) (ServerAuthorizer, error) {
	return newServerAuthorizer(raw, nil)
}

func newServerAuthorizer(raw string, trusted *http.Client) (ServerAuthorizer, error) {
	base, err := NormalizeServerURL(raw)
	if err != nil {
		return nil, err
	}
	transport := &http.Transport{
		DialContext:         (&net.Dialer{Timeout: authorizationTimeout}).DialContext,
		TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
		TLSHandshakeTimeout: authorizationTimeout,
		ForceAttemptHTTP2:   true,
		IdleConnTimeout:     30 * time.Second,
	}
	if trusted != nil {
		original, ok := trusted.Transport.(*http.Transport)
		if !ok || original == nil {
			return nil, errServerAuthorizationCheck
		}
		transport = original.Clone()
	}
	transport.Proxy = nil
	transport.DisableCompression = true
	transport.MaxResponseHeaderBytes = 16 << 10
	client := &http.Client{
		Transport: transport,
		Timeout:   authorizationTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return func(ctx context.Context, creds Credentials, authorization authorizationRequest) error {
		if !validAuthorizationRequest(creds, authorization.Action) {
			return errServerAuthorization
		}
		endpoint := base + "/api/devices/" + creds.DeviceID + "/" + creds.Format + "/authorize"
		body, _ := json.Marshal(authorization)
		request, requestErr := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
		if requestErr != nil {
			return errServerAuthorizationCheck
		}
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Authorization", "Bearer "+creds.Bearer)
		request.Header.Set("X-Colink-Device-Session", creds.Session)
		response, responseErr := client.Do(request)
		if responseErr != nil {
			return errServerAuthorizationCheck
		}
		defer response.Body.Close()
		mediaType, _, mediaErr := mime.ParseMediaType(response.Header.Get("Content-Type"))
		if response.StatusCode != http.StatusOK || mediaErr != nil || mediaType != "application/json" {
			return errServerAuthorization
		}
		data, readErr := io.ReadAll(io.LimitReader(response.Body, authorizationResponseSize+1))
		if readErr != nil || len(data) > authorizationResponseSize || !serverAllowed(data) {
			return errServerAuthorization
		}
		return nil
	}, nil
}

func validAuthorizationRequest(creds Credentials, action string) bool {
	switch creds.Format {
	case "docx", "xlsx":
	case "ocr", "pptx":
		if action != "read" && action != "create" {
			return false
		}
	default:
		return false
	}
	if action != "read" && action != "create" && action != "overwrite" && action != "delete" {
		return false
	}
	if len(creds.DeviceID) != 26 || creds.DeviceID[0] < '0' || creds.DeviceID[0] > '7' || strings.ContainsFunc(creds.DeviceID, func(ch rune) bool {
		return !strings.ContainsRune("0123456789ABCDEFGHJKMNPQRSTVWXYZ", ch)
	}) {
		return false
	}
	return validAuthorizationToken(creds.Bearer, 8192, "-._") && validAuthorizationToken(creds.Session, 128, "-_")
}

func validAuthorizationToken(value string, max int, extra string) bool {
	if len(value) == 0 || len(value) > max {
		return false
	}
	for _, ch := range value {
		if !(ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z' || ch >= '0' && ch <= '9' || strings.ContainsRune(extra, ch)) {
			return false
		}
	}
	return true
}

func serverAllowed(data []byte) bool {
	decoder := json.NewDecoder(bytes.NewReader(data))
	for _, want := range []json.Token{json.Delim('{'), "allowed", true, json.Delim('}')} {
		value, err := decoder.Token()
		if err != nil || value != want {
			return false
		}
	}
	_, err := decoder.Token()
	return err == io.EOF
}
