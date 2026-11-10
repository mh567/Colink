package main

import (
	"archive/zip"
	"bytes"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
)

func launchReady(root string) bool {
	if _, err := os.Stat(filepath.Join(root, "launch.py")); err != nil {
		return false
	}
	info, err := os.Stat(pythonExe(root))
	if err != nil || !info.Mode().IsRegular() || info.Size() < 1024 {
		return false
	}
	return runtime.GOOS == "windows" || info.Mode().Perm()&0o111 != 0
}

func pythonExe(root string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(root, "runtime", "python.exe")
	}
	return filepath.Join(root, "runtime", "bin", "python3")
}

func ensurePortable(locale Locale, status func(string)) error {
	return preparePortable(locale, status, nil)
}

func ensurePortableAtStartup(locale Locale, status func(string)) error {
	return preparePortable(locale, status, func() bool {
		host, port := resolveListen()
		return findRunningDashboard(host, port) == ""
	})
}

func preparePortable(locale Locale, status func(string), runtimeIdle func() bool) error {
	root := portableDir()
	var canSwap func() bool
	if launchReady(root) {
		currentVersion := portableVersion(root)
		bundledVersion, err := bundledPortableVersion()
		order := compareVersions(bundledVersion, currentVersion)
		upgrade := currentVersion == "" || order > 0
		refresh := runtimeIdle != nil && currentVersion != "" && order == 0 &&
			hasNewerPortableRevision(root) && runtimeIdle() && portableDataOutsideRuntime(root)
		if err != nil || bundledVersion == "" || (!upgrade && !refresh) {
			status(desktopText(locale, copyStatusUsingRuntime))
			return nil
		}
		if refresh {
			canSwap = func() bool { return runtimeIdle() && portableDataOutsideRuntime(root) }
		}
		if upgrade {
			status(desktopText(locale, copyStatusBackupDatabase, bundledVersion))
		}
		if _, err := backupSQLiteBeforeUpgrade(root, currentVersion, bundledVersion); err != nil {
			return fmt.Errorf("%s: %w", desktopText(locale, copyErrorBackupFailed), err)
		}
		status(desktopText(locale, copyStatusUpdatingRuntime))
	} else {
		status(desktopText(locale, copyStatusFirstExtract))
	}
	if err := replacePortable(root, canSwap); err != nil {
		if launchReady(root) {
			status(desktopText(locale, copyStatusUpdateFailedKeep))
			return nil
		}
		return err
	}
	if runtime.GOOS == "darwin" {
		_ = exec.Command("xattr", "-dr", "com.apple.quarantine", root).Run()
	}
	if !launchReady(root) {
		return fmt.Errorf("portable extract missing launch.py or python under %s", root)
	}
	return nil
}

const portableRevisionName = "PORTABLE-REVISION.txt"

func installedPortableRevision(root string) int {
	current, _ := os.ReadFile(filepath.Join(root, portableRevisionName))
	return portableRevision(current)
}

func bundledPortableRevision(files []*zip.File) int {
	return portableRevision(readPortableBundleFile(files, portableRevisionName, 32))
}

func portableRevision(value []byte) int {
	revision, err := strconv.Atoi(strings.TrimSpace(string(value)))
	if err != nil {
		return 0
	}
	return max(0, revision)
}

func hasNewerPortableRevision(root string) bool {
	files, closeArchive, err := bundledPortableFiles()
	if err != nil {
		return false
	}
	defer closeArchive()
	return bundledPortableRevision(files) > installedPortableRevision(root) &&
		readPortableBundleFile(files, "pptx/runner.py", 64<<10) != nil
}

func portableDataOutsideRuntime(root string) bool {
	entries, err := os.ReadDir(root)
	if err != nil {
		return false
	}
	// The standalone launcher and directory grants can store user data here.
	for _, entry := range entries {
		switch entry.Name() {
		case "runtime", "packages", "ocr", "pptx":
			if !entry.IsDir() {
				return false
			}
		case "launch.py", "start.sh", "start.bat", "README.txt", "VERSION.txt", portableRevisionName:
			if !entry.Type().IsRegular() {
				return false
			}
		default:
			return false
		}
	}
	home := octopHome()
	states, err := newLocalDOCX(home).load()
	if err != nil {
		return false
	}
	for _, state := range states {
		for _, grant := range state.Grants {
			if pathWithinPortable(root, grant.Path) {
				return false
			}
		}
	}
	database, sqlite, err := desktopSQLitePath(home)
	return err == nil && (!sqlite || !pathWithinPortable(root, database))
}

func pathWithinPortable(root, candidate string) bool {
	within := func(root, candidate string) bool {
		relative, err := filepath.Rel(root, candidate)
		return err == nil && filepath.IsLocal(relative)
	}
	if within(root, candidate) {
		return true
	}
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return false
	}
	resolvedCandidate, err := filepath.EvalSymlinks(candidate)
	return err == nil && within(resolvedRoot, resolvedCandidate)
}

func replacePortable(root string, canSwap func() bool) error {
	next := root + ".new"
	previous := root + ".previous"
	_ = os.RemoveAll(next)
	if err := extractPortable(next); err != nil {
		_ = os.RemoveAll(next)
		return err
	}
	if !launchReady(next) {
		_ = os.RemoveAll(next)
		return fmt.Errorf("portable extract missing launch.py or python under %s", next)
	}
	if canSwap != nil && !canSwap() {
		_ = os.RemoveAll(next)
		return fmt.Errorf("portable runtime is in use")
	}

	_ = os.RemoveAll(previous)
	hadCurrent := false
	if _, err := os.Stat(root); err == nil {
		if err := os.Rename(root, previous); err != nil {
			_ = os.RemoveAll(next)
			return err
		}
		hadCurrent = true
	}
	if err := os.Rename(next, root); err != nil {
		if hadCurrent {
			_ = os.Rename(previous, root)
		}
		return err
	}
	_ = os.RemoveAll(previous)
	return nil
}

func portableVersion(root string) string {
	version := installedPackageVersion(root)
	data, err := os.ReadFile(filepath.Join(root, "VERSION.txt"))
	if err == nil {
		bundledVersion := versionFromText(string(data))
		if version == "" || compareVersions(bundledVersion, version) > 0 {
			version = bundledVersion
		}
	}
	return version
}

func installedPackageVersion(root string) string {
	matches, _ := filepath.Glob(filepath.Join(root, "packages", "octop-*.dist-info", "METADATA"))
	version := ""
	for _, metadata := range matches {
		data, err := os.ReadFile(metadata)
		if err != nil {
			continue
		}
		value := metadataVersion(string(data))
		if value == "" {
			continue
		}
		if version == "" || compareVersions(value, version) > 0 {
			version = value
		}
	}
	return version
}

func bundledPortableVersion() (string, error) {
	files, closeArchive, err := bundledPortableFiles()
	if err != nil {
		return "", err
	}
	defer closeArchive()
	return versionFromZip(files)
}

func bundledPortableFiles() ([]*zip.File, func(), error) {
	if os.Getenv("OCTOP_DESKTOP_PORTABLE_ZIP") == "" && len(embeddedPortable) > 0 {
		reader, err := zip.NewReader(bytes.NewReader(embeddedPortable), int64(len(embeddedPortable)))
		if err != nil {
			return nil, nil, err
		}
		return reader.File, func() {}, nil
	}
	zipPath, err := bundledPortableZip()
	if err != nil {
		return nil, nil, err
	}
	reader, err := zip.OpenReader(zipPath)
	if err != nil {
		return nil, nil, err
	}
	return reader.File, func() { _ = reader.Close() }, nil
}

func readPortableBundleFile(files []*zip.File, relative string, limit int64) []byte {
	name := "Octop-" + greenPlat() + "/" + relative
	var member *zip.File
	for _, file := range files {
		if !strings.EqualFold(portableArchiveDestination(file.Name), "/"+relative) {
			continue
		}
		if file.Name != name || member != nil || !file.Mode().IsRegular() || file.UncompressedSize64 == 0 ||
			file.UncompressedSize64 > uint64(limit) {
			return nil
		}
		member = file
	}
	if member == nil {
		return nil
	}
	reader, err := member.Open()
	if err != nil {
		return nil
	}
	defer reader.Close()
	content, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil || int64(len(content)) > limit {
		return nil
	}
	return content
}

func portableArchiveDestination(name string) string {
	_, relative, _ := strings.Cut(name, "/")
	parts := strings.Split(path.Clean("/"+strings.ReplaceAll(relative, "\\", "/")), "/")
	for i, part := range parts {
		parts[i] = strings.TrimRight(part, " .")
	}
	return path.Clean(strings.Join(parts, "/"))
}

func versionFromZip(files []*zip.File) (string, error) {
	fromFile, err := zipEntryVersion(files, func(name string) bool {
		return filepath.Base(filepath.FromSlash(name)) == "VERSION.txt"
	}, versionFromText)
	if err != nil {
		return "", err
	}
	if fromFile != "" {
		return fromFile, nil
	}
	return zipEntryVersion(files, func(name string) bool {
		rel := filepath.ToSlash(name)
		return strings.Contains(rel, "/octop-") && strings.HasSuffix(rel, ".dist-info/METADATA")
	}, metadataVersion)
}

func zipEntryVersion(files []*zip.File, match func(string) bool, parse func(string) string) (string, error) {
	version := ""
	for _, file := range files {
		if !match(file.Name) {
			continue
		}
		reader, err := file.Open()
		if err != nil {
			return "", err
		}
		data, readErr := io.ReadAll(reader)
		closeErr := reader.Close()
		if readErr != nil {
			return "", readErr
		}
		if closeErr != nil {
			return "", closeErr
		}
		value := parse(string(data))
		if value == "" {
			continue
		}
		if version == "" || compareVersions(value, version) > 0 {
			version = value
		}
	}
	return version, nil
}

func versionFromText(text string) string {
	for _, line := range strings.Split(text, "\n") {
		if value, ok := strings.CutPrefix(strings.TrimSpace(line), "octop_version="); ok {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func metadataVersion(text string) string {
	for _, line := range strings.Split(text, "\n") {
		value, ok := strings.CutPrefix(strings.TrimSpace(line), "Version:")
		if ok {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func extractPortable(root string) error {
	if os.Getenv("OCTOP_DESKTOP_PORTABLE_ZIP") != "" {
		zipPath, err := bundledPortableZip()
		if err != nil {
			return err
		}
		return unzipGreen(zipPath, root)
	}
	if len(embeddedPortable) > 0 {
		return unzipGreenBytes(embeddedPortable, root)
	}
	zipPath, err := bundledPortableZip()
	if err != nil {
		return err
	}
	return unzipGreen(zipPath, root)
}

func bundledPortableZip() (string, error) {
	if override := os.Getenv("OCTOP_DESKTOP_PORTABLE_ZIP"); override != "" {
		if _, err := os.Stat(override); err != nil {
			return "", fmt.Errorf("bundled portable package: %w", err)
		}
		return override, nil
	}
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	dir := filepath.Dir(exe)
	plat := greenPlat()
	legacy := fmt.Sprintf("Octop-%s.zip", plat)
	searchDirs := []string{
		dir,
		filepath.Join(dir, "..", "Resources"),
	}
	for _, search := range searchDirs {
		search = filepath.Clean(search)
		legacyPath := filepath.Join(search, legacy)
		if _, err := os.Stat(legacyPath); err == nil {
			return legacyPath, nil
		}
		matches, _ := filepath.Glob(filepath.Join(search, "Octop-portable-"+plat+"-*.zip"))
		if len(matches) > 0 {
			sort.Strings(matches)
			return matches[len(matches)-1], nil
		}
	}
	return "", fmt.Errorf("bundled portable package %s not found beside application", legacy)
}

func unzipGreen(zipPath, dest string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	return unzipGreenFiles(r.File, dest)
}

func unzipGreenBytes(data []byte, dest string) error {
	r, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return err
	}
	return unzipGreenFiles(r.File, dest)
}

func unzipGreenFiles(files []*zip.File, dest string) error {
	_ = os.RemoveAll(dest)
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return err
	}
	// Zip root is Octop-<plat>/… — strip that prefix.
	for _, f := range files {
		name := f.Name
		parts := strings.SplitN(name, "/", 2)
		if len(parts) < 2 {
			continue
		}
		rel := parts[1]
		if rel == "" {
			continue
		}
		target := filepath.Join(dest, filepath.FromSlash(rel))
		if !strings.HasPrefix(target, filepath.Clean(dest)+string(os.PathSeparator)) && target != filepath.Clean(dest) {
			return fmt.Errorf("illegal zip path %s", name)
		}
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if f.Mode()&os.ModeSymlink != 0 {
			rc, err := f.Open()
			if err != nil {
				return err
			}
			linkTarget, err := io.ReadAll(rc)
			rc.Close()
			if err != nil {
				return err
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			if err := os.Symlink(string(linkTarget), target); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		rc, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, f.Mode())
		if err != nil {
			rc.Close()
			return err
		}
		_, err = io.Copy(out, rc)
		out.Close()
		rc.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func waitHealth(locale Locale, base string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	url := strings.TrimRight(base, "/") + "/api/health"
	var lastErr error
	var lastStatus int
	for time.Now().Before(deadline) {
		resp, err := http.Get(url)
		if err == nil {
			lastStatus = resp.StatusCode
			lastErr = nil
			resp.Body.Close()
			if resp.StatusCode >= 200 && resp.StatusCode < 500 {
				return nil
			}
		} else {
			lastErr = err
			lastStatus = 0
		}
		time.Sleep(400 * time.Millisecond)
	}
	return formatHealthWaitError(locale, base, timeout, lastErr, lastStatus)
}

func formatWaitDuration(locale Locale, d time.Duration) string {
	sec := int(d.Round(time.Second) / time.Second)
	if sec < 1 {
		sec = 1
	}
	minutes := sec%60 == 0
	n := sec
	if minutes {
		n = sec / 60
	}
	switch {
	case minutes && n == 1:
		return desktopText(locale, copyWait1Minute)
	case minutes:
		return desktopText(locale, copyWaitNMinutes, n)
	case n == 1:
		return desktopText(locale, copyWait1Second)
	default:
		return desktopText(locale, copyWaitNSeconds, n)
	}
}

func formatHealthWaitError(locale Locale, base string, timeout time.Duration, lastErr error, lastStatus int) error {
	addr := strings.TrimRight(base, "/")
	wait := formatWaitDuration(locale, timeout)
	switch {
	case lastStatus >= 500:
		return fmt.Errorf("%s", desktopText(locale, copyHealthNotReady5xx, wait, addr))
	case lastErr != nil:
		return fmt.Errorf("%s", desktopText(locale, copyHealthNotReadyConnect, wait, addr))
	default:
		return fmt.Errorf("%s", desktopText(locale, copyHealthNotReady, wait, addr))
	}
}
