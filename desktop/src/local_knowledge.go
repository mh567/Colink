package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"
)

func (w *localDOCX) importKnowledge(s docxSession, grantID, source, kbID, folder string, current func() error) error {
	if kbID == "" || strings.IndexFunc(kbID, func(r rune) bool {
		return !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_')
	}) >= 0 {
		return errors.New("无效知识库")
	}
	filename := filepath.Base(source)
	if filename == "." || filename == "" {
		return errors.New("无效文件")
	}
	var reply struct {
		Allowed bool `json:"allowed"`
	}
	if err := current(); err != nil {
		return err
	}
	if err := deviceTaskCall(s, "knowledge", "authorize", map[string]string{
		"kb_id": kbID, "filename": filename,
	}, &reply); err != nil {
		return err
	}
	if !reply.Allowed {
		return errors.New("操作未获授权")
	}
	var file *os.File
	err := w.withGrant(s, grantID, false, func(root *os.Root) error {
		var err error
		file, _, err = openLocalTaskSource(root, source)
		return err
	})
	if err != nil {
		return err
	}
	defer file.Close()
	s, err = refreshDOCXSession(s)
	if err != nil {
		return err
	}
	base, err := url.Parse(s.Base)
	if err != nil {
		return err
	}
	apiPath := s.APIPath
	if apiPath == "" {
		apiPath = "/api/devices/connect"
	}
	endpoint, err := url.ParseRequestURI(apiPath)
	if err != nil || endpoint.IsAbs() || endpoint.Host != "" || endpoint.RawQuery != "" ||
		endpoint.ForceQuery || !strings.HasPrefix(endpoint.Path, "/") ||
		strings.HasPrefix(endpoint.Path, "//") || !strings.HasSuffix(endpoint.Path, "/api/devices/connect") {
		return errors.New("无效设备任务接口地址")
	}
	base.Path = strings.TrimSuffix(endpoint.Path, "/devices/connect") + "/knowledge-bases/" + kbID + "/documents"
	base.RawPath, base.RawQuery = "", ""
	reader, writer := io.Pipe()
	form := multipart.NewWriter(writer)
	result := make(chan error, 1)
	go func() {
		err := func() error {
			part, err := form.CreateFormFile("upload", filename)
			if err != nil {
				return err
			}
			buffer := make([]byte, 32*1024)
			for {
				if err := current(); err != nil {
					return err
				}
				var n int
				var readErr error
				err = w.withGrant(s, grantID, false, func(_ *os.Root) error {
					n, readErr = file.Read(buffer)
					return nil
				})
				if err != nil {
					return err
				}
				if n > 0 {
					if _, err = part.Write(buffer[:n]); err != nil {
						return err
					}
				}
				if readErr == io.EOF {
					break
				}
				if readErr != nil {
					return readErr
				}
			}
			if err := current(); err != nil {
				return err
			}
			if err := form.WriteField("path", path.Join(folder, filename)); err != nil {
				return err
			}
			return form.Close()
		}()
		_ = writer.CloseWithError(err)
		result <- err
	}()
	defer reader.Close()
	ctx, cancel := context.WithTimeout(s.Context, 2*time.Minute)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, base.String(), reader)
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", "Bearer "+s.Token)
	request.Header.Set("X-Colink-Device-Id", s.DeviceID)
	request.Header.Set("X-Colink-Device-Session", s.Secret)
	request.Header.Set("Content-Type", form.FormDataContentType())
	client := http.Client{CheckRedirect: func(*http.Request, []*http.Request) error {
		return errors.New("拒绝任务请求重定向")
	}}
	response, err := client.Do(request)
	if err != nil {
		_ = reader.CloseWithError(err)
		<-result
		return errors.New("无法连接知识库服务")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		_ = reader.CloseWithError(errors.New("服务拒绝导入"))
		<-result
		return fmt.Errorf("知识库导入失败（%d）", response.StatusCode)
	}
	if err = <-result; err != nil {
		return err
	}
	return nil
}
