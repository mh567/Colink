// @vitest-environment node
import { readFile } from "node:fs/promises";
import { build } from "esbuild";
import { describe, expect, it } from "vitest";
import config from "../vite.config";

describe("Markdown compatibility", () => {
  it("renders Markdown and autolinks without regular expression lookbehind", async () => {
    const options = await config({
      command: "build",
      mode: "production",
      isSsrBuild: false,
      isPreview: false,
    });
    const plugins = (options.plugins ?? []).flat(Infinity);
    const compatibility = plugins.find(
      (plugin) =>
        plugin &&
        typeof plugin === "object" &&
        plugin.name === "markdown-compatibility",
    );

    const result = await build({
      stdin: {
        contents: `
          import React from "react";
          import { renderToStaticMarkup } from "react-dom/server";
          import ReactMarkdown from "react-markdown";
          import remarkGfm from "remark-gfm";
          export default (text) => renderToStaticMarkup(React.createElement(ReactMarkdown, {remarkPlugins: [remarkGfm]}, text));
        `,
        resolveDir: process.cwd(),
      },
      bundle: true,
      platform: "node",
      format: "cjs",
      target: "safari14",
      write: false,
      plugins: [
        {
          name: "apply-dashboard-transform",
          setup(builder) {
            builder.onLoad(
              {
                filter:
                  /mdast-util-gfm-autolink-literal[/\\]lib[/\\]index\.js$/,
              },
              async ({ path }) => {
                const source = await readFile(path, "utf8");
                if (
                  !compatibility ||
                  typeof compatibility.transform !== "function"
                ) {
                  return { contents: source, loader: "js" };
                }
                const transformed = await compatibility.transform.call(
                  {} as never,
                  source,
                  path,
                  {} as never,
                );
                return {
                  contents:
                    typeof transformed === "string"
                      ? transformed
                      : transformed?.code ?? source,
                  loader: "js",
                };
              },
            );
          },
        },
      ],
    });

    const NativeRegExp = RegExp;
    function LegacyRegExp(pattern: string, flags?: string) {
      if (typeof pattern === "string" && pattern.includes("(?<=")) {
        throw new SyntaxError("lookbehind is unsupported");
      }
      return new NativeRegExp(pattern, flags);
    }
    Object.setPrototypeOf(LegacyRegExp, NativeRegExp);
    LegacyRegExp.prototype = NativeRegExp.prototype;
    const module = { exports: {} as { default: (text: string) => string } };
    const execute = new Function(
      "module",
      "exports",
      "require",
      "RegExp",
      result.outputFiles[0].text,
    );
    execute(module, module.exports, require, LegacyRegExp);

    expect(
      module.exports.default(
        "Answer **15 days**. Visit https://example.com or email a@example.com.",
      ),
    ).toContain("<strong>15 days</strong>");
    const html = module.exports.default(
      "Visit https://example.com or email a@example.com.",
    );
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain('href="mailto:a@example.com"');
  });
});
