package main

import (
	"context"
	"crypto/sha256"
	"errors"
	"net/url"
	"os"
	"os/user"
	"strconv"
	"strings"
	"time"

	"github.com/godbus/dbus/v5"

	"octop.desktop/internal/fileops"
)

const errLinuxManagedUnavailable = fileopsCopyError(copyFileManagedUnavailable)

type linuxDestructiveTarget struct {
	conn      *dbus.Conn
	object    dbus.BusObject
	token     string
	operation string
	session   docxSession
}

func connectLinuxFileops(ctx context.Context) (*dbus.Conn, dbus.BusObject, error) {
	account, err := user.Lookup("colink-fileops")
	if err != nil || account.HomeDir != "/nonexistent" {
		return nil, nil, errLinuxManagedUnavailable
	}
	expected, err := strconv.ParseUint(account.Uid, 10, 32)
	if err != nil || expected == 0 {
		return nil, nil, errLinuxManagedUnavailable
	}
	conn, err := dbus.Connect(fileops.SystemBusAddress)
	if err != nil {
		return nil, nil, errLinuxManagedUnavailable
	}
	lookupCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	var owner string
	var uid uint32
	bus := conn.BusObject()
	if bus.CallWithContext(lookupCtx, "org.freedesktop.DBus.GetNameOwner", 0, fileops.BusName).Store(&owner) != nil ||
		!strings.HasPrefix(owner, ":") ||
		bus.CallWithContext(lookupCtx, "org.freedesktop.DBus.GetConnectionUnixUser", 0, owner).Store(&uid) != nil ||
		uid != uint32(expected) {
		conn.Close()
		return nil, nil, errLinuxManagedUnavailable
	}
	return conn, conn.Object(owner, dbus.ObjectPath(fileops.BusPath)), nil
}

func managedProof(s docxSession) (string, fileops.Credentials, error) {
	var proof fileops.Credentials
	server, err := url.Parse(s.Base)
	if err != nil {
		return "", proof, fileopsCopyError(copyFileManagedDenied)
	}
	const connectPath = "/api/devices/connect"
	apiPath := s.APIPath
	if apiPath == "" {
		apiPath = connectPath
	}
	endpoint, err := url.ParseRequestURI(apiPath)
	if err != nil || endpoint.IsAbs() || endpoint.Host != "" || endpoint.RawQuery != "" || endpoint.ForceQuery ||
		!strings.HasPrefix(endpoint.Path, "/") || strings.HasPrefix(endpoint.Path, "//") || !strings.HasSuffix(endpoint.Path, connectPath) {
		return "", proof, fileopsCopyError(copyFileManagedDenied)
	}
	server.Path, server.RawPath = strings.TrimSuffix(endpoint.Path, connectPath), ""
	base, err := fileops.NormalizeServerURL(server.String())
	if err != nil {
		return "", proof, fileopsCopyError(copyFileManagedDenied)
	}
	format := s.Format
	if format == "" {
		format = "docx"
	}
	return base, fileops.Credentials{Format: format, DeviceID: s.DeviceID, Bearer: s.Token, Session: s.Secret}, nil
}

func linuxManagedDirectory(s docxSession) (string, error) {
	s, err := refreshDOCXSession(s)
	if err != nil {
		return "", err
	}
	base, proof, err := managedProof(s)
	if err != nil {
		return "", err
	}
	conn, object, err := connectLinuxFileops(s.Context)
	if err != nil {
		return "", errLinuxManagedUnavailable
	}
	defer conn.Close()
	ctx, cancel := context.WithTimeout(s.Context, 25*time.Second)
	defer cancel()
	var directory string
	err = object.CallWithContext(ctx, fileops.BusName+".Directory", 0, base, proof).Store(&directory)
	if err != nil {
		return "", linuxFileopsError(err)
	}
	if directory != fileops.DirectoryForUID(uint32(os.Getuid())) {
		return "", errLinuxManagedUnavailable
	}
	return directory, nil
}

func managedRootIdentity(root *os.Root) error {
	if !fileops.SameDirectory(root, root.Name()) {
		return fileopsCopyError(copyFileDirectoryChanged)
	}
	return nil
}

func prepareDestructiveTarget(s docxSession, root *os.Root, name, operation string) (destructiveTarget, error) {
	if root.Name() != fileops.DirectoryForUID(uint32(os.Getuid())) {
		return nil, fileopsCopyError(copyFileManagedRequired)
	}
	if err := managedRootIdentity(root); err != nil {
		return nil, err
	}
	s, err := refreshDOCXSession(s)
	if err != nil {
		return nil, err
	}
	base, proof, err := managedProof(s)
	if err != nil {
		return nil, err
	}
	conn, object, err := connectLinuxFileops(s.Context)
	if err != nil {
		return nil, errLinuxManagedUnavailable
	}
	ctx, cancel := context.WithTimeout(s.Context, 15*time.Second)
	defer cancel()
	var token string
	err = object.CallWithContext(ctx, fileops.BusName+".Prepare", 0, base, operation, name, proof).Store(&token)
	if err != nil || token == "" {
		conn.Close()
		if err == nil {
			return nil, errLinuxManagedUnavailable
		}
		return nil, linuxFileopsError(err)
	}
	return &linuxDestructiveTarget{conn: conn, object: object, token: token, operation: operation, session: s}, nil
}

func (t *linuxDestructiveTarget) confirm(ctx context.Context, _ func(context.Context, string, string) (bool, error), data []byte) error {
	s, err := refreshDOCXSession(t.session)
	if err != nil {
		return err
	}
	base, proof, err := managedProof(s)
	if err != nil {
		return err
	}
	var digest []byte
	if t.operation == "overwrite" || len(data) != 0 {
		sum := sha256.Sum256(data)
		digest = sum[:]
	}
	return linuxFileopsError(t.object.CallWithContext(ctx, fileops.BusName+".Confirm", 0, base, t.token, t.operation, proof, digest).Err)
}

func (t *linuxDestructiveTarget) commit(ctx context.Context, data []byte, s docxSession) error {
	base, proof, err := managedProof(s)
	if err != nil {
		return err
	}
	return linuxFileopsError(t.object.CallWithContext(ctx, fileops.BusName+".Commit", 0, base, t.token, t.operation, proof, data).Err)
}

func (t *linuxDestructiveTarget) close() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_ = t.object.CallWithContext(ctx, fileops.BusName+".Abort", 0, t.token).Err
	_ = t.conn.Close()
}

func createLocalOutput(s docxSession, root *os.Root, name string, data []byte) (bool, error) {
	if root.Name() != fileops.DirectoryForUID(uint32(os.Getuid())) {
		return false, nil
	}
	if err := managedRootIdentity(root); err != nil {
		return true, err
	}
	s, err := refreshDOCXSession(s)
	if err != nil {
		return true, err
	}
	base, proof, err := managedProof(s)
	if err != nil {
		return true, err
	}
	conn, object, err := connectLinuxFileops(s.Context)
	if err != nil {
		return true, errLinuxManagedUnavailable
	}
	defer conn.Close()
	ctx, cancel := context.WithTimeout(s.Context, 15*time.Second)
	defer cancel()
	err = object.CallWithContext(ctx, fileops.BusName+".Create", 0, base, name, proof, data).Err
	return true, linuxFileopsError(err)
}

func linuxFileopsError(err error) error {
	if err == nil || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return err
	}
	var busError dbus.Error
	var busErrorPointer *dbus.Error
	if errors.As(err, &busErrorPointer) {
		busError = *busErrorPointer
	} else {
		_ = errors.As(err, &busError)
	}
	switch busError.Name {
	case fileops.CancelError:
		return errDestructiveCancelled
	case fileops.ChangedError:
		return errDestructiveTargetChanged
	case fileops.ExistsError:
		return fileopsCopyError(copyFileTargetExists)
	case fileops.PermissionError:
		return fileopsCopyError(copyFileManagedDenied)
	case "org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner", "org.freedesktop.DBus.Error.UnknownMethod":
		return errLinuxManagedUnavailable
	default:
		return fileopsCopyError(copyFileManagedFailed)
	}
}
