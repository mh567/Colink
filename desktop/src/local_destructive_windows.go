package main

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"unsafe"

	"golang.org/x/sys/windows"
)

type windowsDestructiveVersion struct {
	volume, indexHigh, indexLow uint32
	attributes                  uint32
	created, modified           windows.Filetime
	digest                      [sha256.Size]byte
}

type windowsDestructiveTarget struct {
	mu                 sync.Mutex
	directory          *os.File
	path, name, action string
	version            windowsDestructiveVersion
	output             [sha256.Size]byte
	prompted, approved bool
}

func prepareDestructiveTarget(s docxSession, root *os.Root, name, operation string) (destructiveTarget, error) {
	if !isDestructiveAction(operation) {
		return nil, fileopsCopyError(copyFileOperationFailed)
	}
	if err := s.Context.Err(); err != nil {
		return nil, err
	}
	if root == nil || !filepath.IsLocal(name) || filepath.Base(name) != name || strings.ContainsAny(name, "/\\:\x00") {
		return nil, fileopsCopyError(copyFileOperationFailed)
	}
	path, err := filepath.Abs(filepath.Join(root.Name(), name))
	if err != nil {
		return nil, windowsDestructiveOperationError(err)
	}
	directory, err := root.Open(".")
	if err != nil {
		return nil, windowsDestructiveOperationError(err)
	}
	target := &windowsDestructiveTarget{directory: directory, path: path, name: name, action: operation}
	if err = target.checkDirectory(); err != nil {
		target.close()
		return nil, err
	}
	file, err := openWindowsDestructiveFile(directory, name, operation)
	if err != nil {
		target.close()
		return nil, windowsDestructiveOperationError(err)
	}
	target.version, err = snapshotWindowsDestructiveFile(file)
	if closeErr := file.Close(); err == nil && closeErr != nil {
		err = windowsDestructiveOperationError(closeErr)
	}
	if err == nil && operation == "overwrite" && target.version.attributes&windows.FILE_ATTRIBUTE_READONLY != 0 {
		err = fileopsCopyError(copyFileOperationFailed)
	}
	if err != nil {
		target.close()
		return nil, err
	}
	return target, nil
}

func windowsDestructiveOperationError(err error) error {
	if err == nil {
		return nil
	}
	return errors.Join(fileopsCopyError(copyFileOperationFailed), err)
}

func openWindowsDestructiveFile(directory *os.File, name, operation string) (*os.File, error) {
	access := uint32(windows.FILE_GENERIC_READ | windows.DELETE)
	if operation == "overwrite" {
		access |= windows.FILE_WRITE_DATA
	}
	return openWindowsFileForMutation(directory, name, access, windows.FILE_OPEN)
}

func openWindowsFileForMutation(directory *os.File, name string, access, disposition uint32) (*os.File, error) {
	objectName, err := windows.NewNTUnicodeString(name)
	if err != nil {
		return nil, err
	}
	attrs := windows.OBJECT_ATTRIBUTES{
		RootDirectory: windows.Handle(directory.Fd()),
		ObjectName:    objectName,
		Attributes:    windows.OBJ_CASE_INSENSITIVE,
	}
	attrs.Length = uint32(unsafe.Sizeof(attrs))
	var handle windows.Handle
	// The exclusive handle keeps the directory entry stable during mutation.
	err = windows.NtCreateFile(&handle, access, &attrs, &windows.IO_STATUS_BLOCK{}, nil, 0, 0,
		disposition, windows.FILE_NON_DIRECTORY_FILE|windows.FILE_OPEN_REPARSE_POINT|windows.FILE_SYNCHRONOUS_IO_NONALERT, 0, 0)
	runtime.KeepAlive(directory)
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(handle), name), nil
}

func snapshotWindowsDestructiveFile(file *os.File) (windowsDestructiveVersion, error) {
	var info windows.ByHandleFileInformation
	if err := windows.GetFileInformationByHandle(windows.Handle(file.Fd()), &info); err != nil {
		return windowsDestructiveVersion{}, windowsDestructiveOperationError(err)
	}
	if info.FileAttributes&(windows.FILE_ATTRIBUTE_REPARSE_POINT|windows.FILE_ATTRIBUTE_DIRECTORY) != 0 || info.NumberOfLinks != 1 {
		return windowsDestructiveVersion{}, fileopsCopyError(copyFileOperationFailed)
	}
	size := uint64(info.FileSizeHigh)<<32 | uint64(info.FileSizeLow)
	if size > docxFileLimit {
		return windowsDestructiveVersion{}, fileopsCopyError(copyFileOperationFailed)
	}
	digest := sha256.New()
	read, err := io.Copy(digest, io.LimitReader(file, docxFileLimit+1))
	if err != nil {
		return windowsDestructiveVersion{}, windowsDestructiveOperationError(err)
	}
	if uint64(read) != size {
		return windowsDestructiveVersion{}, errDestructiveTargetChanged
	}
	version := windowsDestructiveVersion{
		volume: info.VolumeSerialNumber, indexHigh: info.FileIndexHigh, indexLow: info.FileIndexLow,
		attributes: info.FileAttributes, created: info.CreationTime, modified: info.LastWriteTime,
	}
	copy(version.digest[:], digest.Sum(nil))
	return version, nil
}

func (t *windowsDestructiveTarget) checkDirectory() error {
	actual, err := t.directory.Stat()
	if err != nil {
		return windowsDestructiveOperationError(err)
	}
	displayed, err := os.Lstat(filepath.Dir(t.path))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return errDestructiveTargetChanged
		}
		return windowsDestructiveOperationError(err)
	}
	if !displayed.IsDir() || !os.SameFile(actual, displayed) {
		return errDestructiveTargetChanged
	}
	return nil
}

func (t *windowsDestructiveTarget) confirm(ctx context.Context, prompt func(context.Context, string, string) (bool, error), data []byte) error {
	if len(data) > docxFileLimit || (t.action == "delete" && len(data) != 0) {
		return fileopsCopyError(copyFileOperationFailed)
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	t.mu.Lock()
	if t.prompted || t.directory == nil {
		t.mu.Unlock()
		return errDestructiveCancelled
	}
	t.prompted = true
	t.output = sha256.Sum256(data)
	t.mu.Unlock()
	if prompt == nil {
		return errDestructiveCancelled
	}
	accepted, err := prompt(ctx, t.action, t.path)
	if err != nil {
		return err
	}
	if !accepted {
		return errDestructiveCancelled
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if err = ctx.Err(); err != nil {
		return err
	}
	if t.directory == nil {
		return errDestructiveCancelled
	}
	t.approved = true
	return nil
}

func (t *windowsDestructiveTarget) commit(ctx context.Context, data []byte, s docxSession) (err error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if !t.approved || t.directory == nil {
		return errDestructiveCancelled
	}
	t.approved = false
	if len(data) > docxFileLimit || sha256.Sum256(data) != t.output {
		return fileopsCopyError(copyFileInvalidIntent)
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	file, err := openWindowsDestructiveFile(t.directory, t.name, t.action)
	if err != nil {
		var status windows.NTStatus
		if errors.As(err, &status) && errors.Is(status.Errno(), os.ErrNotExist) {
			return errDestructiveTargetChanged
		}
		return windowsDestructiveOperationError(err)
	}
	defer func() {
		if closeErr := file.Close(); err == nil && closeErr != nil {
			err = windowsDestructiveOperationError(closeErr)
		}
	}()
	version, err := snapshotWindowsDestructiveFile(file)
	if err != nil {
		return err
	}
	if version != t.version {
		return errDestructiveTargetChanged
	}
	if err = t.checkDirectory(); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	if t.action == "overwrite" {
		return windowsDestructiveOperationError(t.replace(ctx, file, data, s.RecordRecovery))
	}
	return windowsDestructiveOperationError(deleteWindowsFile(file))
}

func (t *windowsDestructiveTarget) replace(ctx context.Context, original *os.File, data []byte, recordRecovery func(string) error) error {
	const securityInfo = windows.OWNER_SECURITY_INFORMATION | windows.GROUP_SECURITY_INFORMATION | windows.DACL_SECURITY_INFORMATION
	security, err := windows.GetSecurityInfo(windows.Handle(original.Fd()), windows.SE_FILE_OBJECT, securityInfo)
	if err != nil {
		return fmt.Errorf("read target security descriptor: %w", err)
	}
	stage, err := openWindowsFileForMutation(t.directory, ".colink-replacement-"+localID(),
		windows.FILE_GENERIC_WRITE|windows.DELETE|windows.READ_CONTROL|windows.WRITE_DAC, windows.FILE_CREATE)
	if err != nil {
		return fmt.Errorf("create replacement: %w", err)
	}
	published := false
	defer func() {
		if !published {
			_ = deleteWindowsFile(stage)
		}
		_ = stage.Close()
	}()
	stageSecurity, err := windows.GetSecurityInfo(windows.Handle(stage.Fd()), windows.SE_FILE_OBJECT, securityInfo)
	if err != nil {
		return err
	}
	expected := security.String()
	if expected == "" {
		return windows.ERROR_ACCESS_DENIED
	}
	if stageSecurity.String() != expected {
		if err = matchWindowsFileAccess(stage, security); err != nil {
			return err
		}
		stageSecurity, err = windows.GetSecurityInfo(windows.Handle(stage.Fd()), windows.SE_FILE_OBJECT, securityInfo)
		if err != nil {
			return err
		}
		if stageSecurity.String() != expected {
			return windows.ERROR_ACCESS_DENIED
		}
	}
	if _, err = stage.Write(data); err != nil {
		return err
	}
	if err = stage.Sync(); err != nil {
		return err
	}

	backup := ".colink-original-" + localID() + filepath.Ext(t.name)
	backupKept := false
	if recordRecovery != nil {
		defer func() {
			if !backupKept {
				_ = recordRecovery("")
			}
		}()
		if err = recordRecovery(filepath.Join(filepath.Dir(t.path), backup)); err != nil {
			return err
		}
	}
	if err = t.checkDirectory(); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	// Neither rename can replace a new entry created by another process.
	if err = renameWindowsFile(t.directory, original, backup); err != nil {
		return fmt.Errorf("move target to backup: %w", err)
	}
	backupKept = true
	if err = renameWindowsFile(t.directory, stage, t.name); err != nil {
		restoreErr := renameWindowsFile(t.directory, original, t.name)
		backupKept = restoreErr != nil
		return errors.Join(err, restoreErr)
	}
	published = true
	err = deleteWindowsFile(original)
	backupKept = err != nil
	return err
}

func matchWindowsFileAccess(file *os.File, security *windows.SECURITY_DESCRIPTOR) error {
	dacl, _, err := security.DACL()
	if err != nil {
		return err
	}
	control, _, err := security.Control()
	if err != nil {
		return err
	}
	flags := windows.SECURITY_INFORMATION(windows.DACL_SECURITY_INFORMATION | windows.UNPROTECTED_DACL_SECURITY_INFORMATION)
	if control&windows.SE_DACL_PROTECTED != 0 {
		flags = windows.DACL_SECURITY_INFORMATION | windows.PROTECTED_DACL_SECURITY_INFORMATION
	}
	err = windows.SetSecurityInfo(windows.Handle(file.Fd()), windows.SE_FILE_OBJECT, flags, nil, nil, dacl, nil)
	runtime.KeepAlive(file)
	return err
}

func renameWindowsFile(directory, file *os.File, name string) error {
	utf16Name, err := windows.UTF16FromString(name)
	if err != nil {
		return err
	}
	// The native structure is followed by the UTF-16 name, without its terminator.
	info := struct {
		flags      uint32
		root       windows.Handle
		nameLength uint32
		name       [256]uint16
	}{root: windows.Handle(directory.Fd()), nameLength: uint32(2 * (len(utf16Name) - 1))}
	if len(utf16Name) > len(info.name) {
		return windows.ERROR_FILENAME_EXCED_RANGE
	}
	copy(info.name[:], utf16Name)
	err = windows.NtSetInformationFile(windows.Handle(file.Fd()), &windows.IO_STATUS_BLOCK{},
		(*byte)(unsafe.Pointer(&info)), uint32(unsafe.Offsetof(info.name))+info.nameLength, windows.FileRenameInformation)
	runtime.KeepAlive(directory)
	runtime.KeepAlive(file)
	return err
}

func deleteWindowsFile(file *os.File) error {
	deleteFile := byte(1)
	err := windows.SetFileInformationByHandle(windows.Handle(file.Fd()), windows.FileDispositionInfo, &deleteFile, 1)
	runtime.KeepAlive(file)
	return err
}

func (t *windowsDestructiveTarget) close() {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.directory != nil {
		t.directory.Close()
		t.directory = nil
	}
	t.approved = false
}
