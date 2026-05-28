#!/usr/bin/env bash
# 提取 markdown 中所有 ```plantuml``` 块到独立 .puml 文件，并逐个编译。
#
# 用法: bash extract_and_compile.sh <markdown_file> [out_dir]
# 环境: PUML_JAR 可覆盖默认 jar 路径

set -u

MD="${1:?markdown file path required}"
OUT="${2:-/tmp/puml_check}"
PUML_JAR="${PUML_JAR:-/home/lyty/work/envcfg/plantuml-1.2026.1.jar}"

if [ ! -f "$MD" ]; then
  echo "ERROR: markdown not found: $MD" >&2
  exit 2
fi
if [ ! -f "$PUML_JAR" ]; then
  echo "ERROR: plantuml.jar not found: $PUML_JAR" >&2
  exit 2
fi

mkdir -p "$OUT"
rm -f "$OUT"/g*.puml "$OUT"/g*.png "$OUT"/g*.err

awk -v outdir="$OUT" '
/^```plantuml$/ { in_block=1; cnt++; fname=sprintf("%s/g%02d.puml", outdir, cnt); next }
/^```$/ && in_block { in_block=0; next }
in_block { print > fname }
' "$MD"

count=$(ls "$OUT"/g*.puml 2>/dev/null | wc -l)
if [ "$count" = "0" ]; then
  echo "No plantuml blocks found in $MD"
  exit 0
fi

echo "Found $count plantuml blocks, compiling..."
echo

fail=0
for f in "$OUT"/g*.puml; do
  base=$(basename "$f" .puml)
  java -jar "$PUML_JAR" -failfast2 -pipe < "$f" > "$OUT/$base.png" 2> "$OUT/$base.err"
  rc=$?
  err=$(cat "$OUT/$base.err")
  if [ $rc -ne 0 ] || [ -n "$err" ]; then
    echo "FAIL $base  (rc=$rc)"
    echo "     $err" | head -3
    fail=$((fail + 1))
  else
    sz=$(file "$OUT/$base.png" 2>/dev/null | grep -oE '[0-9]+ x [0-9]+' || echo "?x?")
    echo "OK   $base  ($sz)"
  fi
done

echo
echo "---"
echo "Total: $count, failures: $fail"
echo "Outputs in: $OUT"

exit $fail
