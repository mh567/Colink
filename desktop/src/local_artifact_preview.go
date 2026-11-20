package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/wailsapp/wails/v3/pkg/application"
)

const artifactPreviewPrefix = "colink:artifact-preview:"
const artifactPreviewLimit = 8 * 1024 * 1024

var errArtifactPreviewUnavailable = errors.New("ARTIFACT_PREVIEW_UNAVAILABLE")
var errArtifactPreviewTooLarge = errors.New("ARTIFACT_PREVIEW_TOO_LARGE")
var errArtifactPreviewChanged = errors.New("ARTIFACT_PREVIEW_CHANGED")

func artifactOutputDigest(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func (w *localDOCX) previewArtifact(s docxSession, taskID string) ([]byte, error) {
	state, err := w.state(s)
	if err != nil {
		return nil, err
	}
	for _, task := range state.Tasks {
		if task.ID != taskID || task.Status != "completed" || task.Operation == "delete" || !filepath.IsAbs(task.Output) {
			continue
		}
		for _, grant := range state.Grants {
			if filepath.Dir(task.Output) != grant.Path {
				continue
			}
			s.Format, s.AgentID = task.Format, task.AgentID
			if err := authorizeDOCX(s, "read"); err != nil {
				return nil, err
			}
			var data []byte
			err = w.withGrant(s, grant.ID, false, func(root *os.Root) error {
				if task.OutputHash == "" {
					return errArtifactPreviewChanged
				}
				file, size, err := openLocalTaskSource(root, filepath.Base(task.Output))
				if err != nil {
					return err
				}
				defer file.Close()
				if size > artifactPreviewLimit {
					return errArtifactPreviewTooLarge
				}
				data, err = io.ReadAll(io.LimitReader(file, artifactPreviewLimit+1))
				if err != nil {
					return err
				}
				if len(data) > artifactPreviewLimit {
					return errArtifactPreviewTooLarge
				}
				// File metadata can't identify bytes after an in-place edit or path replacement.
				if artifactOutputDigest(data) != task.OutputHash {
					return errArtifactPreviewChanged
				}
				return nil
			})
			if err != nil {
				return nil, err
			}
			return data, nil
		}
	}
	return nil, errArtifactPreviewUnavailable
}

func (a *App) handleArtifactPreview(window application.Window, raw string, info *application.OriginInfo) {
	taskID, requestID, ok := strings.Cut(raw, ":")
	if !ok || !validDestructiveID(taskID) || !validDestructiveID(requestID) || a.window == nil || window != a.window {
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
	go func() {
		data, err := a.docx.previewArtifact(session, taskID)
		event := map[string]any{"request_id": requestID, "device_id": session.DeviceID}
		switch {
		case err == nil:
			event["data"] = data
		case errors.Is(err, errArtifactPreviewTooLarge):
			event["error"] = errArtifactPreviewTooLarge.Error()
		case errors.Is(err, errArtifactPreviewChanged):
			event["error"] = errArtifactPreviewChanged.Error()
		default:
			event["error"] = errArtifactPreviewUnavailable.Error()
		}
		a.emitDashboardEvent(session, origin, "colink:artifact-preview", event)
	}()
}
