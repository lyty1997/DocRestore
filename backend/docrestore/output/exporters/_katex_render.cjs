// KaTeX 服务端预渲染（D3 PDF 公式）。
//
// 用法：node _katex_render.cjs <katex 包绝对路径>
//   stdin  : JSON 数组 [{ "tex": "...", "display": true|false }, ...]
//   stdout : JSON 数组 [html|null, ...]（与输入等长；单条渲染失败为 null）
//
// 用 output:'html' 只产出 KaTeX 的 CSS 排版 HTML（不含 katex-mathml），
// 避免 weasyprint 把隐藏 MathML 也渲染出来导致公式重影。
// 详见 docs/zh/export-mode.md §7。

"use strict";

const katex = require(process.argv[2]);

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  let items;
  try {
    items = JSON.parse(input);
  } catch (err) {
    process.stderr.write("invalid json input: " + err.message);
    process.exit(1);
    return;
  }
  const out = items.map((it) => {
    try {
      return katex.renderToString(String(it.tex), {
        displayMode: Boolean(it.display),
        throwOnError: false,
        output: "html",
      });
    } catch (err) {
      return null;
    }
  });
  process.stdout.write(JSON.stringify(out));
});
