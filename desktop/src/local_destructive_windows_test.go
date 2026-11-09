package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"unsafe"

	"golang.org/x/sys/windows"
)

const windowsOriginalContents = "original payload"

func makeWindowsDestructiveFixture(t *testing.T) (*os.Root, string) {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte(windowsOriginalContents), 0600); err != nil {
		t.Fatal(err)
	}
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { root.Close() })
	return root, path
}

func assertWindowsFileContents(t *testing.T, path, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil || string(data) != want {
		t.Fatalf("file = %q, %v; want %q", data, err, want)
	}
}

func TestWindowsOverwriteOnlyCommitsTheConfirmedFileAndBytes(t *testing.T) {
	for _, scenario := range []string{"accepted", "cancelled", "changed bytes", "replaced target"} {
		t.Run(scenario, func(t *testing.T) {
			ctx := context.Background()
			session := docxSession{Context: ctx}
			root, path := makeWindowsDestructiveFixture(t)
			target, err := prepareDestructiveTarget(session, root, filepath.Base(path), "overwrite")
			if err != nil {
				t.Fatal(err)
			}
			defer target.close()
			data := []byte("updated")
			if err = target.commit(ctx, data, session); err == nil {
				t.Fatal("overwrote without approval")
			}
			err = target.confirm(ctx, func(_ context.Context, operation, targetPath string) (bool, error) {
				if operation != "overwrite" || targetPath != path {
					t.Fatalf("prompt for %s %q", operation, targetPath)
				}
				assertWindowsFileContents(t, path, windowsOriginalContents)
				return scenario != "cancelled", nil
			}, data)
			if scenario == "cancelled" {
				if !errors.Is(err, errDestructiveCancelled) || target.commit(ctx, data, session) == nil {
					t.Fatal("overwrite could proceed after cancellation")
				}
				assertWindowsFileContents(t, path, windowsOriginalContents)
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			want := windowsOriginalContents
			switch scenario {
			case "changed bytes":
				data = []byte("different content")
			case "replaced target":
				moved := filepath.Join(root.Name(), "moved.docx")
				if err = os.Rename(path, moved); err != nil {
					t.Fatal(err)
				}
				want = "another document"
				if err = os.WriteFile(path, []byte(want), 0600); err != nil {
					t.Fatal(err)
				}
				defer assertWindowsFileContents(t, moved, windowsOriginalContents)
			}
			err = target.commit(ctx, data, session)
			if scenario == "accepted" {
				if err != nil {
					t.Fatal(err)
				}
				want = "updated"
				entries, listErr := os.ReadDir(root.Name())
				if listErr != nil || len(entries) != 1 {
					t.Fatalf("unexpected files after overwrite: %v, %v", entries, listErr)
				}
			} else if err == nil {
				t.Fatal("overwrite accepted a change to the approved operation")
			}
			if err = target.commit(ctx, data, session); err == nil {
				t.Fatal("reused the approval")
			}
			assertWindowsFileContents(t, path, want)
		})
	}
}

func TestWindowsOverwriteRejectsAReadOnlyFile(t *testing.T) {
	root, path := makeWindowsDestructiveFixture(t)
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = windows.SetFileAttributes(name, windows.FILE_ATTRIBUTE_READONLY); err != nil {
		t.Fatal(err)
	}
	defer windows.SetFileAttributes(name, windows.FILE_ATTRIBUTE_NORMAL)
	target, err := prepareDestructiveTarget(docxSession{Context: context.Background()}, root, filepath.Base(path), "overwrite")
	if target != nil {
		target.close()
	}
	if err == nil {
		t.Fatal("prepared an overwrite for a read-only file")
	}
	assertWindowsFileContents(t, path, windowsOriginalContents)
}

func TestWindowsRenameLeavesAnExistingDestinationUntouched(t *testing.T) {
	root, path := makeWindowsDestructiveFixture(t)
	destination := filepath.Join(root.Name(), "replacement.docx")
	if err := os.WriteFile(destination, []byte("unconfirmed document"), 0600); err != nil {
		t.Fatal(err)
	}
	directory, err := root.Open(".")
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	original, err := openWindowsDestructiveFile(directory, filepath.Base(path), "overwrite")
	if err != nil {
		t.Fatal(err)
	}
	renameErr := renameWindowsFile(directory, original, filepath.Base(destination))
	original.Close()
	if renameErr == nil {
		t.Fatal("rename replaced an unconfirmed document")
	}
	assertWindowsFileContents(t, path, windowsOriginalContents)
	assertWindowsFileContents(t, destination, "unconfirmed document")
}

func TestWindowsDestructiveTargetRequiresConfirmation(t *testing.T) {
	ctx := context.Background()
	session := docxSession{Context: ctx}
	root, path := makeWindowsDestructiveFixture(t)
	target, err := prepareDestructiveTarget(session, root, filepath.Base(path), "delete")
	if err != nil {
		t.Fatal(err)
	}
	defer target.close()
	if err = target.commit(ctx, nil, session); err == nil {
		t.Fatal("deleted without confirmation")
	}
	assertWindowsFileContents(t, path, windowsOriginalContents)
	if err = target.confirm(ctx, func(_ context.Context, action, fullPath string) (bool, error) {
		if action != "delete" || fullPath != path {
			t.Fatalf("prompt = %q, %q; want delete %q", action, fullPath, path)
		}
		return false, nil
	}, nil); !errors.Is(err, errDestructiveCancelled) {
		t.Fatalf("cancel = %v", err)
	}
	if err = target.commit(ctx, nil, session); err == nil {
		t.Fatal("deleted after cancellation")
	}
	assertWindowsFileContents(t, path, windowsOriginalContents)
}

func TestWindowsDestructiveTargetCommitsOnlyOnce(t *testing.T) {
	ctx := context.Background()
	session := docxSession{Context: ctx}
	root, path := makeWindowsDestructiveFixture(t)
	target, err := prepareDestructiveTarget(session, root, filepath.Base(path), "delete")
	if err != nil {
		t.Fatal(err)
	}
	defer target.close()
	if err = target.confirm(ctx, func(context.Context, string, string) (bool, error) { return true, nil }, nil); err != nil {
		t.Fatal(err)
	}
	if err = target.commit(ctx, nil, session); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("deleted path still present: %v", err)
	}
	if err = os.WriteFile(path, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = target.commit(ctx, nil, session); err == nil {
		t.Fatal("confirmation was reusable")
	}
	assertWindowsFileContents(t, path, "replacement")
}

func TestWindowsDestructiveTargetRejectsChangesAfterConfirmation(t *testing.T) {
	for _, change := range []string{"replaced", "replaced with same bytes", "removed", "edited", "hardlinked"} {
		t.Run(change, func(t *testing.T) {
			ctx := context.Background()
			session := docxSession{Context: ctx}
			root, path := makeWindowsDestructiveFixture(t)
			target, err := prepareDestructiveTarget(session, root, filepath.Base(path), "delete")
			if err != nil {
				t.Fatal(err)
			}
			defer target.close()
			if err = target.confirm(ctx, func(context.Context, string, string) (bool, error) { return true, nil }, nil); err != nil {
				t.Fatal(err)
			}
			other := filepath.Join(root.Name(), "other.docx")
			want := windowsOriginalContents
			switch change {
			case "replaced", "replaced with same bytes", "removed":
				if err = os.Rename(path, other); err != nil {
					t.Fatal(err)
				}
				if change == "replaced" {
					want = "replacement payload"
				}
				if change != "removed" {
					if err = os.WriteFile(path, []byte(want), 0600); err != nil {
						t.Fatal(err)
					}
				}
			case "edited":
				info, statErr := os.Stat(path)
				if statErr != nil {
					t.Fatal(statErr)
				}
				want = "modified payload"
				if err = os.WriteFile(path, []byte(want), 0600); err != nil {
					t.Fatal(err)
				}
				if err = os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
					t.Fatal(err)
				}
			case "hardlinked":
				if err = os.Link(path, other); err != nil {
					t.Fatal(err)
				}
			}
			wantErr := errDestructiveTargetChanged
			if change == "hardlinked" {
				wantErr = fileopsCopyError(copyFileOperationFailed)
			}
			if err = target.commit(ctx, nil, session); !errors.Is(err, wantErr) {
				t.Fatalf("changed target error: %v; want %v", err, wantErr)
			}
			if change == "removed" {
				if _, err = os.Stat(path); !errors.Is(err, os.ErrNotExist) {
					t.Fatalf("removed path was recreated: %v", err)
				}
			} else {
				assertWindowsFileContents(t, path, want)
			}
			if change != "edited" {
				assertWindowsFileContents(t, other, windowsOriginalContents)
			}
		})
	}
}

func TestWindowsDestructiveHandlePreventsPathReplacement(t *testing.T) {
	root, path := makeWindowsDestructiveFixture(t)
	directory, err := root.Open(".")
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	file, err := openWindowsDestructiveFile(directory, filepath.Base(path), "delete")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(path, filepath.Join(root.Name(), "moved.docx")); err == nil {
		file.Close()
		t.Fatal("exclusive mutation handle permitted rename")
	}
	if err = os.Remove(path); err == nil {
		file.Close()
		t.Fatal("exclusive mutation handle permitted removal")
	}
	if err = file.Close(); err != nil {
		t.Fatal(err)
	}
	assertWindowsFileContents(t, path, windowsOriginalContents)
}

func TestWindowsDestructiveHandlePreservesAliasAddedAfterSnapshot(t *testing.T) {
	root, path := makeWindowsDestructiveFixture(t)
	alias := filepath.Join(t.TempDir(), "alias.docx")
	directory, err := root.Open(".")
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	file, err := openWindowsDestructiveFile(directory, filepath.Base(path), "delete")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if _, err = snapshotWindowsDestructiveFile(file); err != nil {
		t.Fatal(err)
	}
	// A second name can appear outside the grant even after checking the locked file.
	if err = os.Link(path, alias); err != nil {
		t.Fatal(err)
	}
	deleteFile := byte(1)
	if err = windows.SetFileInformationByHandle(windows.Handle(file.Fd()), windows.FileDispositionInfo, &deleteFile, 1); err != nil {
		t.Fatal(err)
	}
	if err = file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("confirmed link was not deleted: %v", err)
	}
	assertWindowsFileContents(t, alias, windowsOriginalContents)
}

func TestWindowsDestructiveHandleBlocksPOSIXRenameOverDestination(t *testing.T) {
	root, path := makeWindowsDestructiveFixture(t)
	replacement := filepath.Join(root.Name(), "replacement.docx")
	if err := os.WriteFile(replacement, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	directory, err := root.Open(".")
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	file, err := openWindowsDestructiveFile(directory, filepath.Base(path), "delete")
	if err != nil {
		t.Fatal(err)
	}
	renameErr := tryWindowsPOSIXReplacement(replacement, path)
	if err = file.Close(); err != nil {
		t.Fatal(err)
	}
	if !errors.Is(renameErr, windows.ERROR_SHARING_VIOLATION) {
		t.Fatalf("rename over locked target: %v", renameErr)
	}
	assertWindowsFileContents(t, path, windowsOriginalContents)
	assertWindowsFileContents(t, replacement, "replacement")
	if err = tryWindowsPOSIXReplacement(replacement, path); err != nil {
		t.Fatalf("rename over unlocked target: %v", err)
	}
	assertWindowsFileContents(t, path, "replacement")
}

func tryWindowsPOSIXReplacement(source, target string) error {
	sourceName, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	handle, err := windows.CreateFile(sourceName, windows.DELETE,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE, nil, windows.OPEN_EXISTING, 0, 0)
	if err != nil {
		return err
	}
	defer windows.CloseHandle(handle)
	name, err := windows.UTF16FromString(target)
	if err != nil {
		return err
	}
	info := struct {
		flags      uint32
		root       windows.Handle
		nameLength uint32
		name       [32768]uint16
	}{flags: windows.FILE_RENAME_REPLACE_IF_EXISTS | windows.FILE_RENAME_POSIX_SEMANTICS, nameLength: uint32(2 * (len(name) - 1))}
	copy(info.name[:], name)
	return windows.SetFileInformationByHandle(handle, windows.FileRenameInfoEx, (*byte)(unsafe.Pointer(&info)), uint32(unsafe.Offsetof(info.name))+info.nameLength)
}
