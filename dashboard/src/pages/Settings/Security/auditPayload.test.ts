import { expect, it } from "vitest";
import { formatAuditPayload } from "./auditPayload";

it("keeps file trail fields and drops secrets and private content", () => {
  expect(
    formatAuditPayload(
      JSON.stringify({
        device_id: "dev_1",
        path: "合同.docx",
        password: "hunter2",
        api_key: "sk-live",
        instruction: "把身份证号抄下来",
        text: "完整原文",
      }),
    ),
  ).toBe(JSON.stringify({ device_id: "dev_1", path: "合同.docx" }));
});
