package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"math"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/xuri/excelize/v2"
)

const xlsxCellLimit = 10000
const xlsxUnzipLimit = 40 * 1024 * 1024

type xlsxSheet struct {
	Name string  `json:"name"`
	Rows [][]any `json:"rows"`
}
type xlsxData struct {
	Sheets []xlsxSheet `json:"sheets"`
}

func xlsxFilename(name string) error {
	if name == "" || !filepath.IsLocal(name) || filepath.Base(name) != name || strings.ContainsAny(name, "/\\:") || !strings.EqualFold(filepath.Ext(name), ".xlsx") || strings.TrimSpace(name) != name {
		return errors.New("请输入授权目录内的 XLSX 文件名（不含子目录）")
	}
	return nil
}

var xlsxFormulaToken = regexp.MustCompile(`(?i)SUM|AVERAGE|MIN|MAX|COUNT|\$?[A-Z]{1,3}\$?[1-9][0-9]{0,4}|[0-9]+(?:\.[0-9]+)?|[+*/(),:\s-]`)
var xlsxReference = regexp.MustCompile(`(?i)\$?([A-Z]{1,3})\$?([1-9][0-9]{0,4})`)

func checkXLSXFormula(formula string) error {
	if len(formula) == 0 || len(formula) > 1000 || xlsxFormulaToken.ReplaceAllString(formula, "") != "" {
		return errors.New("公式仅支持同表单元格、算术及 SUM/AVERAGE/MIN/MAX/COUNT")
	}
	for _, ref := range xlsxReference.FindAllStringSubmatch(formula, -1) {
		col, err := excelize.ColumnNameToNumber(ref[1])
		row, _ := strconv.Atoi(ref[2])
		if err != nil || col*row > xlsxCellLimit {
			return errors.New("公式引用超出 10000 单元格范围")
		}
	}
	return nil
}

// Bound sparse coordinates before GetRows allocates rows; XML stays in memory.
func checkXLSXArchive(reader io.ReaderAt, size int64) error {
	if size > docxFileLimit {
		return errors.New("XLSX 超过 20 MB")
	}
	z, err := zip.NewReader(reader, size)
	if err != nil {
		return errors.New("不是有效的 XLSX 文件")
	}
	total := uint64(0)
	cells := 0
	seen := map[string]bool{}
	for _, part := range z.File {
		if seen[part.Name] {
			return errors.New("XLSX 包含重复文件")
		}
		seen[part.Name] = true
		if part.UncompressedSize64 > xlsxUnzipLimit || total > xlsxUnzipLimit-part.UncompressedSize64 {
			return errors.New("XLSX 解压数据超过 40 MB")
		}
		total += part.UncompressedSize64
		if strings.HasPrefix(part.Name, "xl/externalLinks/") || strings.HasSuffix(part.Name, "vbaProject.bin") {
			return errors.New("不支持外部链接或宏")
		}
		if !strings.HasPrefix(part.Name, "xl/worksheets/") || !strings.HasSuffix(part.Name, ".xml") {
			continue
		}
		stream, err := part.Open()
		if err != nil {
			return err
		}
		decoder := xml.NewDecoder(io.LimitReader(stream, xlsxUnzipLimit+1))
		maxRow, maxCol := 0, 0
		rowCount, cellCount := 0, 0
		cellRefs := map[string]bool{}
		for {
			token, e := decoder.Token()
			if e == io.EOF {
				break
			}
			if e != nil {
				stream.Close()
				return errors.New("工作表解析失败")
			}
			if start, ok := token.(xml.StartElement); ok && start.Name.Local == "row" {
				rowCount++
				if rowCount > xlsxCellLimit {
					stream.Close()
					return errors.New("工作表超过 10000 行")
				}
				for _, attr := range start.Attr {
					if attr.Name.Local == "r" {
						row, e := strconv.Atoi(attr.Value)
						if e != nil || row < 1 || row > xlsxCellLimit {
							stream.Close()
							return errors.New("工作表行号超出范围")
						}
					}
				}
			}
			if start, ok := token.(xml.StartElement); ok && start.Name.Local == "c" {
				ref := ""
				for _, attr := range start.Attr {
					if attr.Name.Local == "r" {
						ref = attr.Value
					}
				}
				cellCount++
				if cellCount > xlsxCellLimit || cellRefs[ref] {
					stream.Close()
					return errors.New("工作表单元格重复或超过 10000 个")
				}
				cellRefs[ref] = true
				col, row, e := excelize.CellNameToCoordinates(ref)
				if e != nil || col*row > xlsxCellLimit {
					stream.Close()
					return errors.New("工作表超出 10000 单元格范围")
				}
				if col > maxCol {
					maxCol = col
				}
				if row > maxRow {
					maxRow = row
				}
			}
		}
		stream.Close()
		cells += maxRow * maxCol
		if cells > xlsxCellLimit {
			return errors.New("工作簿超过 10000 单元格")
		}
	}
	return nil
}

var xlsxRange = regexp.MustCompile(`(?i)\$?[A-Z]{1,3}\$?[1-9][0-9]{0,4}(?:\s*:\s*\$?[A-Z]{1,3}\$?[1-9][0-9]{0,4})?`)

func checkXLSXDependencies(f *excelize.File) error {
	edges := map[string][]string{}
	total := 0
	for _, sheet := range f.GetSheetList() {
		rows, err := f.GetRows(sheet)
		if err != nil {
			return err
		}
		for r, row := range rows {
			for c := range row {
				cell, _ := excelize.CoordinatesToCellName(c+1, r+1)
				formula, err := f.GetCellFormula(sheet, cell)
				if err != nil {
					return err
				}
				if formula == "" {
					continue
				}
				if err = checkXLSXFormula(formula); err != nil {
					return err
				}
				key := sheet + "!" + cell
				for _, ref := range xlsxRange.FindAllString(formula, -1) {
					ends := strings.Split(strings.ReplaceAll(strings.ToUpper(ref), "$", ""), ":")
					c1, r1, _ := excelize.CellNameToCoordinates(strings.TrimSpace(ends[0]))
					c2, r2 := c1, r1
					if len(ends) == 2 {
						c2, r2, _ = excelize.CellNameToCoordinates(strings.TrimSpace(ends[1]))
					}
					if c1 > c2 {
						c1, c2 = c2, c1
					}
					if r1 > r2 {
						r1, r2 = r2, r1
					}
					if (c2-c1+1)*(r2-r1+1) > xlsxCellLimit {
						return errors.New("公式区域超过 10000 单元格")
					}
					total += (c2 - c1 + 1) * (r2 - r1 + 1)
					if total > 100000 {
						return errors.New("公式引用总量超过 100000")
					}
					for rr := r1; rr <= r2; rr++ {
						for cc := c1; cc <= c2; cc++ {
							target, _ := excelize.CoordinatesToCellName(cc, rr)
							edges[key] = append(edges[key], sheet+"!"+target)
						}
					}
				}
			}
		}
	}
	visited := map[string]int{}
	depths := map[string]int{}
	var visit func(string, int) error
	visit = func(cell string, depth int) error {
		if visited[cell] == 1 {
			return errors.New("公式存在循环引用")
		}
		if visited[cell] == 2 {
			if depth+depths[cell] > 100 {
				return errors.New("公式依赖超过 100 层")
			}
			return nil
		}
		if depth > 100 {
			return errors.New("公式依赖超过 100 层")
		}
		visited[cell] = 1
		for _, target := range edges[cell] {
			if err := visit(target, depth+1); err != nil {
				return err
			}
			if depths[target]+1 > depths[cell] {
				depths[cell] = depths[target] + 1
			}
		}
		visited[cell] = 2
		return nil
	}
	for cell := range edges {
		if err := visit(cell, 0); err != nil {
			return err
		}
	}
	return nil
}

func readXLSX(reader io.ReaderAt, size int64) (string, error) {
	if err := checkXLSXArchive(reader, size); err != nil {
		return "", err
	}
	f, err := excelize.OpenReader(io.NewSectionReader(reader, 0, size), excelize.Options{UnzipSizeLimit: xlsxUnzipLimit, UnzipXMLSizeLimit: xlsxUnzipLimit})
	if err != nil {
		return "", errors.New("无法读取 XLSX 工作簿")
	}
	defer f.Close()
	names := f.GetSheetList()
	if len(names) == 0 || len(names) > 10 {
		return "", errors.New("工作簿须包含 1 至 10 个工作表")
	}
	type sourceSheet struct {
		xlsxSheet
		DisplayRows [][]string `json:"display_rows"`
	}
	data := struct {
		Sheets []sourceSheet `json:"sheets"`
	}{}
	count := 0
	// Validate every formula before calculating any cell, including its dependencies.
	for _, name := range names {
		rows, e := f.GetRows(name, excelize.Options{RawCellValue: true})
		if e != nil {
			return "", e
		}
		sheet := sourceSheet{xlsxSheet: xlsxSheet{Name: name}}
		for r, row := range rows {
			count += len(row)
			if count > xlsxCellLimit {
				return "", errors.New("工作簿超过 10000 单元格")
			}
			values := make([]any, len(row))
			display := make([]string, len(row))
			for c, value := range row {
				cell, _ := excelize.CoordinatesToCellName(c+1, r+1)
				formula, e := f.GetCellFormula(name, cell)
				if e != nil {
					return "", e
				}
				if formula != "" {
					if e = checkXLSXFormula(formula); e != nil {
						return "", fmt.Errorf("%s!%s: %w", name, cell, e)
					}
				}
				kind, e := f.GetCellType(name, cell)
				if e != nil {
					return "", e
				}
				display[c], e = f.GetCellValue(name, cell)
				if e != nil {
					return "", e
				}
				values[c] = value
				if kind == excelize.CellTypeNumber || kind == excelize.CellTypeUnset {
					if n, e := strconv.ParseFloat(value, 64); e == nil && !math.IsInf(n, 0) && !math.IsNaN(n) {
						values[c] = n
					}
				} else if kind == excelize.CellTypeBool {
					values[c] = value == "1" || value == "TRUE"
				} else if kind == excelize.CellTypeError {
					return "", fmt.Errorf("%s!%s 含错误值", name, cell)
				}
			}
			sheet.Rows = append(sheet.Rows, values)
			sheet.DisplayRows = append(sheet.DisplayRows, display)
		}
		data.Sheets = append(data.Sheets, sheet)
	}
	if err = checkXLSXDependencies(f); err != nil {
		return "", err
	}
	for si, sheet := range data.Sheets {
		for r, row := range sheet.Rows {
			for c := range row {
				cell, _ := excelize.CoordinatesToCellName(c+1, r+1)
				formula, _ := f.GetCellFormula(sheet.Name, cell)
				if formula != "" {
					value, e := f.CalcCellValue(sheet.Name, cell, excelize.Options{RawCellValue: true, MaxCalcIterations: 100})
					if e != nil {
						return "", fmt.Errorf("%s!%s 公式计算失败", sheet.Name, cell)
					}
					n, e := strconv.ParseFloat(value, 64)
					if e != nil || math.IsInf(n, 0) || math.IsNaN(n) {
						return "", errors.New("公式结果不是有效数字")
					}
					data.Sheets[si].Rows[r][c] = n
					display, e := f.CalcCellValue(sheet.Name, cell, excelize.Options{MaxCalcIterations: 100})
					if e != nil {
						return "", e
					}
					data.Sheets[si].DisplayRows[r][c] = display
				}
			}
		}
	}
	raw, err := json.Marshal(data)
	if err != nil {
		return "", err
	}
	if count == 0 || utf8.RuneCount(raw) > docxTextLimit {
		return "", errors.New("工作簿为空或提取数据超过 60000 字符")
	}
	return string(raw), nil
}

func makeXLSX(text string) ([]byte, error) {
	if utf8.RuneCountInString(text) > docxTextLimit {
		return nil, errors.New("模型表格超过 60000 字符")
	}
	var data xlsxData
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&data); err != nil {
		return nil, errors.New("模型未返回有效表格 JSON")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return nil, errors.New("模型返回了额外内容")
	}
	if len(data.Sheets) == 0 || len(data.Sheets) > 10 {
		return nil, errors.New("成果须包含 1 至 10 个工作表")
	}
	f := excelize.NewFile()
	defer f.Close()
	type formulaCell struct {
		sheet, cell, formula string
		value                float64
	}
	formulas := []formulaCell{}
	names := map[string]bool{}
	count := 0
	for index, sheet := range data.Sheets {
		name := strings.ToLower(sheet.Name)
		if sheet.Name == "" || names[name] || len(sheet.Rows) == 0 {
			return nil, errors.New("工作表名称重复、为空或没有数据")
		}
		names[name] = true
		var err error
		if index == 0 {
			err = f.SetSheetName("Sheet1", sheet.Name)
		} else {
			_, err = f.NewSheet(sheet.Name)
		}
		if err != nil {
			return nil, err
		}
		width := 0
		for _, row := range sheet.Rows {
			if len(row) > width {
				width = len(row)
			}
		}
		count += len(sheet.Rows) * width
		if width == 0 || count > xlsxCellLimit {
			return nil, errors.New("成果为空或超过 10000 单元格")
		}
		for r, row := range sheet.Rows {
			for c, value := range row {
				cell, _ := excelize.CoordinatesToCellName(c+1, r+1)
				switch v := value.(type) {
				case map[string]any:
					formula, ok := v["formula"].(string)
					if !ok || len(v) != 1 {
						return nil, errors.New("无效公式单元格")
					}
					if err = checkXLSXFormula(formula); err != nil {
						return nil, err
					}
					if err = f.SetCellFormula(sheet.Name, cell, formula); err != nil {
						return nil, err
					}
					formulas = append(formulas, formulaCell{sheet: sheet.Name, cell: cell, formula: formula})
				case string:
					if utf8.RuneCountInString(v) > 32767 {
						return nil, errors.New("单元格文本超过 32767 字符")
					}
					err = f.SetCellStr(sheet.Name, cell, v)
				case nil, bool, float64:
					err = f.SetCellValue(sheet.Name, cell, value)
				default:
					return nil, errors.New("无效单元格值")
				}
				if err != nil {
					return nil, err
				}
			}
		}
	}
	if err := checkXLSXDependencies(f); err != nil {
		return nil, err
	}
	for i, item := range formulas {
		value, err := f.CalcCellValue(item.sheet, item.cell, excelize.Options{RawCellValue: true, MaxCalcIterations: 100})
		if err != nil {
			return nil, fmt.Errorf("%s!%s 公式计算失败", item.sheet, item.cell)
		}
		n, err := strconv.ParseFloat(value, 64)
		if err != nil || math.IsInf(n, 0) || math.IsNaN(n) {
			return nil, errors.New("公式结果不是有效数字")
		}
		formulas[i].value = n
	}
	// Set cached numeric results, then restore formulas so office applications can edit them.
	for _, item := range formulas {
		if err := f.SetCellFloat(item.sheet, item.cell, item.value, -1, 64); err != nil {
			return nil, err
		}
		if err := f.SetCellFormula(item.sheet, item.cell, item.formula); err != nil {
			return nil, err
		}
	}
	buffer, err := f.WriteToBuffer()
	if err != nil {
		return nil, err
	}
	if buffer.Len() > docxFileLimit {
		return nil, errors.New("成果超过 20 MB")
	}
	return numericXLSXCaches(buffer.Bytes())
}

// Excelize 2.11.0 labels formula caches as strings. Our generated formulas are
// numeric; normalize only these new worksheets, never an input workbook.
func numericXLSXCaches(data []byte) ([]byte, error) {
	archive, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, err
	}
	var result bytes.Buffer
	writer := zip.NewWriter(&result)
	for _, part := range archive.File {
		if !strings.HasPrefix(part.Name, "xl/worksheets/") || !strings.HasSuffix(part.Name, ".xml") {
			if err = writer.Copy(part); err != nil {
				return nil, err
			}
			continue
		}
		reader, e := part.Open()
		if e != nil {
			return nil, e
		}
		content, e := io.ReadAll(reader)
		reader.Close()
		if e != nil {
			return nil, e
		}
		content = bytes.ReplaceAll(content, []byte(` t="str"`), []byte(` t="n"`))
		entry, e := writer.Create(part.Name)
		if e != nil {
			return nil, e
		}
		if _, e = entry.Write(content); e != nil {
			return nil, e
		}
	}
	if err = writer.Close(); err != nil {
		return nil, err
	}
	return result.Bytes(), nil
}
