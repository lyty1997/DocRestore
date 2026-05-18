export type CodeTokenKind =
  | "plain"
  | "comment"
  | "keyword"
  | "literal"
  | "number"
  | "operator"
  | "punctuation"
  | "string"
  | "type";

export interface CodeToken {
  readonly kind: CodeTokenKind;
  readonly text: string;
}

const C_LIKE_KEYWORDS = new Set([
  "alignas",
  "alignof",
  "auto",
  "break",
  "case",
  "catch",
  "class",
  "const",
  "consteval",
  "constexpr",
  "constinit",
  "continue",
  "decltype",
  "default",
  "delete",
  "do",
  "else",
  "enum",
  "explicit",
  "export",
  "extern",
  "for",
  "friend",
  "goto",
  "if",
  "inline",
  "namespace",
  "new",
  "noexcept",
  "operator",
  "private",
  "protected",
  "public",
  "requires",
  "return",
  "sizeof",
  "static",
  "struct",
  "switch",
  "template",
  "this",
  "throw",
  "try",
  "typedef",
  "typename",
  "using",
  "virtual",
  "while",
]);

const PYTHON_KEYWORDS = new Set([
  "and",
  "as",
  "assert",
  "async",
  "await",
  "break",
  "class",
  "continue",
  "def",
  "del",
  "elif",
  "else",
  "except",
  "finally",
  "for",
  "from",
  "global",
  "if",
  "import",
  "in",
  "is",
  "lambda",
  "nonlocal",
  "not",
  "or",
  "pass",
  "raise",
  "return",
  "try",
  "while",
  "with",
  "yield",
]);

const JS_KEYWORDS = new Set([
  "async",
  "await",
  "break",
  "case",
  "catch",
  "class",
  "const",
  "continue",
  "default",
  "do",
  "else",
  "export",
  "extends",
  "finally",
  "for",
  "from",
  "function",
  "if",
  "import",
  "interface",
  "let",
  "new",
  "private",
  "protected",
  "public",
  "readonly",
  "return",
  "static",
  "switch",
  "throw",
  "try",
  "type",
  "typeof",
  "var",
  "while",
]);

const SHELL_KEYWORDS = new Set([
  "case",
  "do",
  "done",
  "elif",
  "else",
  "esac",
  "fi",
  "for",
  "function",
  "if",
  "in",
  "then",
  "while",
]);

const LITERALS = new Set([
  "False",
  "None",
  "True",
  "false",
  "nullptr",
  "null",
  "true",
  "undefined",
]);

const TYPES = new Set([
  "bool",
  "boolean",
  "char",
  "double",
  "float",
  "int",
  "int16_t",
  "int32_t",
  "int64_t",
  "int8_t",
  "long",
  "number",
  "short",
  "size_t",
  "string",
  "uint16_t",
  "uint32_t",
  "uint64_t",
  "uint8_t",
  "void",
]);

const NUMBER_RE = /^(?:0x[\da-fA-F]+|\d+(?:\.\d+)?)(?:[eE][+-]?\d+)?/;
const IDENT_RE = /^[A-Za-z_$][\w$]*/;
const OPERATOR_RE = /^(?:=>|==|!=|<=|>=|\+\+|--|&&|\|\||::|->|[+\-*/%=!<>|&^~?:]+)/;

function languageKey(language: string | null | undefined, path: string): string {
  const raw = (language ?? "").toLowerCase();
  const lowerPath = path.toLowerCase();
  const dot = lowerPath.lastIndexOf(".");
  const ext = dot === -1 ? "" : lowerPath.slice(dot + 1);
  const hint = raw === "" || raw === "?" ? ext : raw;

  if (["cc", "cpp", "cxx", "c++", "hpp", "hh", "h", "c"].includes(hint)) {
    return "cpp";
  }
  if (["py", "python"].includes(hint)) return "python";
  if (["js", "jsx", "javascript", "ts", "tsx", "typescript"].includes(hint)) {
    return "javascript";
  }
  if (["sh", "bash", "zsh", "shell"].includes(hint)) return "shell";
  if (["json"].includes(hint)) return "json";
  if (["md", "markdown"].includes(hint)) return "markdown";
  return "plain";
}

function keywordSetFor(key: string): ReadonlySet<string> {
  if (key === "cpp") return C_LIKE_KEYWORDS;
  if (key === "python") return PYTHON_KEYWORDS;
  if (key === "javascript") return JS_KEYWORDS;
  if (key === "shell") return SHELL_KEYWORDS;
  return new Set<string>();
}

function lineCommentStart(key: string, line: string, pos: number): string | undefined {
  if (["cpp", "javascript"].includes(key) && line.startsWith("//", pos)) {
    return "//";
  }
  if (["python", "shell"].includes(key) && line.startsWith("#", pos)) {
    return "#";
  }
  return undefined;
}

function readQuotedString(line: string, pos: number): number {
  const quote = line[pos];
  if (quote === undefined) return pos + 1;
  let idx = pos + 1;
  while (idx < line.length) {
    const ch = line[idx];
    if (ch === "\\") {
      idx += 2;
      continue;
    }
    idx += 1;
    if (ch === quote) break;
  }
  return idx;
}

function pushToken(tokens: CodeToken[], kind: CodeTokenKind, text: string): void {
  if (text !== "") tokens.push({ kind, text });
}

export function tokenizeCodeLine(
  line: string,
  language: string | null | undefined,
  path: string,
): CodeToken[] {
  const key = languageKey(language, path);
  if (key === "plain") return [{ kind: "plain", text: line }];

  const keywords = keywordSetFor(key);
  const tokens: CodeToken[] = [];
  let pos = 0;

  while (pos < line.length) {
    const ch = line[pos] ?? "";
    const commentStart = lineCommentStart(key, line, pos);
    if (commentStart !== undefined) {
      pushToken(tokens, "comment", line.slice(pos));
      break;
    }

    if (ch === "\"" || ch === "'" || ch === "`") {
      const end = readQuotedString(line, pos);
      pushToken(tokens, "string", line.slice(pos, end));
      pos = end;
      continue;
    }

    if (/\s/.test(ch)) {
      let end = pos + 1;
      while (end < line.length && /\s/.test(line[end] ?? "")) end += 1;
      pushToken(tokens, "plain", line.slice(pos, end));
      pos = end;
      continue;
    }

    const numberMatch = NUMBER_RE.exec(line.slice(pos));
    if (numberMatch?.[0] !== undefined) {
      pushToken(tokens, "number", numberMatch[0]);
      pos += numberMatch[0].length;
      continue;
    }

    const identMatch = IDENT_RE.exec(line.slice(pos));
    if (identMatch?.[0] !== undefined) {
      const ident = identMatch[0];
      let kind: CodeTokenKind = "plain";
      if (keywords.has(ident)) {
        kind = "keyword";
      } else if (TYPES.has(ident)) {
        kind = "type";
      } else if (LITERALS.has(ident)) {
        kind = "literal";
      }
      pushToken(tokens, kind, ident);
      pos += ident.length;
      continue;
    }

    const operatorMatch = OPERATOR_RE.exec(line.slice(pos));
    if (operatorMatch?.[0] !== undefined) {
      pushToken(tokens, "operator", operatorMatch[0]);
      pos += operatorMatch[0].length;
      continue;
    }

    pushToken(tokens, "punctuation", ch);
    pos += 1;
  }

  return tokens.length > 0 ? tokens : [{ kind: "plain", text: "" }];
}
