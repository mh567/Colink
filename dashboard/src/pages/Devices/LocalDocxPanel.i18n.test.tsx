import { act, render, screen } from "@testing-library/react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { afterEach, expect, it, vi } from "vitest";
import en from "../../locales/en.json";
import zh from "../../locales/zh.json";
import LocalDocxPanel from "./LocalDocxPanel";

vi.unmock("react-i18next");

afterEach(() => {
  delete (window as Window & { _wails?: unknown })._wails;
});

it.each(["zh", "en"])(
  "translates OCR and PPTX task errors in %s while preserving existing errors",
  async (language) => {
    const i18n = createInstance();
    await i18n.init({
      lng: language,
      resources: { zh: { translation: zh }, en: { translation: en } },
      interpolation: { escapeValue: false },
    });
    (window as Window & { _wails?: unknown })._wails = { invoke: vi.fn() };
    render(
      <I18nextProvider i18n={i18n}>
        <LocalDocxPanel deviceId="mine" />
      </I18nextProvider>,
    );
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("colink:docx-status", {
          detail: {
            device_id: "mine",
            busy: false,
            grants: [],
            error: "OCR_MODELS_MISSING",
            tasks: [
              {
                id: "1",
                format: "ocr",
                instruction: "",
                source: "scan.pdf",
                output: "scan.txt",
                status: "failed",
                error: "OCR_TIMEOUT",
              },
              {
                id: "2",
                format: "docx",
                instruction: "",
                source: "source.docx",
                output: "out.docx",
                status: "failed",
                error: "Existing native error",
              },
              {
                id: "3",
                format: "pptx",
                instruction: "",
                source: "source.pptx",
                output: "report.pptx",
                status: "failed",
                error: "PPTX_INVALID_REPORT",
              },
              {
                id: "4",
                format: "pptx",
                instruction: "",
                source: "chart.pptx",
                output: "report.pptx",
                status: "failed",
                error: "PPTX_UNSUPPORTED_CHART",
              },
            ],
          },
        }),
      );
    });
    expect(
      screen.getByText(
        language === "zh"
          ? "离线识别模型缺失，请重新安装完整客户端。"
          : "Offline recognition models are missing. Reinstall the complete client.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        language === "zh"
          ? "识别超时，请拆分 PDF 后重试。"
          : "Recognition timed out. Split the PDF and try again.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        language === "zh"
          ? "汇报内容格式无效，请缩短任务描述后重试。"
          : "The report format is invalid. Shorten the instructions and try again.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        language === "zh"
          ? "请在演示文稿副本中用表格替换图表后重试。"
          : "In a copy of the presentation, replace the charts with tables and try again.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Existing native error")).toBeInTheDocument();
    expect(screen.queryByText("OCR_MODELS_MISSING")).toBeNull();
    expect(screen.queryByText("OCR_TIMEOUT")).toBeNull();
  },
);
