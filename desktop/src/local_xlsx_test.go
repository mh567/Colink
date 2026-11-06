package main

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/xuri/excelize/v2"
)

func TestLocalXLSXTaskCreatesCalculatedEditableWorkbook(t *testing.T) {
	var sent string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, "/xlsx/") {
			t.Errorf("wrong feature endpoint: %s", r.URL.Path)
		}
		if strings.HasSuffix(r.URL.Path, "/generate") {
			var body struct {
				Text string `json:"text"`
			}
			json.NewDecoder(r.Body).Decode(&body)
			sent = body.Text
			json.NewEncoder(w).Encode(map[string]string{"text": `{"sheets":[{"name":"汇总","rows":[["项目","金额"],["甲",12.5],["乙",7.5],["总计",{"formula":"SUM(B2:B3)"}]]}]}`})
		} else {
			w.Write([]byte(`{"allowed":true}`))
		}
	}))
	defer server.Close()
	dir := filepath.Join(t.TempDir(), "中文台账")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	book := excelize.NewFile()
	defer book.Close()
	book.SetCellStr("Sheet1", "A1", "项目")
	book.SetCellStr("Sheet1", "B1", "金额")
	book.SetCellStr("Sheet1", "A2", "甲")
	book.SetCellFloat("Sheet1", "B2", 12.5, -1, 64)
	book.SetCellStr("Sheet1", "A3", "乙")
	book.SetCellFloat("Sheet1", "B3", 7.5, -1, 64)
	if err := book.SaveAs(filepath.Join(dir, "输入.xlsx")); err != nil {
		t.Fatal(err)
	}
	worker := newLocalDOCX(t.TempDir())
	session := docxSession{Base: server.URL, DeviceID: "mine", UserID: 1, Token: "token", Secret: "secret", Context: context.Background()}
	grant, err := worker.grant(session, dir, "rw")
	if err != nil {
		t.Fatal(err)
	}
	request := docxRequest{Format: "xlsx", SourceGrant: grant.ID, OutputGrant: grant.ID, Source: "输入.xlsx", Output: "成果.xlsx", Instruction: "汇总金额"}
	task, err := worker.run(session, request, func() error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != "completed" || !strings.Contains(sent, "12.5") || !strings.Contains(sent, "项目") {
		t.Fatalf("task=%+v sent=%s", task, sent)
	}
	result, err := excelize.OpenFile(task.Output)
	if err != nil {
		t.Fatal(err)
	}
	defer result.Close()
	value, err := result.GetCellValue("汇总", "B4")
	if err != nil || value != "20" {
		t.Fatalf("cached total=%q %v", value, err)
	}
	kind, err := result.GetCellType("汇总", "B4")
	if err != nil || (kind != excelize.CellTypeNumber && kind != excelize.CellTypeUnset) {
		t.Fatalf("numeric formula cache has type %v: %v", kind, err)
	}
	formula, err := result.GetCellFormula("汇总", "B4")
	if err != nil || formula != "SUM(B2:B3)" {
		t.Fatalf("formula=%q %v", formula, err)
	}
	result.SetCellFloat("汇总", "B2", 22.5, -1, 64)
	value, err = result.CalcCellValue("汇总", "B4")
	if err != nil || value != "30" {
		t.Fatalf("edited total=%q %v", value, err)
	}
	if _, err = worker.run(session, request, func() error { return nil }); err == nil {
		t.Fatal("overwrote output")
	}
	persisted, err := newLocalDOCX(worker.home).state(session)
	if err != nil || len(persisted.Tasks) != 2 || persisted.Tasks[0].Format != "xlsx" {
		t.Fatalf("state=%+v %v", persisted, err)
	}
}

func TestXLSXRejectsInvalidModelDataAndUnsafeFormulas(t *testing.T) {
	for _, body := range []string{
		`not json`, `{"sheets":[]}`, `{"sheets":[{"name":"表","rows":[[{}]]}]}`,
		`{"sheets":[{"name":"表","rows":[[{"formula":"WEBSERVICE(\"https://example.com\")"}]]}]}`,
		`{"sheets":[{"name":"表","rows":[[{"formula":"SUM([other.xlsx]Sheet1!A1)"}]]}]}`,
		`{"sheets":[{"name":"表","rows":[[{"formula":"1/0"}]]}]}`,
		`{"sheets":[{"name":"表","rows":[[{"formula":"A1+1"}]]}]}`,
		`{"sheets":[{"name":"表","rows":[[{"formula":"B1"},{"formula":"A1"}]]}]}`,
	} {
		t.Run(body, func(t *testing.T) {
			if _, err := makeXLSX(body); err == nil {
				t.Fatal("invalid output accepted")
			}
		})
	}
}

func TestLocalXLSXFailuresNeverCreateArtifact(t *testing.T) {
	for _, scenario := range []string{"readonly", "other-user", "other-device", "traversal", "symlink", "hardlink", "read-denied", "create-denied", "invalid-result", "changed-session"} {
		t.Run(scenario, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasSuffix(r.URL.Path, "/generate") {
					text := `{"sheets":[{"name":"结果","rows":[["汇总",20]]}]}`
					if scenario == "invalid-result" {
						text = "invalid"
					}
					json.NewEncoder(w).Encode(map[string]string{"text": text})
					return
				}
				var body struct {
					Action string `json:"action"`
				}
				json.NewDecoder(r.Body).Decode(&body)
				if scenario == body.Action+"-denied" {
					w.WriteHeader(403)
					return
				}
				w.Write([]byte(`{"allowed":true}`))
			}))
			defer server.Close()
			dir := t.TempDir()
			book := excelize.NewFile()
			defer book.Close()
			book.SetCellStr("Sheet1", "A1", "测试")
			if err := book.SaveAs(filepath.Join(dir, "输入.xlsx")); err != nil {
				t.Fatal(err)
			}
			worker := newLocalDOCX(t.TempDir())
			session := docxSession{Base: server.URL, DeviceID: "mine", UserID: 1, Context: context.Background()}
			mode := "rw"
			if scenario == "readonly" {
				mode = "ro"
			}
			grant, err := worker.grant(session, dir, mode)
			if err != nil {
				t.Fatal(err)
			}
			request := docxRequest{Format: "xlsx", SourceGrant: grant.ID, OutputGrant: grant.ID, Source: "输入.xlsx", Output: "成果.xlsx", Instruction: "汇总"}
			switch scenario {
			case "other-user":
				session.UserID = 2
			case "other-device":
				session.DeviceID = "other"
			case "traversal":
				request.Source = "../输入.xlsx"
			case "symlink", "hardlink":
				link := filepath.Join(dir, "链接.xlsx")
				var e error
				if scenario == "symlink" {
					e = os.Symlink(filepath.Join(dir, "输入.xlsx"), link)
				} else {
					e = os.Link(filepath.Join(dir, "输入.xlsx"), link)
				}
				if e != nil {
					t.Skipf("link unavailable: %v", e)
				}
				request.Source = "链接.xlsx"
			}
			current := func() error {
				if scenario == "changed-session" {
					return fmt.Errorf("session changed")
				}
				return nil
			}
			task, err := worker.run(session, request, current)
			if err == nil || task.Status == "completed" {
				t.Fatalf("false success: %+v %v", task, err)
			}
			if _, err = os.Stat(filepath.Join(dir, "成果.xlsx")); !os.IsNotExist(err) {
				t.Fatalf("artifact exists: %v", err)
			}
		})
	}
}

func TestXLSXRejectsLongFormulaChainsDeterministically(t *testing.T) {
	rows := make([][]any, 102)
	rows[101] = []any{1}
	for i := 0; i < 101; i++ {
		rows[i] = []any{map[string]any{"formula": fmt.Sprintf("A%d+1", i+2)}}
	}
	raw, _ := json.Marshal(xlsxData{Sheets: []xlsxSheet{{Name: "链", Rows: rows}}})
	for i := 0; i < 5; i++ {
		if _, err := makeXLSX(string(raw)); err == nil {
			t.Fatal("accepted dependency chain over 100")
		}
	}
}

func TestXLSXSourcePreservesFormattedDatesAndIdentifiers(t *testing.T) {
	for _, date1904 := range []bool{false, true} {
		t.Run(fmt.Sprint(date1904), func(t *testing.T) {
			f := excelize.NewFile()
			defer f.Close()
			f.SetWorkbookProps(&excelize.WorkbookPropsOptions{Date1904: &date1904})
			serial := 46332.0
			if date1904 {
				serial -= 1462
			}
			f.SetCellFloat("Sheet1", "A1", serial, -1, 64)
			dateFormat := "yyyy-mm-dd"
			style, _ := f.NewStyle(&excelize.Style{CustomNumFmt: &dateFormat})
			f.SetCellStyle("Sheet1", "A1", "A1", style)
			f.SetCellInt("Sheet1", "B1", 12)
			idFormat := "00000"
			style, _ = f.NewStyle(&excelize.Style{CustomNumFmt: &idFormat})
			f.SetCellStyle("Sheet1", "B1", "B1", style)
			f.SetCellFormula("Sheet1", "C1", "B1*2")
			buffer, err := f.WriteToBuffer()
			if err != nil {
				t.Fatal(err)
			}
			text, err := readXLSX(bytes.NewReader(buffer.Bytes()), int64(buffer.Len()))
			if err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(text, "2026-11-06") || !strings.Contains(text, "00012") || !strings.Contains(text, "24") {
				t.Fatalf("lost source semantics: %s", text)
			}
		})
	}
}

func TestXLSXRejectsOversizedSparseWorksheetBeforeParsing(t *testing.T) {
	var buffer bytes.Buffer
	z := zip.NewWriter(&buffer)
	entry, _ := z.Create("xl/worksheets/sheet1.xml")
	fmt.Fprint(entry, "<worksheet><sheetData>"+strings.Repeat("<row/>", 10001)+"</sheetData></worksheet>")
	z.Close()
	if _, err := readXLSX(bytes.NewReader(buffer.Bytes()), int64(buffer.Len())); err == nil || !strings.Contains(err.Error(), "10000") {
		t.Fatalf("expected row limit: %v", err)
	}
}
