package main

import (
	"errors"
	"log"
	"net/http"
	"path/filepath"
	"slices"
)

type fileAuditReceipt struct {
	RequestID string `json:"request_id"`
	Event     string `json:"event"`
	Format    string `json:"format"`
	Operation string `json:"operation"`
	Path      string `json:"path"`
	ErrorCode string `json:"error_code,omitempty"`
}

func (s docxSession) fileAudit(event, operation string) fileAuditReceipt {
	r := fileAuditReceipt{
		RequestID: s.AuditRequestID,
		Event:     event,
		Format:    s.Format,
		Operation: operation,
		Path:      filepath.Base(s.AuditPath),
	}
	if event == "failed" {
		r.ErrorCode = "failed"
	}
	return r
}

func (w *localDOCX) updateState(s docxSession, update func(*docxState)) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	states, err := w.load()
	if err != nil {
		return err
	}
	state := states[s.scope()]
	update(&state)
	states[s.scope()] = state
	return w.save(states)
}

func (w *localDOCX) enqueueFileAudit(s docxSession, receipt fileAuditReceipt) error {
	return w.updateState(s, func(state *docxState) {
		state.PendingFileAudit = append(state.PendingFileAudit, receipt)
	})
}

func (w *localDOCX) retryFileAudit(s docxSession) {
	if !w.auditMu.TryLock() {
		return
	}
	defer w.auditMu.Unlock()
	w.mu.Lock()
	states, err := w.load()
	w.mu.Unlock()
	if err != nil {
		log.Print("Local file audit queue could not be read")
		return
	}
	blocked := make(map[string]bool)
	for _, receipt := range states[s.scope()].PendingFileAudit {
		if blocked[receipt.RequestID] {
			continue
		}
		var reply struct {
			Recorded bool `json:"recorded"`
		}
		if err := deviceTaskCall(s, "file-events", "", receipt, &reply); err != nil || !reply.Recorded {
			log.Print("Local file audit delivery failed; receipt kept for retry")
			var response *deviceTaskResponseError
			if errors.As(err, &response) && response.status == http.StatusConflict {
				blocked[receipt.RequestID] = true
				continue
			}
			return
		}
		if err := w.updateState(s, func(state *docxState) {
			state.PendingFileAudit = slices.DeleteFunc(state.PendingFileAudit, func(pending fileAuditReceipt) bool {
				return pending == receipt
			})
		}); err != nil {
			log.Print("Local file audit acknowledgement could not be saved; receipt kept for retry")
			return
		}
	}
}
