package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const docxTextLimit = 60000
const docxFileLimit = 20 * 1024 * 1024

var (
	errDOCXFileLimit = errors.New("DOCX 超过 20 MB")
	errDOCXBodyLimit = errors.New("DOCX 正文缺失或超过 20 MB")
	errDOCXTextLimit = errors.New("DOCX 正文超过 60000 字符")
	errDOCXNoText    = errors.New("DOCX 没有可提取的正文")
)

// Native sessions and directory handles never enter the dashboard or the model.
type docxSession struct {
	Base, APIPath, DeviceID, Token, Secret string
	Format                                 string
	SourceFormat                           string
	AgentID                                string
	UserID                                 int
	Generation                             uint64
	Context                                context.Context
	Refresh                                func() (docxSession, error)
	Locale                                 Locale
	Apply                                  func(func() error) error
	TaskChanged                            func()
	RecordRecovery                         func(string) error
}

func (s docxSession) scope() string { return fmt.Sprintf("%s\n%d\n%s", s.Base, s.UserID, s.DeviceID) }

type docxRequest struct {
	RequestID   string `json:"request_id,omitempty"`
	AgentID     string `json:"agent_id,omitempty"`
	Format      string `json:"format,omitempty"`
	Action      string `json:"action"`
	Mode        string `json:"mode,omitempty"`
	SourceGrant string `json:"source_grant,omitempty"`
	Source      string `json:"source,omitempty"`
	OutputGrant string `json:"output_grant,omitempty"`
	Output      string `json:"output,omitempty"`
	Instruction string `json:"instruction,omitempty"`
}
type directoryGrant struct {
	ID          string `json:"id"`
	Path        string `json:"path"`
	Mode        string `json:"mode"`
	Identity    string `json:"identity"`
	Destructive bool   `json:"destructive,omitempty"`
	Overwrite   bool   `json:"overwrite,omitempty"`
}
type docxTask struct {
	AgentID     string `json:"agent_id,omitempty"`
	Format      string `json:"format,omitempty"`
	ID          string `json:"id"`
	Instruction string `json:"instruction"`
	Source      string `json:"source"`
	Output      string `json:"output"`
	Status      string `json:"status"`
	Error       string `json:"error,omitempty"`
	Operation   string `json:"operation,omitempty"`
	Recovery    string `json:"recovery,omitempty"`
}
type docxState struct {
	Grants []directoryGrant `json:"grants"`
	Tasks  []docxTask       `json:"tasks"`
}
type docxEvent struct {
	DeviceID         string           `json:"device_id"`
	Grants           []directoryGrant `json:"grants"`
	Tasks            []docxTask       `json:"tasks"`
	Busy             bool             `json:"busy"`
	Error            string           `json:"error,omitempty"`
	ManagedDirectory bool             `json:"managed_directory,omitempty"`
}
type localDOCX struct {
	mu                 sync.Mutex
	home               string
	roots              map[string]*os.Root
	busy               bool
	confirmDestructive func(context.Context, Locale, string, string) (bool, error)
}

func newLocalDOCX(home string) *localDOCX {
	return &localDOCX{home: home, roots: map[string]*os.Root{}}
}
func localID() string {
	var data [16]byte
	if _, err := rand.Read(data[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(data[:])
}
func (w *localDOCX) load() (map[string]docxState, error) {
	data, err := os.ReadFile(filepath.Join(w.home, "local-docx.json"))
	if errors.Is(err, os.ErrNotExist) {
		return map[string]docxState{}, nil
	}
	if err != nil {
		return nil, err
	}
	var states map[string]docxState
	if err = json.Unmarshal(data, &states); err != nil {
		return nil, fmt.Errorf("本机任务记录损坏：%w", err)
	}
	if states == nil {
		return nil, errors.New("本机任务记录无效")
	}
	return states, nil
}
func (w *localDOCX) save(states map[string]docxState) error {
	if err := os.MkdirAll(w.home, 0700); err != nil {
		return err
	}
	data, err := json.Marshal(states)
	if err != nil {
		return err
	}
	return saveLocalTaskJSON(filepath.Join(w.home, "local-docx.json"), data)
}

func saveLocalTaskJSON(path string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".local-docx-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}
func (w *localDOCX) state(s docxSession) (docxState, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	states, err := w.load()
	if err != nil {
		return docxState{}, err
	}
	state := states[s.scope()]
	if state.Grants == nil {
		state.Grants = []directoryGrant{}
	}
	if state.Tasks == nil {
		state.Tasks = []docxTask{}
	}
	// A running record after process restart is not evidence of completion.
	changed := false
	if !w.busy {
		for i := range state.Tasks {
			if state.Tasks[i].Status == "running" {
				state.Tasks[i].Status = "interrupted"
				state.Tasks[i].Error = "客户端已中断；请核对目标文件后使用新文件名重试"
				changed = true
			}
		}
	}
	if changed {
		states[s.scope()] = state
		if err = w.save(states); err != nil {
			return docxState{}, err
		}
	}
	tasks, err := w.destructiveTasks(s)
	if err != nil {
		return docxState{}, err
	}
	state.Tasks = append(state.Tasks, tasks...)
	return state, nil
}
func (w *localDOCX) grant(s docxSession, path, mode string) (directoryGrant, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	managed := mode == "managed"
	if managed {
		mode = "rw"
	}
	if mode != "ro" && mode != "rw" {
		return directoryGrant{}, errors.New("无效目录权限")
	}
	path, err := filepath.EvalSymlinks(path)
	if err != nil {
		return directoryGrant{}, err
	}
	path, err = filepath.Abs(path)
	if err != nil {
		return directoryGrant{}, err
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		return directoryGrant{}, fmt.Errorf("无法建立目录隔离：%w", err)
	}
	keep := false
	defer func() {
		if !keep {
			root.Close()
		}
	}()
	f, err := root.Open(".")
	if err != nil {
		return directoryGrant{}, err
	}
	identity, _, err := fileIdentity(f)
	f.Close()
	if err != nil {
		return directoryGrant{}, err
	}
	grant := directoryGrant{ID: localID(), Path: path, Mode: mode, Identity: identity, Destructive: managed, Overwrite: managed}
	states, err := w.load()
	if err != nil {
		return directoryGrant{}, err
	}
	state := states[s.scope()]
	if managed {
		for _, existing := range state.Grants {
			if existing.Destructive && existing.Path == path && existing.Identity == identity {
				return existing, nil
			}
		}
	}
	state.Grants = append(state.Grants, grant)
	states[s.scope()] = state
	if err = w.save(states); err != nil {
		return directoryGrant{}, err
	}
	w.roots[s.scope()+"\n"+grant.ID] = root
	keep = true
	return grant, nil
}
func (w *localDOCX) openGrant(s docxSession, id string, write bool) (*os.Root, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	states, err := w.load()
	if err != nil {
		return nil, err
	}
	for _, grant := range states[s.scope()].Grants {
		if grant.ID != id {
			continue
		}
		if write && grant.Mode != "rw" {
			return nil, errors.New("只读目录不能写入")
		}
		key := s.scope() + "\n" + id
		root, err := os.OpenRoot(grant.Path)
		if err != nil {
			return nil, err
		}
		f, err := root.Open(".")
		if err != nil {
			root.Close()
			return nil, err
		}
		identity, _, err := fileIdentity(f)
		f.Close()
		if err != nil || identity != grant.Identity {
			root.Close()
			return nil, errors.New("授权目录已改变，请重新选择授权目录")
		}
		if existing := w.roots[key]; existing != nil {
			root.Close()
			return existing, nil
		}
		w.roots[key] = root
		return root, nil
	}
	return nil, errors.New("目录未获当前账号和设备授权")
}
func docxFilename(name string) error {
	if name == "" || !filepath.IsLocal(name) || filepath.Base(name) != name || strings.ContainsAny(name, "/\\:") || !strings.EqualFold(filepath.Ext(name), ".docx") || strings.TrimSpace(name) != name {
		return errors.New("请输入授权目录内的 DOCX 文件名（不含子目录）")
	}
	return nil
}
func readDOCX(reader io.ReaderAt, size int64) (string, error) {
	if size > docxFileLimit {
		return "", errDOCXFileLimit
	}
	z, err := zip.NewReader(reader, size)
	if err != nil {
		return "", errors.New("不是有效的 DOCX 文件")
	}
	var document *zip.File
	for _, part := range z.File {
		if part.Name == "word/document.xml" {
			if document != nil {
				return "", errors.New("DOCX 正文重复")
			}
			document = part
		}
	}
	if document == nil {
		return "", errors.New("DOCX 正文缺失或超过 20 MB")
	}
	if document.UncompressedSize64 > docxFileLimit {
		return "", errDOCXBodyLimit
	}
	stream, err := document.Open()
	if err != nil {
		return "", err
	}
	defer stream.Close()
	decoder := xml.NewDecoder(io.LimitReader(stream, docxFileLimit+1))
	var output strings.Builder
	inText := false
	count := 0
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", errors.New("DOCX 正文解析失败")
		}
		piece := ""
		switch t := token.(type) {
		case xml.StartElement:
			if t.Name.Space == "http://schemas.openxmlformats.org/wordprocessingml/2006/main" {
				switch t.Name.Local {
				case "t":
					inText = true
				case "tab":
					piece = "\t"
				case "br":
					piece = "\n"
				}
			}
		case xml.EndElement:
			if t.Name.Local == "t" {
				inText = false
			}
			if t.Name.Local == "p" {
				piece = "\n"
			}
		case xml.CharData:
			if inText {
				piece = string(t)
			}
		}
		count += utf8.RuneCountInString(piece)
		if count > docxTextLimit {
			return "", errDOCXTextLimit
		}
		output.WriteString(piece)
	}
	if strings.TrimSpace(output.String()) == "" {
		return "", errDOCXNoText
	}
	return output.String(), nil
}
func makeDOCX(text string) ([]byte, error) {
	if strings.TrimSpace(text) == "" || utf8.RuneCountInString(text) > docxTextLimit {
		return nil, errors.New("模型正文为空或超过 60000 字符")
	}
	var body bytes.Buffer
	body.WriteString(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>`)
	for _, line := range strings.Split(text, "\n") {
		body.WriteString(`<w:p><w:r><w:t xml:space="preserve">`)
		if err := xml.EscapeText(&body, []byte(line)); err != nil {
			return nil, err
		}
		body.WriteString(`</w:t></w:r></w:p>`)
	}
	body.WriteString(`<w:sectPr/></w:body></w:document>`)
	var result bytes.Buffer
	z := zip.NewWriter(&result)
	parts := []struct{ name, text string }{
		{"[Content_Types].xml", `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>`},
		{"_rels/.rels", `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>`},
		{"word/document.xml", body.String()}}
	for _, part := range parts {
		entry, err := z.Create(part.name)
		if err != nil {
			return nil, err
		}
		if _, err = io.WriteString(entry, part.text); err != nil {
			return nil, err
		}
	}
	if err := z.Close(); err != nil {
		return nil, err
	}
	return result.Bytes(), nil
}
func refreshDOCXSession(s docxSession) (docxSession, error) {
	if s.Refresh != nil {
		latest, err := s.Refresh()
		if err != nil {
			return s, err
		}
		if latest.Generation != s.Generation || latest.scope() != s.scope() || latest.Secret != s.Secret {
			return s, errors.New("设备会话已改变，任务已停止")
		}
		s.Token, s.Context = latest.Token, latest.Context
	}
	return s, nil
}

func docxCall(s docxSession, action string, body any, result any) error {
	s, err := refreshDOCXSession(s)
	if err != nil {
		return err
	}
	u, err := url.Parse(s.Base)
	if err != nil {
		return err
	}
	apiPath := s.APIPath
	if apiPath == "" {
		apiPath = "/api/devices/connect"
	}
	endpoint, err := url.ParseRequestURI(apiPath)
	if err != nil || endpoint.IsAbs() || endpoint.Host != "" || endpoint.RawQuery != "" || endpoint.ForceQuery || !strings.HasPrefix(endpoint.Path, "/") || strings.HasPrefix(endpoint.Path, "//") || !strings.HasSuffix(endpoint.Path, "/api/devices/connect") {
		return errors.New("无效设备任务接口地址")
	}
	format := s.Format
	if format == "" {
		format = "docx"
	}
	if format != "docx" && format != "xlsx" && format != "ocr" && format != "pptx" {
		return errors.New("不支持的文件类型")
	}
	u.Path = strings.TrimSuffix(endpoint.Path, "/connect") + "/" + s.DeviceID + "/" + format + "/" + action
	u.RawPath = ""
	u.RawQuery = ""
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(s.Context, 2*time.Minute)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), bytes.NewReader(data))
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", "Bearer "+s.Token)
	request.Header.Set("X-Colink-Device-Session", s.Secret)
	request.Header.Set("Content-Type", "application/json")
	client := http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("拒绝任务请求重定向") }}
	response, err := client.Do(request)
	if err != nil {
		return errors.New("无法连接任务服务")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		var envelope struct {
			Error struct {
				Message string `json:"message"`
			} `json:"error"`
		}
		json.NewDecoder(io.LimitReader(response.Body, 16384)).Decode(&envelope)
		if envelope.Error.Message != "" {
			return errors.New(envelope.Error.Message)
		}
		return fmt.Errorf("任务服务拒绝操作（%d）", response.StatusCode)
	}
	if err = json.NewDecoder(io.LimitReader(response.Body, 400000)).Decode(result); err != nil {
		return errors.New("任务服务返回无效结果")
	}
	return nil
}
func authorizeDOCX(s docxSession, action string) error {
	var reply struct {
		Allowed bool `json:"allowed"`
	}
	payload := map[string]string{"action": action}
	if s.Format == "pptx" && s.SourceFormat == "docx" && action == "create" {
		payload["source_format"] = s.SourceFormat
	}
	if s.AgentID != "" {
		payload["agent_id"] = s.AgentID
	}
	if err := docxCall(s, "authorize", payload, &reply); err != nil {
		return err
	}
	if !reply.Allowed {
		return errors.New("操作未获授权")
	}
	return nil
}
func (w *localDOCX) record(s docxSession, task docxTask) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	states, err := w.load()
	if err != nil {
		return err
	}
	state := states[s.scope()]
	found := false
	for i := range state.Tasks {
		if state.Tasks[i].ID == task.ID {
			state.Tasks[i] = task
			found = true
		}
	}
	if !found {
		state.Tasks = append(state.Tasks, task)
	}
	states[s.scope()] = state
	return w.save(states)
}
func (w *localDOCX) run(s docxSession, request docxRequest, current func() error) (task docxTask, err error) {
	if isDestructiveAction(request.Action) {
		if request.Format == "pptx" {
			return task, errors.New("PPTX_CREATE_ONLY")
		}
		return w.runDestructive(s, request, current)
	}
	if request.Action != "" && request.Action != "run" {
		return task, errors.New("不支持的本机任务")
	}
	format := request.Format
	if format == "" {
		format = "docx"
	}
	if format != "docx" && format != "xlsx" && format != "ocr" && format != "pptx" {
		return task, errors.New("不支持的文件类型")
	}
	s.Format = format
	s.AgentID = request.AgentID
	filename := docxFilename
	outputFilename := docxFilename
	if format == "xlsx" {
		filename = xlsxFilename
		outputFilename = xlsxFilename
	}
	if format == "ocr" {
		filename, outputFilename = ocrSourceFilename, ocrOutputFilename
	}
	if format == "pptx" {
		filename, outputFilename = pptxSourceFilename, pptxFilename
		if strings.EqualFold(filepath.Ext(request.Source), ".docx") {
			s.SourceFormat = "docx"
		}
	}
	task = docxTask{AgentID: request.AgentID, Format: format, ID: localID(), Instruction: request.Instruction, Source: request.Source, Output: request.Output, Status: "running"}
	if err = filename(request.Source); err != nil {
		if format == "pptx" {
			return task, errors.New("PPTX_SOURCE_FILENAME")
		}
		return task, err
	}
	if err = outputFilename(request.Output); err != nil {
		return task, err
	}
	if format != "ocr" && (strings.TrimSpace(request.Instruction) == "" || utf8.RuneCountInString(request.Instruction) > 8000) {
		if format == "pptx" {
			return task, errors.New("PPTX_INSTRUCTION_REQUIRED")
		}
		return task, errors.New("请输入不超过 8000 字符的任务说明")
	}
	source, err := w.openGrant(s, request.SourceGrant, false)
	if err != nil {
		if format == "pptx" {
			return task, errors.New("PPTX_SOURCE_FOLDER")
		}
		return task, err
	}
	output, err := w.openGrant(s, request.OutputGrant, true)
	if err != nil {
		if format == "pptx" {
			return task, errPPTXOutputFolder
		}
		return task, err
	}
	task.Source = filepath.Join(source.Name(), request.Source)
	task.Output = filepath.Join(output.Name(), request.Output)
	if err = w.record(s, task); err != nil {
		return task, err
	}
	defer func() {
		if err != nil {
			task.Status = "failed"
			task.Error = err.Error()
		} else {
			task.Status = "completed"
		}
		if saveErr := w.record(s, task); saveErr != nil {
			err = fmt.Errorf("任务结果记录失败，请核对目标文件：%w", saveErr)
		}
	}()
	data, err := generateLocalOutput(s, request, source, current)
	if err != nil {
		return task, err
	}
	if err = authorizeDOCX(s, "create"); err != nil {
		return task, err
	}
	if err = current(); err != nil {
		return task, err
	}
	output, err = w.openGrant(s, request.OutputGrant, true)
	if err != nil {
		if format == "pptx" {
			return task, errPPTXOutputFolder
		}
		return task, err
	}
	if handled, createErr := createLocalOutput(s, output, request.Output, data); handled {
		return task, presentFileopsError(s.Locale, createErr)
	}
	if format == "pptx" {
		return task, writeLocalPPTXOutput(s, output, request.Output, data, func() error {
			if err := authorizeDOCX(s, "create"); err != nil {
				return err
			}
			return current()
		})
	}
	target, err := output.OpenFile(request.Output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return task, fmt.Errorf("无法新建产物（已有文件不会覆盖）：%w", err)
	}
	if _, err = target.Write(data); err == nil {
		err = target.Sync()
	}
	closeErr := target.Close()
	if err == nil {
		err = closeErr
	}
	return task, err
}

func generateLocalOutput(s docxSession, request docxRequest, source *os.Root, current func() error) ([]byte, error) {
	format := s.Format
	var err error
	if err = current(); err != nil {
		return nil, err
	}
	if err = authorizeDOCX(s, "read"); err != nil {
		return nil, err
	}
	if err = current(); err != nil {
		return nil, err
	}
	if format == "pptx" && s.SourceFormat == "docx" {
		sourceSession := s
		sourceSession.Format, sourceSession.AgentID = "docx", ""
		if err = authorizeDOCX(sourceSession, "read"); err != nil {
			return nil, err
		}
		if err = current(); err != nil {
			return nil, err
		}
	}
	f, size, err := openLocalTaskSource(source, request.Source)
	if err != nil {
		if format == "pptx" {
			return nil, errors.New("PPTX_SOURCE_UNAVAILABLE")
		}
		return nil, err
	}
	defer f.Close()
	read := readDOCX
	if format == "xlsx" {
		read = readXLSX
	}
	var text string
	if format == "ocr" {
		text, err = readLocalOCR(s.Context, f, size)
	} else if format == "pptx" && s.SourceFormat != "docx" {
		text, err = readLocalPPTX(s.Context, f, size)
	} else {
		text, err = read(f, size)
		if err != nil && format == "pptx" {
			switch err {
			case errDOCXFileLimit, errDOCXBodyLimit, errDOCXTextLimit:
				err = errors.New("PPTX_SOURCE_LIMIT")
			case errDOCXNoText:
				err = errors.New("PPTX_NO_TEXT")
			default:
				err = errors.New("PPTX_INVALID_SOURCE")
			}
		}
	}
	if err != nil {
		return nil, err
	}
	var data []byte
	if format == "ocr" {
		data = []byte(text)
	} else {
		var generated struct {
			Text string `json:"text"`
		}
		payload := map[string]string{"instruction": request.Instruction, "text": text}
		if format == "pptx" && s.SourceFormat == "docx" {
			payload["source_format"] = s.SourceFormat
		}
		if request.Action == "overwrite" {
			payload["action"] = "overwrite"
		}
		if request.AgentID != "" {
			payload["agent_id"] = request.AgentID
		}
		if err = docxCall(s, "generate", payload, &generated); err != nil {
			return nil, err
		}
		if format == "pptx" {
			return makeLocalPPTX(s.Context, generated.Text)
		}
		makeFile := makeDOCX
		if format == "xlsx" {
			makeFile = makeXLSX
		}
		data, err = makeFile(generated.Text)
		if err != nil {
			return nil, err
		}
	}
	return data, nil
}

func openLocalTaskSource(source *os.Root, name string) (*os.File, int64, error) {
	info, err := source.Lstat(name)
	if err != nil {
		return nil, 0, err
	}
	if !info.Mode().IsRegular() {
		return nil, 0, errors.New("只允许读取普通文件，不允许符号链接")
	}
	file, err := source.OpenFile(name, os.O_RDONLY|docxNonblock, 0)
	if err != nil {
		return nil, 0, err
	}
	actual, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, 0, err
	}
	_, links, err := fileIdentity(file)
	if err != nil {
		file.Close()
		return nil, 0, err
	}
	if !actual.Mode().IsRegular() || !os.SameFile(info, actual) || links != 1 {
		file.Close()
		return nil, 0, errors.New("文件已改变或存在硬链接，拒绝读取")
	}
	return file, actual.Size(), nil
}
