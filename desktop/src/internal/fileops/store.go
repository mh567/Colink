package fileops

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"
)

const (
	BusName         = "org.colink.FileOperations1"
	BusPath         = "/org/colink/FileOperations1"
	ManagedRoot     = "/var/lib/colink-fileops"
	CancelError     = BusName + ".Cancelled"
	OverwriteAction = "org.colink.fileops.overwrite"
	DeleteAction    = "org.colink.fileops.delete"
	maxFileBytes    = 20 << 20
	intentTTL       = 3 * time.Minute
)

var (
	ErrCancelled  = errors.New("confirmation cancelled")
	errInactive   = errors.New("confirmation is unavailable")
	errChanged    = errors.New("target changed")
	errInvalid    = errors.New("invalid managed file operation")
	errPermission = errors.New("managed file permission unavailable")
)

func DirectoryForUID(uid uint32) string {
	return ManagedRoot + "/" + strconv.FormatUint(uint64(uid), 10)
}

func SameDirectory(root *os.Root, path string) bool {
	current, err := os.Lstat(path)
	if err != nil || !current.IsDir() {
		return false
	}
	opened, err := root.Stat(".")
	return err == nil && os.SameFile(current, opened)
}

type intent struct {
	sender, name, operation string
	uid                     uint32
	root                    *os.Root
	original                *os.File
	digest                  [sha256.Size]byte
	output                  [sha256.Size]byte
	proof                   credentialBinding
	deadline                time.Time
	timer                   *time.Timer
	cancel                  context.CancelFunc
	approved                bool
}

type credentialBinding struct {
	format, device string
	session        [sha256.Size]byte
}

func bindCredentials(proof Credentials) credentialBinding {
	return credentialBinding{proof.Format, proof.DeviceID, sha256.Sum256([]byte(proof.Session))}
}

type store struct {
	mu      sync.Mutex
	intents map[string]*intent
	verify  func(*os.File, uint32) error
	protect func(*os.File, uint32) error
	syncDir func(*os.Root) error
	server  ServerAuthorizer
}

func newStore(verify, protect func(*os.File, uint32) error, syncDir func(*os.Root) error, server ServerAuthorizer) *store {
	return &store{intents: make(map[string]*intent), verify: verify, protect: protect, syncDir: syncDir, server: server}
}

func (s *store) checkPermission(ctx context.Context, proof Credentials, action string) error {
	return s.authorize(ctx, proof, authorizationRequest{Action: action})
}

func (s *store) authorize(ctx context.Context, proof Credentials, request authorizationRequest) error {
	if ctx.Err() != nil || s.server == nil || s.server(ctx, proof, request) != nil || ctx.Err() != nil {
		return errPermission
	}
	return nil
}

func validName(name string) bool {
	if len(name) == 0 || len(name) > 255 || strings.HasPrefix(name, ".") || strings.ContainsAny(name, "/\\:") ||
		strings.TrimSpace(name) != name || !utf8.ValidString(name) {
		return false
	}
	for _, r := range name {
		if unicode.IsControl(r) || unicode.In(r, unicode.Cf, unicode.Zl, unicode.Zp) {
			return false
		}
	}
	return true
}

func managedName(name, format string, destructive bool) bool {
	if !validName(name) {
		return false
	}
	ext := filepath.Ext(name)
	switch format {
	case "docx", "xlsx":
		return strings.EqualFold(ext, "."+format)
	case "pptx":
		return !destructive && strings.EqualFold(ext, ".pptx")
	case "ocr":
		return !destructive && strings.EqualFold(ext, ".txt")
	default:
		return false
	}
}

func destructiveOperation(operation string) bool {
	return operation == "overwrite" || operation == "delete"
}

func wrapPath(path string) string {
	var out strings.Builder
	width := 0
	for _, r := range path {
		w := 1
		if r > 127 {
			w = 2
		}
		if width+w > 30 {
			out.WriteByte('\n')
			width = 0
		}
		out.WriteRune(r)
		width += w
	}
	return out.String()
}

func promptDetails(operation, path string) map[string]string {
	dir, name := filepath.Split(path)
	target := wrapPath(dir) + "\n" + wrapPath(name)
	enAction, enScope := "Overwrite", "Replace only this file's contents."
	zhAction, zhScope := "覆盖", "仅替换这一个文件的内容。"
	if operation == "delete" {
		enAction, enScope = "Delete", "Delete only this file."
		zhAction, zhScope = "删除", "仅删除这一个文件。"
	}
	return map[string]string{
		"prompt_en": "Action: " + enAction + "\nFull path (join the lines):\n" + target + "\nScope: " + enScope,
		"prompt_zh": "动作：" + zhAction + "\n完整路径（各行直接拼接）：\n" + target + "\n影响范围：" + zhScope,
	}
}

func (s *store) snapshot(root *os.Root, uid uint32, name string) (*os.File, [sha256.Size]byte, error) {
	var digest [sha256.Size]byte
	info, err := root.Lstat(name)
	if err != nil || !info.Mode().IsRegular() {
		return nil, digest, errChanged
	}
	f, err := root.Open(name)
	if err != nil {
		return nil, digest, err
	}
	actual, err := f.Stat()
	if err != nil || !os.SameFile(info, actual) || s.verify(f, uid) != nil {
		f.Close()
		return nil, digest, errChanged
	}
	h := sha256.New()
	n, err := io.Copy(h, io.LimitReader(f, maxFileBytes+1))
	if err != nil || n > maxFileBytes {
		f.Close()
		return nil, digest, errInvalid
	}
	copy(digest[:], h.Sum(nil))
	return f, digest, nil
}

func (s *store) prepare(ctx context.Context, sender string, uid uint32, root *os.Root, name, operation string, proof Credentials) (string, error) {
	if !managedName(name, proof.Format, true) || !destructiveOperation(operation) {
		return "", errInvalid
	}
	if err := s.checkPermission(ctx, proof, operation); err != nil {
		return "", err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if ctx.Err() != nil || len(s.intents) >= 64 {
		return "", errInactive
	}
	for _, p := range s.intents {
		if p.uid == uid {
			return "", errInactive
		}
	}
	if !SameDirectory(root, root.Name()) {
		return "", errChanged
	}
	f, digest, err := s.snapshot(root, uid, name)
	if err != nil {
		return "", err
	}
	token := rand.Text()
	p := &intent{sender: sender, uid: uid, root: root, original: f, digest: digest, name: name, operation: operation,
		proof: bindCredentials(proof), deadline: time.Now().Add(intentTTL)}
	s.intents[token] = p
	p.timer = time.AfterFunc(intentTTL, func() { s.abort(sender, token) })
	return token, nil
}

func (s *store) activeIntent(sender, token, operation string, proof Credentials) *intent {
	p := s.intents[token]
	if p == nil || p.sender != sender {
		return nil
	}
	if !time.Now().Before(p.deadline) || p.operation != operation || p.proof != bindCredentials(proof) {
		s.release(token, p)
		return nil
	}
	return p
}

func (s *store) release(token string, p *intent) {
	delete(s.intents, token)
	p.timer.Stop()
	if p.cancel != nil {
		p.cancel()
	}
	p.original.Close()
	p.root.Close()
}

func (s *store) abort(sender, token string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if p := s.intents[token]; p != nil && p.sender == sender {
		s.release(token, p)
	}
}

func (s *store) disconnect(sender string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for token, p := range s.intents {
		if p.sender == sender {
			s.release(token, p)
		}
	}
}

func (s *store) confirm(ctx context.Context, sender, token, operation string, proof Credentials, payloadDigest []byte, authorize func(context.Context, string, string) (bool, error)) error {
	if !destructiveOperation(operation) || s.checkPermission(ctx, proof, operation) != nil {
		s.abort(sender, token)
		return errPermission
	}
	s.mu.Lock()
	p := s.activeIntent(sender, token, operation, proof)
	if p == nil || ctx.Err() != nil {
		s.mu.Unlock()
		return errInactive
	}
	wantLen := 0
	if p.operation == "overwrite" {
		wantLen = sha256.Size
	}
	var output [sha256.Size]byte
	copy(output[:], payloadDigest)
	if len(payloadDigest) != wantLen || ((p.approved || p.cancel != nil) && output != p.output) {
		s.release(token, p)
		s.mu.Unlock()
		return errInvalid
	}
	if p.approved {
		s.mu.Unlock()
		return nil
	}
	if p.cancel != nil {
		s.mu.Unlock()
		return errInactive
	}
	promptCtx, cancel := context.WithDeadline(ctx, p.deadline)
	p.cancel = cancel
	p.output = output
	target := filepath.Join(p.root.Name(), p.name)
	s.mu.Unlock()

	allowed, err := authorize(promptCtx, p.operation, target)
	cancel()

	s.mu.Lock()
	defer s.mu.Unlock()
	if s.activeIntent(sender, token, operation, proof) != p {
		return errInactive
	}
	p.cancel = nil
	if err != nil || !allowed {
		s.release(token, p)
		if err != nil {
			return err
		}
		return ErrCancelled
	}
	p.approved = true
	return nil
}

func (s *store) commit(ctx context.Context, sender, token, operation string, proof Credentials, data []byte) error {
	if !destructiveOperation(operation) || s.checkPermission(ctx, proof, operation) != nil {
		s.abort(sender, token)
		return errPermission
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	p := s.activeIntent(sender, token, operation, proof)
	if p == nil || ctx.Err() != nil || !p.approved {
		return errInactive
	}
	defer s.release(token, p)
	if len(data) > maxFileBytes || (p.operation == "delete" && len(data) != 0) ||
		(p.operation == "overwrite" && sha256.Sum256(data) != p.output) {
		return errInvalid
	}
	if !SameDirectory(p.root, p.root.Name()) {
		return errChanged
	}
	latest, digest, err := s.snapshot(p.root, p.uid, p.name)
	if err != nil {
		return err
	}
	before, beforeErr := p.original.Stat()
	after, afterErr := latest.Stat()
	latest.Close()
	if beforeErr != nil || afterErr != nil || !os.SameFile(before, after) || digest != p.digest {
		return errChanged
	}
	if !time.Now().Before(p.deadline) || ctx.Err() != nil {
		return errInactive
	}
	// Only the service can change this directory; all its mutations take mu.
	if p.operation == "delete" {
		if err = p.root.Remove(p.name); err != nil {
			return err
		}
		return s.syncDir(p.root)
	}
	stage := ".colink-" + rand.Text()
	if err = s.write(p.root, p.uid, stage, data, nil); err != nil {
		return err
	}
	defer p.root.Remove(stage)
	if !time.Now().Before(p.deadline) || ctx.Err() != nil {
		return errInactive
	}
	if err = p.root.Rename(stage, p.name); err != nil {
		return err
	}
	return s.syncDir(p.root)
}

func (s *store) create(ctx context.Context, root *os.Root, uid uint32, name string, data []byte, proof Credentials, request authorizationRequest) error {
	if !managedName(name, proof.Format, false) || len(data) > maxFileBytes {
		return errInvalid
	}
	if proof.Format == "pptx" && request.SourceFormat != "pptx" && request.SourceFormat != "docx" {
		return errPermission
	}
	if err := s.authorize(ctx, proof, request); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if ctx.Err() != nil {
		return errInactive
	}
	if !SameDirectory(root, root.Name()) {
		return errChanged
	}
	if proof.Format == "pptx" {
		return s.publishPPTX(ctx, root, uid, name, data, proof, request)
	}
	if err := s.write(root, uid, name, data, nil); err != nil {
		return err
	}
	return s.syncDir(root)
}

func (s *store) publishPPTX(ctx context.Context, root *os.Root, uid uint32, name string, data []byte, proof Credentials, request authorizationRequest) error {
	stage := ".colink-" + rand.Text()
	// Recheck permission before the staging file gets its readable ACL.
	if err := s.write(root, uid, stage, data, func() error {
		return s.authorize(ctx, proof, request)
	}); err != nil {
		return err
	}
	defer root.Remove(stage)
	if ctx.Err() != nil {
		return errInactive
	}
	if !SameDirectory(root, root.Name()) {
		return errChanged
	}
	if err := root.Link(stage, name); err != nil {
		return err
	}
	if err := root.Remove(stage); err != nil {
		return errors.Join(err, root.Remove(name))
	}
	if err := s.syncDir(root); err != nil {
		if rollbackErr := root.Remove(name); rollbackErr != nil {
			return errors.Join(err, rollbackErr)
		}
		return errors.Join(err, s.syncDir(root))
	}
	return nil
}

func (s *store) write(root *os.Root, uid uint32, entry string, data []byte, beforeProtect func() error) (err error) {
	f, err := root.OpenFile(entry, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = root.Remove(entry)
		}
	}()
	_, err = f.Write(data)
	if err == nil && beforeProtect != nil {
		err = beforeProtect()
	}
	if err == nil {
		err = s.protect(f, uid)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	return err
}
