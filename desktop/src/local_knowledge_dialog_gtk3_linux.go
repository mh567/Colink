//go:build gtk3 && cgo

package main

/*
#cgo pkg-config: gtk+-3.0
#include <gtk/gtk.h>
#include <stdlib.h>

static GtkWidget *new_knowledge_confirmation(GtkWindow *parent, const char *title, const char *message) {
	GtkWidget *widget = gtk_message_dialog_new(parent, GTK_DIALOG_MODAL | GTK_DIALOG_DESTROY_WITH_PARENT,
		GTK_MESSAGE_QUESTION, GTK_BUTTONS_NONE, "%s", message);
	GtkDialog *dialog = GTK_DIALOG(widget);
	gtk_window_set_title(GTK_WINDOW(widget), title);
	GtkWidget *no = gtk_dialog_add_button(dialog, "No", GTK_RESPONSE_NO);
	gtk_dialog_add_button(dialog, "Yes", GTK_RESPONSE_YES);
	gtk_dialog_set_default_response(dialog, GTK_RESPONSE_NO);
	gtk_widget_grab_focus(no);
	return g_object_ref_sink(widget);
}
*/
import "C"

import (
	"context"
	"unsafe"

	"github.com/wailsapp/wails/v3/pkg/application"
)

// Wails beta.13 drops GTK3's dismissal response instead of calling the cancel button.
func (a *App) showKnowledgeImportConfirmation(ctx context.Context, title, message string, accept, decline func()) {
	application.InvokeAsync(func() {
		if ctx.Err() != nil {
			return
		}
		parent := a.window.NativeWindow()
		if parent == nil {
			decline()
			return
		}
		cTitle, cMessage := C.CString(title), C.CString(message)
		widget := C.new_knowledge_confirmation((*C.GtkWindow)(parent), cTitle, cMessage)
		C.free(unsafe.Pointer(cTitle))
		C.free(unsafe.Pointer(cMessage))
		defer C.g_object_unref(C.gpointer(widget))

		dialog := (*C.GtkDialog)(unsafe.Pointer(widget))
		stop := context.AfterFunc(ctx, func() {
			application.InvokeAsync(func() {
				if dialog != nil {
					C.gtk_dialog_response(dialog, C.GTK_RESPONSE_CANCEL)
				}
			})
		})
		response := C.gtk_dialog_run(dialog)
		dialog = nil
		stop()
		C.gtk_widget_destroy(widget)
		if response == C.GTK_RESPONSE_YES {
			accept()
		} else {
			decline()
		}
	})
}
