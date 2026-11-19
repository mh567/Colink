//go:build !linux || !gtk3

package main

import (
	"context"
	"runtime"

	"github.com/wailsapp/wails/v3/pkg/application"
)

func (a *App) showKnowledgeImportConfirmation(ctx context.Context, title, message string, accept, decline func()) {
	application.InvokeAsync(func() {
		if ctx.Err() != nil {
			return
		}
		dialog := a.app.Dialog.Question().SetTitle(title).SetMessage(message).AttachToWindow(a.window)
		dialog.AddButton("Yes").OnClick(accept)
		no := dialog.AddButton("No").SetAsCancel().OnClick(decline)
		// Wails binds Escape on macOS only if the cancel button isn't also default.
		if runtime.GOOS != "darwin" {
			dialog.SetDefaultButton(no)
		}
		dialog.Show()
	})
}
