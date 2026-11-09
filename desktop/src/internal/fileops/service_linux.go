package fileops

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/godbus/dbus/v5"
	"golang.org/x/sys/unix"
)

const (
	ChangedError     = BusName + ".TargetChanged"
	ExistsError      = BusName + ".TargetExists"
	PermissionError  = BusName + ".PermissionDenied"
	SystemBusAddress = "unix:path=/run/dbus/system_bus_socket"
	aclAttribute     = "system.posix_acl_access"
)

type service struct {
	conn          *dbus.Conn
	store         *store
	trustedServer string
	dirs          sync.Mutex
}

func Serve(ctx context.Context, serverURL string) error {
	if os.Geteuid() == 0 {
		return errInvalid
	}
	trustedServer, err := NormalizeServerURL(serverURL)
	if err != nil {
		return errPermission
	}
	serverAuth, err := NewServerAuthorizer(trustedServer)
	if err != nil {
		return errPermission
	}
	base, err := openBase()
	if err != nil {
		return err
	}
	base.Close()
	conn, err := dbus.Connect(SystemBusAddress)
	if err != nil {
		return err
	}
	defer conn.Close()
	svc := &service{conn: conn, trustedServer: trustedServer, store: newStore(verifyManagedFile, protectManagedFile, syncManagedDir, serverAuth)}
	if err = conn.Export(svc, dbus.ObjectPath(BusPath), BusName); err != nil {
		return err
	}
	signals := make(chan *dbus.Signal, 32)
	conn.Signal(signals)
	defer conn.RemoveSignal(signals)
	if err = conn.AddMatchSignal(dbus.WithMatchSender("org.freedesktop.DBus"), dbus.WithMatchInterface("org.freedesktop.DBus"), dbus.WithMatchMember("NameOwnerChanged")); err != nil {
		return err
	}
	owner, err := conn.RequestName(BusName, dbus.NameFlagDoNotQueue)
	if err != nil {
		return err
	}
	if owner != dbus.RequestNameReplyPrimaryOwner {
		return errInactive
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-conn.Context().Done():
			return errInactive
		case signal, ok := <-signals:
			if !ok {
				return errInactive
			}
			if signal == nil || signal.Sender != "org.freedesktop.DBus" || signal.Name != "org.freedesktop.DBus.NameOwnerChanged" || len(signal.Body) != 3 {
				continue
			}
			name, _ := signal.Body[0].(string)
			newOwner, _ := signal.Body[2].(string)
			if strings.HasPrefix(name, ":") && newOwner == "" {
				svc.store.disconnect(name)
			}
		}
	}
}

func fileError(err error) *dbus.Error {
	if err == nil {
		return nil
	}
	name := BusName + ".Failed"
	switch {
	case errors.Is(err, ErrCancelled):
		name = CancelError
	case errors.Is(err, errChanged):
		name = ChangedError
	case errors.Is(err, os.ErrExist):
		name = ExistsError
	case errors.Is(err, errPermission):
		name = PermissionError
	}
	return dbus.NewError(name, nil)
}

func (s *service) peerUID(sender dbus.Sender) (uint32, error) {
	var uid uint32
	if !strings.HasPrefix(string(sender), ":") {
		return 0, errInvalid
	}
	ctx, cancel := context.WithTimeout(s.conn.Context(), 5*time.Second)
	defer cancel()
	err := s.conn.BusObject().CallWithContext(ctx, "org.freedesktop.DBus.GetConnectionUnixUser", 0, string(sender)).Store(&uid)
	if err != nil || uid == 0 || uid == uint32(os.Geteuid()) {
		return 0, errInvalid
	}
	return uid, nil
}

func (s *service) verifiedUID(sender dbus.Sender, server string) (uint32, error) {
	url, err := NormalizeServerURL(server)
	if err != nil || url != s.trustedServer {
		return 0, errPermission
	}
	return s.peerUID(sender)
}

func (s *service) Directory(sender dbus.Sender, server string, proof Credentials) (string, *dbus.Error) {
	uid, err := s.verifiedUID(sender, server)
	if err != nil {
		return "", fileError(err)
	}
	ctx := s.conn.Context()
	for _, action := range []string{"read", "create"} {
		if err = s.store.checkPermission(ctx, proof, action); err != nil {
			return "", fileError(err)
		}
	}
	root, err := s.openManaged(uid, true)
	if err != nil {
		return "", fileError(err)
	}
	defer root.Close()
	return root.Name(), nil
}

func (s *service) Prepare(sender dbus.Sender, server, operation, name string, proof Credentials) (string, *dbus.Error) {
	uid, err := s.verifiedUID(sender, server)
	if err != nil {
		return "", fileError(err)
	}
	root, err := s.openManaged(uid, false)
	if err != nil {
		return "", fileError(err)
	}
	token, err := s.store.prepare(s.conn.Context(), string(sender), uid, root, name, operation, proof)
	if err != nil {
		root.Close()
	}
	return token, fileError(err)
}

func (s *service) Confirm(sender dbus.Sender, server, token, operation string, proof Credentials, digest []byte) *dbus.Error {
	if _, err := s.verifiedUID(sender, server); err != nil {
		s.store.abort(string(sender), token)
		return fileError(err)
	}
	return fileError(s.store.confirm(s.conn.Context(), string(sender), token, operation, proof, digest, func(ctx context.Context, operation, path string) (bool, error) {
		return s.authorize(ctx, sender, operation, path)
	}))
}

func (s *service) Commit(sender dbus.Sender, server, token, operation string, proof Credentials, data []byte) *dbus.Error {
	if _, err := s.verifiedUID(sender, server); err != nil {
		s.store.abort(string(sender), token)
		return fileError(err)
	}
	ctx, cancel := context.WithTimeout(s.conn.Context(), 12*time.Second)
	defer cancel()
	return fileError(s.store.commit(ctx, string(sender), token, operation, proof, data))
}

func (s *service) Abort(sender dbus.Sender, token string) *dbus.Error {
	s.store.abort(string(sender), token)
	return nil
}

func (s *service) Create(sender dbus.Sender, server, name string, proof Credentials, data []byte) *dbus.Error {
	uid, err := s.verifiedUID(sender, server)
	if err != nil {
		return fileError(err)
	}
	root, err := s.openManaged(uid, false)
	if err != nil {
		return fileError(err)
	}
	defer root.Close()
	return fileError(s.store.create(s.conn.Context(), root, uid, name, data, proof))
}

func (s *service) authorize(ctx context.Context, sender dbus.Sender, operation, path string) (bool, error) {
	action := OverwriteAction
	if operation == "delete" {
		action = DeleteAction
	}
	subject := struct {
		Kind    string
		Details map[string]dbus.Variant
	}{"system-bus-name", map[string]dbus.Variant{"name": dbus.MakeVariant(string(sender))}}
	details := promptDetails(operation, path)
	authority := s.conn.Object("org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority")
	check := "org.freedesktop.PolicyKit1.Authority.CheckAuthorization"
	var result struct {
		Authorized, Challenge bool
		Details               map[string]string
	}
	// Require an interactive challenge even if a local rule grants implicit authorization.
	if err := authority.CallWithContext(ctx, check, 0, subject, action, details, uint32(0), "").Store(&result); err != nil || result.Authorized || !result.Challenge {
		return false, errInactive
	}
	id := rand.Text()
	err := authority.CallWithContext(ctx, check, 0, subject, action, details, uint32(1), id).Store(&result)
	if ctx.Err() != nil {
		cancelCtx, cancel := context.WithTimeout(s.conn.Context(), 5*time.Second)
		defer cancel()
		_ = authority.CallWithContext(cancelCtx, "org.freedesktop.PolicyKit1.Authority.CancelCheckAuthorization", 0, id).Err
		return false, ErrCancelled
	}
	if err != nil || result.Details["polkit.temporary_authorization_id"] != "" || result.Details["polkit.retains_authorization_after_challenge"] != "" {
		return false, errInactive
	}
	if result.Authorized || result.Details["polkit.dismissed"] != "" {
		return result.Authorized, nil
	}
	return false, errInactive
}

func openBase() (*os.Root, error) {
	info, err := os.Lstat(ManagedRoot)
	if err != nil || !info.IsDir() {
		return nil, errInactive
	}
	root, err := os.OpenRoot(ManagedRoot)
	if err != nil {
		return nil, err
	}
	f, err := root.Open(".")
	if err == nil {
		err = verifyDirectory(f, 0711)
		f.Close()
	}
	if err != nil {
		root.Close()
		return nil, err
	}
	return root, nil
}

func (s *service) openManaged(uid uint32, provision bool) (*os.Root, error) {
	s.dirs.Lock()
	defer s.dirs.Unlock()
	base, err := openBase()
	if err != nil {
		return nil, err
	}
	defer base.Close()
	name := strconv.FormatUint(uint64(uid), 10)
	info, err := base.Lstat(name)
	if errors.Is(err, os.ErrNotExist) && provision {
		if err = base.Mkdir(name, 0700); err != nil {
			return nil, err
		}
		f, openErr := base.Open(name)
		if openErr == nil {
			openErr = unix.Fsetxattr(int(f.Fd()), aclAttribute, accessACL(uid, 7, 5), 0)
			f.Close()
		}
		if openErr != nil {
			_ = base.Remove(name)
			return nil, openErr
		}
		info, err = base.Lstat(name)
	}
	if err != nil || !info.IsDir() {
		return nil, errInactive
	}
	f, err := base.Open(name)
	if err != nil {
		return nil, err
	}
	if err = verifyDirectory(f, 0750); err == nil {
		err = verifyACL(f, accessACL(uid, 7, 5))
	}
	f.Close()
	if err != nil {
		return nil, err
	}
	return base.OpenRoot(name)
}

func verifyDirectory(f *os.File, mode uint32) error {
	var info unix.Stat_t
	if err := unix.Fstat(int(f.Fd()), &info); err != nil {
		return err
	}
	if info.Uid != uint32(os.Geteuid()) || info.Mode&unix.S_IFMT != unix.S_IFDIR || uint32(info.Mode)&07777 != mode {
		return errInactive
	}
	return nil
}

func protectManagedFile(f *os.File, uid uint32) error {
	return unix.Fsetxattr(int(f.Fd()), aclAttribute, accessACL(uid, 6, 4), 0)
}

func verifyManagedFile(f *os.File, uid uint32) error {
	var info unix.Stat_t
	if err := unix.Fstat(int(f.Fd()), &info); err != nil {
		return err
	}
	if info.Uid != uint32(os.Geteuid()) || info.Nlink != 1 || info.Mode&unix.S_IFMT != unix.S_IFREG || info.Mode&0777 != 0640 {
		return errInvalid
	}
	return verifyACL(f, accessACL(uid, 6, 4))
}

func verifyACL(f *os.File, expected []byte) error {
	actual := make([]byte, len(expected))
	n, err := unix.Fgetxattr(int(f.Fd()), aclAttribute, actual)
	if err != nil || n != len(expected) || !bytes.Equal(actual, expected) {
		return errInvalid
	}
	return nil
}

func accessACL(uid uint32, owner, reader uint16) []byte {
	// Linux POSIX ACL xattrs use version 2 and little-endian tag, permission, id entries.
	const undefinedID = ^uint32(0)
	entries := []struct {
		tag, perm uint16
		id        uint32
	}{{1, owner, undefinedID}, {2, reader, uid}, {4, 0, undefinedID}, {16, reader, undefinedID}, {32, 0, undefinedID}}
	data := make([]byte, 4+8*len(entries))
	binary.LittleEndian.PutUint32(data, 2)
	for i, entry := range entries {
		start := 4 + i*8
		binary.LittleEndian.PutUint16(data[start:], entry.tag)
		binary.LittleEndian.PutUint16(data[start+2:], entry.perm)
		binary.LittleEndian.PutUint32(data[start+4:], entry.id)
	}
	return data
}

func syncManagedDir(root *os.Root) error {
	f, err := root.Open(".")
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}
