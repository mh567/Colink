package main

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestPortableVersionPrefersNewestPackageMetadata(t *testing.T) {
	for _, test := range []struct {
		versions       []string
		fromFile, want string
	}{
		{[]string{"0.9.29", "0.9.32"}, "0.9.28", "0.9.32"},
		{[]string{"1.0.2b4", "1.0.2b5"}, "1.0.2b3", "1.0.2b5"},
		{[]string{"1.0.2b9", "1.0.2b10", "1.0.2b4"}, "1.0.2b3", "1.0.2b10"},
	} {
		t.Run(test.want, func(t *testing.T) {
			root := t.TempDir()
			for _, version := range test.versions {
				path := filepath.Join(root, "packages", "octop-"+version+".dist-info", "METADATA")
				if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte("Version: "+version+"\n"), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			if err := os.WriteFile(filepath.Join(root, "VERSION.txt"), []byte("octop_version="+test.fromFile+"\n"), 0o644); err != nil {
				t.Fatal(err)
			}
			if got := portableVersion(root); got != test.want {
				t.Fatalf("portable version = %q, want %q", got, test.want)
			}
		})
	}
}

func TestDesktopSQLitePathUsesConfiguredRelativePath(t *testing.T) {
	home := t.TempDir()
	config := `{"database":{"driver":"sqlite","sqlite_path":"state/control.db"}}`
	if err := os.WriteFile(filepath.Join(home, "config.json"), []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}

	path, isSQLite, err := desktopSQLitePath(home)
	if err != nil {
		t.Fatal(err)
	}
	if !isSQLite {
		t.Fatal("configured SQLite database was not detected")
	}
	want := filepath.Join(home, "state", "control.db")
	if path != want {
		t.Fatalf("SQLite path = %q, want %q", path, want)
	}
}

func TestDesktopSQLitePathSkipsPostgreSQL(t *testing.T) {
	home := t.TempDir()
	config := `{"database":{"driver":"postgresql"}}`
	if err := os.WriteFile(filepath.Join(home, "config.json"), []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}

	path, isSQLite, err := desktopSQLitePath(home)
	if err != nil {
		t.Fatal(err)
	}
	if isSQLite || path != "" {
		t.Fatalf("PostgreSQL resolved as SQLite: path=%q isSQLite=%v", path, isSQLite)
	}
}

func findHostPython() string {
	for _, candidate := range []string{"python3", "python"} {
		if path, err := exec.LookPath(candidate); err == nil {
			if exec.Command(path, "-c", "import sqlite3").Run() == nil {
				return path
			}
		}
	}
	return ""
}

func TestPortableOfflineUpgradePreservesStateAndNotifiesOnFailure(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()

	oldZip := filepath.Join(t.TempDir(), "old.zip")
	writeTestGreenZip(t, oldZip, "0.9.30")
	if err := unzipGreen(oldZip, root); err != nil {
		t.Fatal(err)
	}

	database := filepath.Join(home, "octop.db")
	hostPython := findHostPython()
	if hostPython != "" {
		seed := `import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute("CREATE TABLE accounts(id INTEGER PRIMARY KEY, username TEXT, device_id TEXT)")
conn.execute("INSERT INTO accounts VALUES (101, 'alice', 'win-device-01')")
conn.commit()
conn.close()`
		if output, err := exec.Command(hostPython, "-c", seed, database).CombinedOutput(); err != nil {
			t.Fatalf("seed sqlite db: %v: %s", err, output)
		}
	} else if err := os.WriteFile(database, []byte("sqlite-database-placeholder"), 0o600); err != nil {
		t.Fatal(err)
	}

	grantDir := t.TempDir()
	docxBytes, err := makeDOCX("会议纪要核心结论")
	if err != nil {
		t.Fatal(err)
	}
	docxArtifact := filepath.Join(grantDir, "现有产物.docx")
	if err := os.WriteFile(docxArtifact, docxBytes, 0o600); err != nil {
		t.Fatal(err)
	}

	session := docxSession{Base: "http://127.0.0.1:8088", DeviceID: "win-device-01", UserID: 101, Context: context.Background()}
	var grantID string
	func() {
		docxWorker := newLocalDOCX(home)
		defer docxWorker.close()
		grant, grantErr := docxWorker.grant(session, grantDir, "rw")
		if grantErr != nil {
			t.Fatal(grantErr)
		}
		grantID = grant.ID
		states, loadErr := docxWorker.load()
		if loadErr != nil {
			t.Fatal(loadErr)
		}
		st := states[session.scope()]
		st.Tasks = append(st.Tasks, docxTask{
			ID:          "task-101",
			Instruction: "汇总会议纪要",
			Output:      "现有产物.docx",
			Status:      "completed",
			Steps:       []string{"read", "generate"},
		})
		states[session.scope()] = st
		if saveErr := docxWorker.save(states); saveErr != nil {
			t.Fatal(saveErr)
		}
	}()

	newZip := filepath.Join(t.TempDir(), "new.zip")
	writeTestGreenZip(t, newZip, "0.9.33")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)

	previousBackup := runSQLiteBackup
	runSQLiteBackup = func(_ string, source string, destination string) error {
		if source != database {
			t.Fatalf("backup source = %q, want %q", source, database)
		}
		if hostPython != "" {
			return previousBackup(hostPython, source, destination)
		}
		return os.WriteFile(destination, []byte("sqlite-backup-content"), 0o600)
	}
	t.Cleanup(func() { runSQLiteBackup = previousBackup })

	var statusLog []string
	if err := ensurePortable(LocaleZH, func(msg string) { statusLog = append(statusLog, msg) }); err != nil {
		t.Fatalf("upgrade failed: %v", err)
	}

	wantBackupStatus := desktopText(LocaleZH, copyStatusBackupDatabase, "0.9.33")
	wantUpdatingStatus := desktopText(LocaleZH, copyStatusUpdatingRuntime)
	joinedStatus := strings.Join(statusLog, "\n")
	if !strings.Contains(joinedStatus, wantBackupStatus) || !strings.Contains(joinedStatus, wantUpdatingStatus) {
		t.Fatalf("missing expected upgrade statuses: %v", statusLog)
	}
	if got := portableVersion(root); got != "0.9.33" {
		t.Fatalf("portable version = %q, want 0.9.33", got)
	}

	backups, err := filepath.Glob(filepath.Join(home, "backups", "octop-desktop-pre-upgrade-*.db"))
	if err != nil || len(backups) != 1 {
		t.Fatalf("expected 1 backup, got %v", backups)
	}
	if hostPython != "" {
		verifySQL := `import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
rows = conn.execute("SELECT id, username, device_id FROM accounts").fetchall()
conn.close()
assert rows == [(101, "alice", "win-device-01")], rows`
		for _, dbPath := range []string{database, backups[0]} {
			if output, err := exec.Command(hostPython, "-c", verifySQL, dbPath).CombinedOutput(); err != nil {
				t.Fatalf("verify sqlite %s: %v: %s", dbPath, err, output)
			}
		}
	}

	rawDocx, err := os.ReadFile(docxArtifact)
	if err != nil {
		t.Fatalf("read artifact after upgrade: %v", err)
	}
	if text, err := readDOCX(bytes.NewReader(rawDocx), int64(len(rawDocx))); err != nil || !strings.Contains(text, "会议纪要核心结论") {
		t.Fatalf("existing docx unreadable after upgrade: %q %v", text, err)
	}

	restoredWorker := newLocalDOCX(home)
	t.Cleanup(restoredWorker.close)
	restoredState, err := restoredWorker.state(session)
	if err != nil {
		t.Fatalf("load restored state: %v", err)
	}
	if len(restoredState.Grants) != 1 || restoredState.Grants[0].ID != grantID {
		t.Fatalf("grant not preserved: %+v", restoredState.Grants)
	}
	if len(restoredState.Tasks) != 1 || restoredState.Tasks[0].ID != "task-101" || restoredState.Tasks[0].Status != "completed" {
		t.Fatalf("tasks not preserved: %+v", restoredState.Tasks)
	}

	badZip := filepath.Join(t.TempDir(), "corrupt.zip")
	writeVersionOnlyZip(t, badZip, "0.9.34")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", badZip)

	expectedKeep := desktopText(LocaleZH, copyStatusUpdateFailedKeep)
	var upgradeWarning string
	if err := ensurePortable(LocaleZH, func(msg string) {
		if msg == expectedKeep {
			upgradeWarning = msg
		}
	}); err != nil {
		t.Fatalf("expected graceful fallback, got error: %v", err)
	}
	if upgradeWarning != expectedKeep {
		t.Fatalf("upgrade warning = %q, want %q", upgradeWarning, expectedKeep)
	}

	var notifiedTitle, notifiedMessage string
	app := &App{
		notifyWarning: func(title, message string) {
			notifiedTitle = title
			notifiedMessage = message
		},
	}
	app.warnUpgradeFallback(LocaleZH, upgradeWarning)
	if notifiedTitle != desktopText(LocaleZH, copyErrorUpgradeFailed) || notifiedMessage != expectedKeep {
		t.Fatalf("warning dialog = (%q, %q), want (%q, %q)", notifiedTitle, notifiedMessage, desktopText(LocaleZH, copyErrorUpgradeFailed), expectedKeep)
	}
	if got := portableVersion(root); got != "0.9.33" {
		t.Fatalf("version changed on failure: %q", got)
	}
	rawDocx, err = os.ReadFile(docxArtifact)
	if err != nil {
		t.Fatalf("read artifact after failed upgrade: %v", err)
	}
	if text, err := readDOCX(bytes.NewReader(rawDocx), int64(len(rawDocx))); err != nil || !strings.Contains(text, "会议纪要核心结论") {
		t.Fatalf("artifact damaged after failed upgrade: %q %v", text, err)
	}
}

func TestAppShutdownClosesSharedDOCXWithoutBrowserClosingIt(t *testing.T) {
	home := t.TempDir()
	sharedDOCX := newLocalDOCX(home)
	browser := newLocalBrowserWithDOCX(home, sharedDOCX)

	browser.close()
	sharedDOCX.mu.Lock()
	closedByBrowser := sharedDOCX.closing
	sharedDOCX.mu.Unlock()
	if closedByBrowser {
		t.Fatal("localBrowser.close() closed a shared localDOCX instance")
	}

	app := &App{
		docx:    sharedDOCX,
		browser: browser,
		sleep:   &sleepGuard{},
	}
	if err := app.ServiceShutdown(); err != nil {
		t.Fatalf("ServiceShutdown: %v", err)
	}
	sharedDOCX.mu.Lock()
	closedByApp := sharedDOCX.closing
	sharedDOCX.mu.Unlock()
	if !closedByApp {
		t.Fatal("App.ServiceShutdown() did not close App.docx")
	}
}
