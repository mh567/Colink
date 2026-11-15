//go:build linux && cgo && gtk3 && webkit238

// Package webkit238 provides the WebKitGTK 2.40 entry points that Wails calls
// when built with the gtk3 tag. Kylin V10 SP1 2503 ships WebKitGTK 2.38.6,
// which lacks those symbols; this package lets the shell link and run there.
//
// The 2.38 build cannot read custom-scheme request bodies, so the bundled
// pages must not call the HTTP runtime transport. The shell UI reaches Go
// through the native IPC bridge instead.
package webkit238

/*
#cgo linux pkg-config: webkit2gtk-4.0
#include <webkit2/webkit2.h>
*/
import "C"
