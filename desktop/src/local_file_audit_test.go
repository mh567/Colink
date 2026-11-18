package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func handleFileAudit(w http.ResponseWriter, r *http.Request) bool {
	if !strings.HasSuffix(r.URL.Path, "/file-events") {
		return false
	}
	w.Write([]byte(`{"recorded":true}`))
	return true
}

func TestReportFileEventPostsBasenameWithoutPrivateContent(t *testing.T) {
	var path string
	var body map[string]string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		json.NewDecoder(r.Body).Decode(&body)
		if r.Header.Get("X-Colink-Device-Session") != "secret" {
			t.Error("missing native session")
		}
		w.Write([]byte(`{"recorded":true}`))
	}))
	defer server.Close()
	session := docxSession{
		Base:           server.URL,
		APIPath:        "/api/devices/connect",
		DeviceID:       "device",
		Token:          "token",
		Secret:         "secret",
		Format:         "docx",
		Context:        context.Background(),
		AuditRequestID: "0123456789abcdef0123456789abcdef",
		AuditPath:      "/员工/私有/合同.docx",
	}
	reportFileEvent(session, "completed", "overwrite", "")
	if !strings.HasSuffix(path, "/api/devices/device/file-events") {
		t.Fatalf("path=%s", path)
	}
	if body["path"] != "合同.docx" || body["event"] != "completed" || body["operation"] != "overwrite" {
		t.Fatalf("body=%v", body)
	}
	if _, ok := body["instruction"]; ok {
		t.Fatal("sent private instruction")
	}
	if _, ok := body["text"]; ok {
		t.Fatal("sent original file text")
	}
}

func TestAuthorizeDOCXSendsRequestIDAndBasename(t *testing.T) {
	var body map[string]string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&body)
		w.Write([]byte(`{"allowed":true}`))
	}))
	defer server.Close()
	session := docxSession{
		Base:           server.URL,
		APIPath:        "/api/devices/connect",
		DeviceID:       "device",
		Token:          "token",
		Secret:         "secret",
		Context:        context.Background(),
		AuditRequestID: "0123456789abcdef0123456789abcdef",
		AuditPath:      "/员工/私有/合同.docx",
	}
	if err := authorizeDOCX(session, "overwrite"); err != nil {
		t.Fatal(err)
	}
	if body["request_id"] != session.AuditRequestID || body["path"] != "合同.docx" || body["action"] != "overwrite" {
		t.Fatalf("body=%v", body)
	}
}
