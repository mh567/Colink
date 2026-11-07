package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"strings"

	"github.com/wailsapp/wails/v3/pkg/application"
)

const docxEventPrefix = "desktop:docx:"

type docxEventListener struct{ app *App }

func (l *docxEventListener) DispatchWailsEvent(event *application.CustomEvent) {
	if event == nil || !strings.HasPrefix(event.Name, docxEventPrefix) {
		return
	}
	go l.app.handleDOCX(strings.TrimPrefix(event.Name, docxEventPrefix))
}
func parseDOCXRequest(raw string) (docxRequest, error) {
	var request docxRequest
	if len(raw) > 100000 {
		return request, errors.New("任务请求过大")
	}
	decoded, err := url.QueryUnescape(raw)
	if err != nil {
		return request, errors.New("无效任务请求")
	}
	decoder := json.NewDecoder(strings.NewReader(decoded))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&request); err != nil {
		return request, errors.New("无效任务请求")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return request, errors.New("无效任务请求")
	}
	if request.Format != "" && request.Format != "docx" && request.Format != "xlsx" && request.Format != "ocr" {
		return request, errors.New("不支持的文件类型")
	}
	if request.Action != "list" && request.Action != "grant" && request.Action != "run" {
		return request, errors.New("不支持该操作；不允许命令执行")
	}
	return request, nil
}
func (a *App) emitDOCX(s docxSession, busy bool, err error) {
	if a.window == nil {
		return
	}
	if a.device.currentDOCX(s) != nil {
		return
	}
	state, stateErr := a.docx.state(s)
	if err == nil {
		err = stateErr
	}
	event := docxEvent{DeviceID: s.DeviceID, Grants: state.Grants, Tasks: state.Tasks, Busy: busy}
	if event.Grants == nil {
		event.Grants = []directoryGrant{}
	}
	if event.Tasks == nil {
		event.Tasks = []docxTask{}
	}
	if err != nil {
		event.Error = err.Error()
	}
	// Local identities are only for reopening the exact directory, not UI data.
	for i := range event.Grants {
		event.Grants[i].Identity = ""
	}
	data, marshalErr := json.Marshal(event)
	if marshalErr != nil {
		return
	}
	a.window.ExecJS(fmt.Sprintf(`window.dispatchEvent(new CustomEvent("colink:docx-status",{detail:%s}));`, data))
}
func (a *App) handleDOCX(raw string) {
	session, err := a.device.docxSession()
	if err != nil {
		return
	}
	request, err := parseDOCXRequest(raw)
	if err != nil {
		a.emitDOCX(session, false, err)
		return
	}
	session.Format = request.Format
	a.docx.mu.Lock()
	busy := a.docx.busy
	if request.Action != "list" && !busy {
		a.docx.busy = true
	}
	a.docx.mu.Unlock()
	if request.Action == "list" {
		a.emitDOCX(session, busy, nil)
		return
	}
	if busy {
		a.emitDOCX(session, true, errors.New("已有本机任务执行中"))
		return
	}
	defer func() { a.docx.mu.Lock(); a.docx.busy = false; a.docx.mu.Unlock(); a.emitDOCX(session, false, err) }()
	a.emitDOCX(session, true, nil)
	switch request.Action {
	case "grant":
		if request.Mode != "ro" && request.Mode != "rw" {
			err = errors.New("无效目录权限")
			return
		}
		if err = authorizeDOCX(session, "read"); err != nil {
			return
		}
		title := "选择只读授权目录"
		if request.Mode == "rw" {
			if err = authorizeDOCX(session, "create"); err != nil {
				return
			}
			title = "选择读写授权目录"
		}
		var path string
		path, err = a.app.Dialog.OpenFile().CanChooseFiles(false).CanChooseDirectories(true).SetTitle(title).AttachToWindow(a.window).PromptForSingleSelection()
		if err != nil || path == "" {
			return
		}
		if err = a.device.currentDOCX(session); err != nil {
			return
		}
		_, err = a.docx.grant(session, path, request.Mode)
	case "run":
		_, err = a.docx.run(session, request, func() error { return a.device.currentDOCX(session) })
	}
}
