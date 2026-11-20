package main

import (
	"context"
	"os"
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

func TestWindowsOfflineUpdatePreservesStateAndArtifactsAndShowsFailure(t *testing.T) {
	home := t.TempDir()
	t.Setenv("OCTOP_HOME", home)
	root := portableDir()

	oldZip := filepath.Join(t.TempDir(), "old.zip")
	writeTestGreenZip(t, oldZip, "0.9.30")
	if err := unzipGreen(oldZip, root); err != nil {
		t.Fatal(err)
	}

	database := filepath.Join(home, "octop.db")
	if err := os.WriteFile(database, []byte("sqlite-database-with-accounts-and-tokens"), 0o600); err != nil {
		t.Fatal(err)
	}

	grantDir := t.TempDir()
	docxArtifact := filepath.Join(grantDir, "现有产物.docx")
	if err := os.WriteFile(docxArtifact, []byte("docx-binary-data"), 0o600); err != nil {
		t.Fatal(err)
	}

	docxWorker := newLocalDOCX(home)
	session := docxSession{Base: "http://127.0.0.1:8088", DeviceID: "win-device-01", UserID: 101, Context: context.Background()}
	grant, err := docxWorker.grant(session, grantDir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(docxWorker.close)

	states, err := docxWorker.load()
	if err != nil {
		t.Fatal(err)
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
	if err := docxWorker.save(states); err != nil {
		t.Fatal(err)
	}
	docxWorker.close()

	newZip := filepath.Join(t.TempDir(), "new.zip")
	writeTestGreenZip(t, newZip, "0.9.33")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZip)

	previousBackup := runSQLiteBackup
	runSQLiteBackup = func(_ string, source string, destination string) error {
		if source != database {
			t.Fatalf("backup source = %q, want %q", source, database)
		}
		return os.WriteFile(destination, []byte("sqlite-backup-content"), 0o600)
	}
	t.Cleanup(func() { runSQLiteBackup = previousBackup })

	var statusLog []string
	statusRecorder := func(msg string) {
		statusLog = append(statusLog, msg)
	}

	if err := ensurePortable(LocaleZH, statusRecorder); err != nil {
		t.Fatalf("upgrade failed: %v", err)
	}

	if got := portableVersion(root); got != "0.9.33" {
		t.Fatalf("portable version = %q, want 0.9.33", got)
	}

	if data, err := os.ReadFile(database); err != nil || string(data) != "sqlite-database-with-accounts-and-tokens" {
		t.Fatalf("database content modified or missing: %v", err)
	}
	backups, err := filepath.Glob(filepath.Join(home, "backups", "octop-desktop-pre-upgrade-*.db"))
	if err != nil || len(backups) != 1 {
		t.Fatalf("expected 1 backup, got %v", backups)
	}

	if data, err := os.ReadFile(docxArtifact); err != nil || string(data) != "docx-binary-data" {
		t.Fatalf("existing artifact corrupted: %v", err)
	}

	restoredWorker := newLocalDOCX(home)
	t.Cleanup(restoredWorker.close)
	restoredState, err := restoredWorker.state(session)
	if err != nil {
		t.Fatalf("load restored state: %v", err)
	}
	if len(restoredState.Grants) != 1 || restoredState.Grants[0].ID != grant.ID {
		t.Fatalf("grant not preserved: %+v", restoredState.Grants)
	}
	if len(restoredState.Tasks) != 1 || restoredState.Tasks[0].ID != "task-101" || restoredState.Tasks[0].Status != "completed" {
		t.Fatalf("tasks not preserved: %+v", restoredState.Tasks)
	}

	badZip := filepath.Join(t.TempDir(), "corrupt.zip")
	writeVersionOnlyZip(t, badZip, "0.9.34")
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", badZip)

	var failureLog []string
	if err := ensurePortable(LocaleZH, func(msg string) { failureLog = append(failureLog, msg) }); err != nil {
		t.Fatalf("expected graceful fallback, got error: %v", err)
	}

	foundKeepMsg := false
	expectedKeep := desktopText(LocaleZH, copyStatusUpdateFailedKeep)
	for _, msg := range failureLog {
		if strings.Contains(msg, expectedKeep) {
			foundKeepMsg = true
			break
		}
	}
	if !foundKeepMsg {
		t.Fatalf("expected status %q, got logs %v", expectedKeep, failureLog)
	}
	if got := portableVersion(root); got != "0.9.33" {
		t.Fatalf("version rolled back or changed on failure: %q", got)
	}
	if data, err := os.ReadFile(docxArtifact); err != nil || string(data) != "docx-binary-data" {
		t.Fatalf("artifact damaged after failed upgrade: %v", err)
	}
}
