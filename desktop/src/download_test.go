package main

import (
	"archive/zip"
	"bytes"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestEnsurePortableUsesEmbeddedPackage(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", "")

	zipPath := filepath.Join(t.TempDir(), "embedded.zip")
	writeTestGreenZip(t, zipPath, "1.0.0")
	data, err := os.ReadFile(zipPath)
	if err != nil {
		t.Fatal(err)
	}
	prev := embeddedPortable
	embeddedPortable = data
	t.Cleanup(func() { embeddedPortable = prev })

	if err := ensurePortable(LocaleZH, func(string) {}); err != nil {
		t.Fatal(err)
	}
	if !launchReady(portableDir()) {
		t.Fatal("embedded package was not extracted into the portable directory")
	}
}

func TestEnsurePortableUsesBundledPackage(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)

	zipPath := filepath.Join(t.TempDir(), "Octop-"+greenPlat()+".zip")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", zipPath)
	writeTestGreenZip(t, zipPath, "1.0.0")

	var statuses []string
	err := ensurePortable(LocaleZH, func(status string) {
		statuses = append(statuses, status)
	})
	if err != nil {
		t.Fatal(err)
	}
	if !launchReady(portableDir()) {
		t.Fatal("local package was not extracted into the portable directory")
	}
	if len(statuses) == 0 || statuses[0] != "首次启动，正在解压内置运行环境…" {
		t.Fatalf("unexpected statuses: %v", statuses)
	}
	if _, err := os.Stat(zipPath); err != nil {
		t.Fatalf("bundled package should be retained: %v", err)
	}
}

func TestSameVersionPPTXWorkerUsesBundledRunnerWithoutReplacingRuntime(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	root := portableDir()
	oldZip := filepath.Join(t.TempDir(), "old.zip")
	writeTestGreenZip(t, oldZip, "1.0.3b3")
	if err := unzipGreen(oldZip, root); err != nil {
		t.Fatal(err)
	}
	oldRunner := filepath.Join(root, "pptx", "runner.py")
	if err := os.Mkdir(filepath.Dir(oldRunner), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(oldRunner, []byte("old runner"), 0o600); err != nil {
		t.Fatal(err)
	}
	newZip := filepath.Join(t.TempDir(), "new.zip")
	writeTestGreenZip(t, newZip, "1.0.3b3", testGreenZipFile{"pptx/runner.py", "updated runner"})
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)

	script, cleanup, err := prepareLocalPPTXRunner(root)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	if data, err := os.ReadFile(script); err != nil || string(data) != "updated runner" {
		t.Fatalf("task runner = %q, %v", data, err)
	}
	if data, err := os.ReadFile(oldRunner); err != nil || string(data) != "old runner" {
		t.Fatalf("installed runner changed: %q, %v", data, err)
	}
	if filepath.Dir(filepath.Dir(script)) != root || script == oldRunner {
		t.Fatalf("task runner must use the installed packages from a separate directory: %s", script)
	}
	if err := os.Remove(oldRunner); err != nil {
		t.Fatal(err)
	}
	missing, cleanupMissing, err := prepareLocalPPTXRunner(root)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanupMissing()
	if data, err := os.ReadFile(missing); err != nil || string(data) != "updated runner" {
		t.Fatalf("runner fallback for an old install = %q, %v", data, err)
	}
	if err := os.WriteFile(filepath.Join(root, portableRevisionName), []byte("1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := prepareLocalPPTXRunner(root); err == nil || err.Error() != "PPTX_RUNTIME_UNAVAILABLE" {
		t.Fatalf("stale ZIP should not fill a missing runner in a newer runtime: %v", err)
	}
	if err := os.WriteFile(oldRunner, []byte("newer installed runner"), 0o600); err != nil {
		t.Fatal(err)
	}
	script, cleanupInstalled, err := prepareLocalPPTXRunner(root)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanupInstalled()
	if script != oldRunner {
		t.Fatalf("stale ZIP replaced the newer installed runner: %s", script)
	}
	if err := os.Remove(oldRunner); err != nil {
		t.Fatal(err)
	}
	for _, alias := range []string{"pptx/./runner.py", "pptx/runner.py. "} {
		aliasZip := filepath.Join(t.TempDir(), "alias.zip")
		writeTestGreenZip(t, aliasZip, "1.0.3b3",
			testGreenZipFile{portableRevisionName, "1\n"},
			testGreenZipFile{"pptx/runner.py", "updated runner"},
			testGreenZipFile{alias, "stale runner"})
		t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", aliasZip)
		if _, _, err := prepareLocalPPTXRunner(root); err == nil || err.Error() != "PPTX_RUNTIME_UNAVAILABLE" {
			t.Fatalf("ZIP with alias %q should fail: %v", alias, err)
		}
	}
}

func TestEnsurePortableStartupRefreshesSameVersionOnlyWhenIdle(t *testing.T) {
	for _, scenario := range []string{
		"cold startup", "running", "nested data", "nested database", "user folder", "nested grant", "task",
	} {
		t.Run(scenario, func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("OCTOP_HOME", home)
			t.Setenv("OCTOP_DATABASE_DRIVER", "sqlite")
			t.Setenv("OCTOP_DATABASE_URL", "")
			t.Setenv("OCTOP_DATABASE_SQLITE_PATH", "")
			root := portableDir()
			oldZip := filepath.Join(t.TempDir(), "old.zip")
			writeTestGreenZip(t, oldZip, "1.0.3b3")
			if err := unzipGreen(oldZip, root); err != nil {
				t.Fatal(err)
			}
			newZip := filepath.Join(t.TempDir(), "new.zip")
			writeTestGreenZip(t, newZip, "1.0.3b3",
				testGreenZipFile{"PORTABLE-REVISION.txt", "1\n"},
				testGreenZipFile{"pptx/runner.py", "updated runner"})
			t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)
			database := filepath.Join(home, "octop.db")
			if scenario == "nested database" {
				database = filepath.Join(root, "project.db")
				t.Setenv("OCTOP_DATABASE_SQLITE_PATH", database)
			}
			if err := os.WriteFile(database, []byte("user data"), 0o600); err != nil {
				t.Fatal(err)
			}
			if scenario == "nested data" {
				if err := os.Mkdir(filepath.Join(root, "data"), 0o700); err != nil {
					t.Fatal(err)
				}
			}
			if scenario == "user folder" || scenario == "nested grant" {
				reports := filepath.Join(root, "Reports")
				if scenario == "nested grant" {
					reports = filepath.Join(root, "packages", "Reports")
					worker := newLocalDOCX(home)
					if err := worker.save(map[string]docxState{
						"scope": {Grants: []directoryGrant{{Path: reports, Mode: "rw"}}},
					}); err != nil {
						t.Fatal(err)
					}
				}
				if err := os.MkdirAll(reports, 0o700); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					if _, err := os.Stat(reports); err != nil {
						t.Errorf("user folder removed: %v", err)
					}
				})
			}

			backupCalls := 0
			previousBackup := runSQLiteBackup
			runSQLiteBackup = func(_ string, source, destination string) error {
				if source != database {
					t.Fatalf("backup source = %s, want %s", source, database)
				}
				backupCalls++
				return os.WriteFile(destination, []byte("backup"), 0o600)
			}
			t.Cleanup(func() { runSQLiteBackup = previousBackup })

			ensure := func(locale Locale, status func(string)) error {
				return preparePortable(locale, status, func() bool { return scenario != "running" })
			}
			if scenario == "task" {
				ensure = ensurePortable
			}
			for range 2 {
				if err := ensure(LocaleZH, func(string) {}); err != nil {
					t.Fatal(err)
				}
			}
			content, err := os.ReadFile(filepath.Join(root, "pptx", "runner.py"))
			if scenario == "cold startup" {
				if err != nil || string(content) != "updated runner" || backupCalls != 1 {
					t.Fatalf("runner = %q, %v, backups = %d", content, err, backupCalls)
				}
			} else if !os.IsNotExist(err) || backupCalls != 0 {
				t.Fatalf("unsafe runtime refresh: runner = %q, %v, backups = %d", content, err, backupCalls)
			}
			if data, err := os.ReadFile(database); err != nil || string(data) != "user data" {
				t.Fatalf("database changed: %q, %v", data, err)
			}
		})
	}
}

func TestEnsurePortableReplacesOlderRuntime(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()

	oldZip := filepath.Join(t.TempDir(), "old.zip")
	writeTestGreenZip(t, oldZip, "0.9.31")
	if err := unzipGreen(oldZip, root); err != nil {
		t.Fatal(err)
	}
	stale := filepath.Join(root, "stale.txt")
	if err := os.WriteFile(stale, []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}

	newZip := filepath.Join(t.TempDir(), "new.zip")
	writeTestGreenZip(t, newZip, "0.9.32")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)
	var statuses []string
	if err := ensurePortable(LocaleZH, func(status string) { statuses = append(statuses, status) }); err != nil {
		t.Fatal(err)
	}

	if got := portableVersion(root); got != "0.9.32" {
		t.Fatalf("portable version = %q, want 0.9.32", got)
	}
	if _, err := os.Stat(stale); !os.IsNotExist(err) {
		t.Fatalf("old runtime was not replaced: %v", err)
	}
	if len(statuses) < 2 ||
		statuses[0] != "发现客户端新版 0.9.32，正在备份数据库…" ||
		statuses[1] != "正在更新内置运行环境…" {
		t.Fatalf("unexpected statuses: %v", statuses)
	}
}

func TestEnsurePortableUpgradesBundledVersionAfterDatabaseBackup(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()

	oldZip := filepath.Join(t.TempDir(), "old.zip")
	writeTestGreenZip(t, oldZip, "0.9.29")
	if err := unzipGreen(oldZip, root); err != nil {
		t.Fatal(err)
	}
	database := filepath.Join(home, "octop.db")
	if err := os.WriteFile(database, []byte("database"), 0o600); err != nil {
		t.Fatal(err)
	}

	newZip := filepath.Join(t.TempDir(), "new.zip")
	writeTestGreenZip(t, newZip, "0.9.32")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)

	previousBackup := runSQLiteBackup
	runSQLiteBackup = func(_ string, source string, destination string) error {
		if source != database {
			t.Fatalf("backup source = %q, want %q", source, database)
		}
		return os.WriteFile(destination, []byte("backup"), 0o600)
	}
	t.Cleanup(func() { runSQLiteBackup = previousBackup })

	if err := ensurePortable(LocaleZH, func(string) {}); err != nil {
		t.Fatal(err)
	}
	if got := portableVersion(root); got != "0.9.32" {
		t.Fatalf("portable version = %q, want 0.9.32", got)
	}
	backups, err := filepath.Glob(filepath.Join(home, "backups", "octop-desktop-pre-upgrade-*.db"))
	if err != nil || len(backups) != 1 {
		t.Fatalf("upgrade backup = %v, err = %v", backups, err)
	}
}

func TestEnsurePortableKeepsRuntimeWhenDatabaseBackupFails(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()

	oldZip := filepath.Join(t.TempDir(), "old.zip")
	writeTestGreenZip(t, oldZip, "0.9.29")
	if err := unzipGreen(oldZip, root); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "octop.db"), []byte("database"), 0o600); err != nil {
		t.Fatal(err)
	}
	newZip := filepath.Join(t.TempDir(), "new.zip")
	writeTestGreenZip(t, newZip, "0.9.32")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)

	previousBackup := runSQLiteBackup
	runSQLiteBackup = func(_, _, _ string) error { return errors.New("backup unavailable") }
	t.Cleanup(func() { runSQLiteBackup = previousBackup })

	if err := ensurePortable(LocaleZH, func(string) {}); err == nil {
		t.Fatal("backup failure should abort the runtime upgrade")
	}
	if got := portableVersion(root); got != "0.9.29" {
		t.Fatalf("portable version = %q, want preserved 0.9.29", got)
	}
}

func TestEnsurePortableKeepsNewerExistingRuntime(t *testing.T) {
	for _, test := range []struct {
		name, installed, recorded, bundled string
	}{
		{"stable", "0.9.33", "0.9.31", "0.9.32"},
		{"in-app prerelease upgrade", "1.0.2b6", "1.0.2b4", "1.0.2b5"},
	} {
		t.Run(test.name, func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("OCTOP_HOME", home)
			root := portableDir()

			newZip := filepath.Join(t.TempDir(), "new.zip")
			writeTestGreenZip(t, newZip, test.installed)
			if err := unzipGreen(newZip, root); err != nil {
				t.Fatal(err)
			}
			// An in-app package upgrade can leave the original VERSION.txt behind.
			preserved := map[string]string{
				filepath.Join(root, "VERSION.txt"): "octop_version=" + test.recorded + "\n",
				filepath.Join(root, "keep.txt"):    "keep",
				filepath.Join(home, "octop.db"):    "database",
			}
			for path, content := range preserved {
				if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
					t.Fatal(err)
				}
			}

			oldZip := filepath.Join(t.TempDir(), "old.zip")
			writeTestGreenZip(t, oldZip, test.bundled)
			t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", oldZip)
			if got, err := bundledPortableVersion(); err != nil || got != test.bundled {
				t.Fatalf("bundled version = %q, err = %v, want %q", got, err, test.bundled)
			}
			backupCalls := 0
			previousBackup := runSQLiteBackup
			runSQLiteBackup = func(_, _, _ string) error {
				backupCalls++
				return errors.New("unexpected backup for a newer installed runtime")
			}
			t.Cleanup(func() { runSQLiteBackup = previousBackup })

			for startup := 0; startup < 2; startup++ {
				var statuses []string
				if err := ensurePortable(LocaleZH, func(status string) { statuses = append(statuses, status) }); err != nil {
					t.Fatal(err)
				}
				if got := portableVersion(root); got != test.installed {
					t.Fatalf("portable version = %q, want %q", got, test.installed)
				}
				if got := installedPackageVersion(root); got != test.installed {
					t.Fatalf("installed package version = %q, want %q", got, test.installed)
				}
				for path, want := range preserved {
					if data, err := os.ReadFile(path); err != nil || string(data) != want {
						t.Fatalf("startup %d changed %s: %q, %v", startup, path, data, err)
					}
				}
				if len(statuses) != 1 || statuses[0] != desktopText(LocaleZH, copyStatusUsingRuntime) {
					t.Fatalf("unexpected startup statuses: %v", statuses)
				}
				if backupCalls != 0 {
					t.Fatalf("newer installed runtime triggered %d backups", backupCalls)
				}
			}
		})
	}
}

func TestPortableVersionPrefersNewerMetadataOverVersionTxt(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()
	b4Zip := filepath.Join(t.TempDir(), "b4.zip")
	writeTestGreenZip(t, b4Zip, "1.0.2b4")
	if err := unzipGreen(b4Zip, root); err != nil {
		t.Fatal(err)
	}

	// An in-app upgrade can leave both the old VERSION.txt and old dist-info.
	metadata := filepath.Join(root, "packages", "octop-1.0.2b5.dist-info", "METADATA")
	if err := os.MkdirAll(filepath.Dir(metadata), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(metadata, []byte("Name: octop\nVersion: 1.0.2b5\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := portableVersion(root); got != "1.0.2b5" {
		t.Fatalf("portable version = %q, want newer package metadata 1.0.2b5", got)
	}

	preserved := map[string]string{
		filepath.Join(root, "VERSION.txt"): "octop_version=1.0.2b4\n",
		filepath.Join(root, "keep.txt"):    "keep",
		filepath.Join(home, "octop.db"):    "database",
	}
	for path, content := range preserved {
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	b5Zip := filepath.Join(t.TempDir(), "b5.zip")
	writeTestGreenZip(t, b5Zip, "1.0.2b5")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", b5Zip)
	backupCalls := 0
	previousBackup := runSQLiteBackup
	runSQLiteBackup = func(_, _, _ string) error {
		backupCalls++
		return errors.New("unexpected backup for an already installed version")
	}
	t.Cleanup(func() { runSQLiteBackup = previousBackup })

	for startup := 0; startup < 2; startup++ {
		var statuses []string
		if err := ensurePortable(LocaleZH, func(status string) { statuses = append(statuses, status) }); err != nil {
			t.Fatal(err)
		}
		if got := portableVersion(root); got != "1.0.2b5" {
			t.Fatalf("portable version = %q, want preserved 1.0.2b5", got)
		}
		for path, want := range preserved {
			if data, err := os.ReadFile(path); err != nil || string(data) != want {
				t.Fatalf("startup %d changed %s: %q, %v", startup, path, data, err)
			}
		}
		if len(statuses) != 1 || statuses[0] != desktopText(LocaleZH, copyStatusUsingRuntime) {
			t.Fatalf("unexpected startup statuses: %v", statuses)
		}
		if backupCalls != 0 {
			t.Fatalf("already installed version triggered %d backups", backupCalls)
		}
	}
}

func TestEnsurePortableKeepsCurrentRuntimeWhenReplacementIsInvalid(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()

	currentZip := filepath.Join(t.TempDir(), "current.zip")
	writeTestGreenZip(t, currentZip, "0.9.31")
	if err := unzipGreen(currentZip, root); err != nil {
		t.Fatal(err)
	}

	invalidZip := filepath.Join(t.TempDir(), "invalid.zip")
	writeVersionOnlyZip(t, invalidZip, "0.9.32")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", invalidZip)
	var statuses []string
	if err := ensurePortable(LocaleZH, func(status string) { statuses = append(statuses, status) }); err != nil {
		t.Fatalf("existing runtime should still boot after a failed replacement: %v", err)
	}

	if !launchReady(root) {
		t.Fatal("current runtime should remain usable after replacement failure")
	}
	if got := portableVersion(root); got != "0.9.31" {
		t.Fatalf("portable version = %q, want 0.9.31", got)
	}
	if len(statuses) == 0 || statuses[len(statuses)-1] != "更新内置运行环境失败，继续使用已有运行环境…" {
		t.Fatalf("unexpected statuses: %v", statuses)
	}
}

func TestEnsurePortableReplacesLegacyRuntimeWithoutVersionFiles(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()

	oldZip := filepath.Join(t.TempDir(), "old.zip")
	writeTestGreenZip(t, oldZip, "0.9.31")
	if err := unzipGreen(oldZip, root); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(root, "VERSION.txt")); err != nil {
		t.Fatal(err)
	}
	if err := os.RemoveAll(filepath.Join(root, "packages")); err != nil {
		t.Fatal(err)
	}

	newZip := filepath.Join(t.TempDir(), "new.zip")
	writeTestGreenZip(t, newZip, "0.9.32")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)
	if err := ensurePortable(LocaleZH, func(string) {}); err != nil {
		t.Fatal(err)
	}
	if got := portableVersion(root); got != "0.9.32" {
		t.Fatalf("portable version = %q, want 0.9.32", got)
	}
}

func TestBundledPortableVersionFallsBackToMetadata(t *testing.T) {
	zipPath := filepath.Join(t.TempDir(), "meta-only.zip")
	writeMetadataOnlyZip(t, zipPath, "0.9.32")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", zipPath)
	got, err := bundledPortableVersion()
	if err != nil {
		t.Fatal(err)
	}
	if got != "0.9.32" {
		t.Fatalf("bundled version = %q, want 0.9.32", got)
	}
}

// Keep these cases aligned with octop.infra.setup.self_update.parse_version.
func TestCompareVersions(t *testing.T) {
	for _, test := range []struct {
		left, right string
		want        int
	}{
		{"0.9.32", "0.9.31", 1},
		{"0.9.32", "0.9.32", 0},
		{"0.9.31", "0.9.32", -1},
		{"1.0", "1.0.0", 0},
		{"0.9.32rc1", "0.9.31", 1},
		{"0.9.34-beta.1", "0.9.34b1", 0},
		{"1.0.2b5", "1.0.2b4", 1},
		{"1.0.2b4", "1.0.2b5", -1},
		{"1.0.2b10", "1.0.2b9", 1},
		{"1.0.2", "1.0.2b5", 1},
		{"1.0.2b5", "1.0.2", -1},
		{"1.0.2a2", "1.0.2b1", -1},
		{"1.0.2b5", "1.0.2rc1", -1},
		{"1.0.2rc1", "1.0.2", -1},
		{"v1.0.2B05", "1.0.2b5", 0},
		{"1.0.2+local.10", "1.0.2+local.9", 0},
		{"1.0.2+local.10", "1.0.2", 0},
		{"1.0.2-custom", "1.0.1", 1},
		{"dev", "dev", 0},
	} {
		t.Run(test.left+"/"+test.right, func(t *testing.T) {
			if got := compareVersions(test.left, test.right); got != test.want {
				t.Fatalf("compareVersions(%q, %q) = %d, want %d", test.left, test.right, got, test.want)
			}
		})
	}
}

func TestEnsurePortablePrereleaseUpgrade(t *testing.T) {
	for _, test := range []struct {
		name, installed, bundled string
		upgrade, backupFails     bool
	}{
		{"beta", "1.0.2b4", "1.0.2b5", true, false},
		{"two-digit beta", "1.0.2b9", "1.0.2b10", true, false},
		{"final", "1.0.2b5", "1.0.2", true, false},
		{"same", "1.0.2b5", "1.0.2b5", false, false},
		{"backup failure", "1.0.2b4", "1.0.2b5", true, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("OCTOP_HOME", home)
			root := portableDir()
			oldZip := filepath.Join(t.TempDir(), "old.zip")
			writeTestGreenZip(t, oldZip, test.installed)
			if err := unzipGreen(oldZip, root); err != nil {
				t.Fatal(err)
			}
			marker := filepath.Join(root, "keep.txt")
			database := filepath.Join(home, "octop.db")
			for _, path := range []string{marker, database} {
				if err := os.WriteFile(path, []byte("preserve"), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			newZip := filepath.Join(t.TempDir(), "new.zip")
			writeTestGreenZip(t, newZip, test.bundled)
			t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)

			backupCalls := 0
			previousBackup := runSQLiteBackup
			runSQLiteBackup = func(_ string, source, destination string) error {
				backupCalls++
				if source != database || portableVersion(root) != test.installed {
					t.Fatal("backup must precede replacement of the installed runtime")
				}
				if test.backupFails {
					return errors.New("backup unavailable")
				}
				return os.WriteFile(destination, []byte("backup"), 0o600)
			}
			t.Cleanup(func() { runSQLiteBackup = previousBackup })

			err := ensurePortable(LocaleZH, func(string) {})
			if (err != nil) != test.backupFails {
				t.Fatalf("ensurePortable error = %v, backupFails = %v", err, test.backupFails)
			}
			wantVersion := test.installed
			if test.upgrade && !test.backupFails {
				wantVersion = test.bundled
			}
			if got := portableVersion(root); got != wantVersion {
				t.Fatalf("portable version = %q, want %q", got, wantVersion)
			}
			wantBackups := 0
			if test.upgrade {
				wantBackups = 1
			}
			if backupCalls != wantBackups {
				t.Fatalf("backup calls = %d, want %d", backupCalls, wantBackups)
			}
			if data, err := os.ReadFile(database); err != nil || string(data) != "preserve" {
				t.Fatalf("database changed: %q, %v", data, err)
			}
			_, markerErr := os.Stat(marker)
			if test.upgrade && !test.backupFails {
				if !os.IsNotExist(markerErr) {
					t.Fatalf("old runtime was not replaced: %v", markerErr)
				}
			} else if markerErr != nil {
				t.Fatalf("installed runtime was not preserved: %v", markerErr)
			}
			if test.backupFails {
				return
			}
			if err := os.WriteFile(marker, []byte("keep on restart"), 0o600); err != nil {
				t.Fatal(err)
			}
			if err := ensurePortable(LocaleZH, func(string) {}); err != nil {
				t.Fatal(err)
			}
			if data, err := os.ReadFile(marker); err != nil || string(data) != "keep on restart" {
				t.Fatalf("runtime was replaced again: %q, %v", data, err)
			}
			if backupCalls != wantBackups {
				t.Fatal("repeated startup unexpectedly backed up the database again")
			}
		})
	}
}

func TestBundledPortableZipRequiresMatchingPackage(t *testing.T) {
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", filepath.Join(t.TempDir(), "missing.zip"))
	if _, err := bundledPortableZip(); err == nil {
		t.Fatal("missing bundled package should fail")
	}
}

func TestLaunchReadyRejectsFlattenedPythonSymlink(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()
	if err := os.MkdirAll(filepath.Join(root, "runtime", "bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "launch.py"), []byte("test"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "runtime", "bin", "python3"), []byte("python3.12"), 0o755); err != nil {
		t.Fatal(err)
	}
	if launchReady(root) {
		t.Fatal("flattened Python symlink must not be treated as a ready runtime")
	}
}

func TestFormatHealthWaitErrorIsActionableChinese(t *testing.T) {
	err := formatHealthWaitError(LocaleZH, "http://127.0.0.1:8088/", time.Minute, errors.New("connection refused"), 0)
	if err == nil {
		t.Fatal("expected an error")
	}
	msg := err.Error()
	for _, needle := range []string{
		"Octop 服务未在",
		"1 分钟",
		"http://127.0.0.1:8088",
		"请确认",
	} {
		if !strings.Contains(msg, needle) {
			t.Fatalf("friendly health error missing %q: %s", needle, msg)
		}
	}
	if strings.Contains(msg, "/api/health") {
		t.Fatalf("user-facing status should not expose the health path: %s", msg)
	}
	if strings.Contains(msg, "did not become healthy") {
		t.Fatalf("should not use the old English diagnostic: %s", msg)
	}
}

func TestFormatHealthWaitErrorUsesEnglishWhenLocaleIsEn(t *testing.T) {
	msg := formatHealthWaitError(LocaleEN, "http://127.0.0.1:8088", time.Minute, errors.New("connection refused"), 0).Error()
	for _, needle := range []string{
		"Octop did not become ready within",
		"1 minute",
		"http://127.0.0.1:8088",
		"make sure Octop is running",
	} {
		if !strings.Contains(msg, needle) {
			t.Fatalf("English health error missing %q: %s", needle, msg)
		}
	}
	if strings.Contains(msg, "服务未在") || strings.Contains(msg, "请确认") {
		t.Fatalf("English locale should not use Chinese splash copy: %s", msg)
	}
}

func TestFormatHealthWaitErrorUsesServiceNotReadyHintOn5xx(t *testing.T) {
	zh := formatHealthWaitError(LocaleZH, "http://127.0.0.1:8088", 2*time.Minute, nil, 503).Error()
	if !strings.Contains(zh, "2 分钟") || !strings.Contains(zh, "尚未就绪") {
		t.Fatalf("zh 5xx hint: %s", zh)
	}
	en := formatHealthWaitError(LocaleEN, "http://127.0.0.1:8088", 2*time.Minute, nil, 503).Error()
	if !strings.Contains(en, "2 minutes") || !strings.Contains(en, "not ready yet") {
		t.Fatalf("en 5xx hint: %s", en)
	}
}

func TestWaitHealthSucceedsOnOK(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(srv.Close)
	if err := waitHealth(LocaleZH, srv.URL, time.Second); err != nil {
		t.Fatal(err)
	}
}

func TestWaitHealthTimesOutWithFriendlyMessage(t *testing.T) {
	err := waitHealth(LocaleEN, "http://127.0.0.1:1", 50*time.Millisecond)
	if err == nil {
		t.Fatal("closed port should time out")
	}
	if strings.Contains(err.Error(), "did not become healthy") {
		t.Fatalf("should not use the old English diagnostic: %s", err)
	}
	if !strings.Contains(err.Error(), "Octop did not become ready within") {
		t.Fatalf("timeout should follow the desktop locale: %s", err)
	}
}

type testGreenZipFile struct{ name, content string }

func TestUnzipGreenRejectsLinksOutsideStaging(t *testing.T) {
	for _, absolute := range []bool{false, true} {
		name := "relative"
		if absolute {
			name = "absolute"
		}
		t.Run(name, func(t *testing.T) {
			parent := t.TempDir()
			outside := filepath.Join(parent, "outside")
			if err := os.Mkdir(outside, 0o700); err != nil {
				t.Fatal(err)
			}
			sentinel := filepath.Join(outside, "report.pptx")
			if err := os.WriteFile(sentinel, []byte("existing report"), 0o600); err != nil {
				t.Fatal(err)
			}
			target := "../outside"
			if absolute {
				target = outside
			}
			data := greenZipWithLink(t, target, "alias/report.pptx")
			extractErr := unzipGreenBytes(data, filepath.Join(parent, "staging"))
			if content, err := os.ReadFile(sentinel); err != nil || string(content) != "existing report" {
				t.Fatalf("extraction changed the external report: %q, %v", content, err)
			}
			if !errors.Is(extractErr, errInvalidPortableArchive) {
				t.Fatalf("external symbolic link returned %v, want invalid archive", extractErr)
			}
		})
	}
}

func TestEnsurePortableRejectsUnsafeFirstLaunch(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	bundle := filepath.Join(t.TempDir(), "portable.zip")
	if err := os.WriteFile(bundle, greenZipWithLink(t, "../outside", "alias/report.pptx"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", bundle)
	for _, locale := range []Locale{LocaleZH, LocaleEN} {
		err := ensurePortableAtStartup(locale, func(string) {})
		if want := desktopText(locale, copyErrorInvalidRuntime); err == nil || err.Error() != want {
			t.Fatalf("first launch returned %v, want %q", err, want)
		}
		if _, err := os.Lstat(portableDir() + ".new"); !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("invalid bundle left a staged runtime: %v", err)
		}
	}
}

func TestUnzipGreenPreservesInternalRuntimeLink(t *testing.T) {
	if runtime.GOOS == "windows" {
		probe := t.TempDir()
		if err := os.Symlink(".", filepath.Join(probe, "link")); err != nil {
			t.Skipf("symbolic links are unavailable: %v", err)
		}
	}
	dest := filepath.Join(t.TempDir(), "staging")
	if err := unzipGreenBytes(greenZipWithLink(t, "runtime/python3.12", "runtime/python3.12"), dest); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dest, "alias")
	if target, err := os.Readlink(link); err != nil || filepath.ToSlash(target) != "runtime/python3.12" {
		t.Fatalf("runtime link target = %q, error = %v", target, err)
	}
	if data, err := os.ReadFile(link); err != nil || string(data) != "archive contents" {
		t.Fatalf("runtime link contents = %q, error = %v", data, err)
	}
}

func greenZipWithLink(t *testing.T, linkTarget, fileName string) []byte {
	t.Helper()
	var archive bytes.Buffer
	writer := zip.NewWriter(&archive)
	header := &zip.FileHeader{Name: "Octop-test/alias"}
	header.SetMode(os.ModeSymlink | 0o755)
	link, err := writer.CreateHeader(header)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := link.Write([]byte(linkTarget)); err != nil {
		t.Fatal(err)
	}
	file, err := writer.Create("Octop-test/" + fileName)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write([]byte("archive contents")); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return archive.Bytes()
}

func writeTestGreenZip(t *testing.T, path, version string, extras ...testGreenZipFile) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	w := zip.NewWriter(f)
	files := []string{
		"Octop-test/launch.py",
		"Octop-test/VERSION.txt",
		"Octop-test/packages/octop-" + version + ".dist-info/METADATA",
	}
	if runtime.GOOS == "windows" {
		files = append(files, "Octop-test/runtime/python.exe")
	} else {
		files = append(files,
			"Octop-test/runtime/bin/python3",
			"Octop-test/runtime/bin/python3.12",
		)
	}
	for _, name := range files {
		header := &zip.FileHeader{Name: name, Method: zip.Store}
		content := []byte("test executable payload")
		if strings.HasSuffix(name, "/VERSION.txt") {
			content = []byte("platform=test\noctop_version=" + version + "\n")
		} else if strings.HasSuffix(name, "/METADATA") {
			content = []byte("Name: octop\nVersion: " + version + "\n")
		} else if strings.HasSuffix(name, "/python3") {
			header.SetMode(os.ModeSymlink | 0o755)
			content = []byte("python3.12")
		} else {
			header.SetMode(0o755)
			if strings.HasSuffix(name, "/python3.12") || strings.HasSuffix(name, "/python.exe") {
				content = make([]byte, 2048)
			}
		}
		entry, err := w.CreateHeader(header)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write(content); err != nil {
			t.Fatal(err)
		}
	}
	for _, extra := range extras {
		entry, err := w.Create("Octop-" + greenPlat() + "/" + extra.name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(extra.content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
}

func writeMetadataOnlyZip(t *testing.T, path, version string) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	w := zip.NewWriter(f)
	entry, err := w.Create("Octop-test/packages/octop-" + version + ".dist-info/METADATA")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := entry.Write([]byte("Name: octop\nVersion: " + version + "\n")); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
}

func writeVersionOnlyZip(t *testing.T, path, version string) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	w := zip.NewWriter(f)
	entry, err := w.Create("Octop-test/VERSION.txt")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := entry.Write([]byte("octop_version=" + version + "\n")); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
}
