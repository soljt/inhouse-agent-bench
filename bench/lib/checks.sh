#!/usr/bin/env bash
# Shared grading helpers. Source it from a task's grade.sh:
#   . "$(dirname "$(readlink -f "$0")")/../../lib/checks.sh"
# Results go into the associative array R; emit_json writes them as <out>/auto.json.
# Every helper takes the workspace from $WS and the output folder from $OUT.

export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
CHECKS_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
declare -gA R

json_str() { printf '"%s"' "${1//\"/\\\"}"; }

# Agent's changes against the base tag, including new files: diff.patch, changed-files.txt
record_diff() {
  local base="$1"
  ( cd "$WS" && git add -A -N . 2>/dev/null
    git diff "$base" > "$OUT/diff.patch"
    git diff --name-only "$base" > "$OUT/changed-files.txt"
    git reset -q )
  R[changed_files]=$(wc -l < "$OUT/changed-files.txt")
}

# Trailing whitespace and the like; legacy space-before-tab indentation is ignored
diff_check() {
  local base="$1"
  ( cd "$WS" && git add -A -N . 2>/dev/null
    git -c core.whitespace=-space-before-tab,cr-at-eol diff --check "$base" > "$OUT/diff-check.txt" 2>&1; rc=$?
    git reset -q; exit $rc ) && R[diff_check_clean]=true || R[diff_check_clean]=false
}

# AGENTS.md: no dashes as punctuation in comments. Counts added comment lines with " - ", en or em dash.
# Copyright lines are exempt: their "2016 - 2026" range is the format AGENTS.md prescribes.
comment_dashes() {
  grep -E '^\+' "$OUT/diff.patch" | grep -vE '^\+\+\+' \
    | grep -E '(//|<!--|/\*|^\+\s*\*|<#--).*( - |—|–)' \
    | grep -vE '(\(c\)|©|@author)' > "$OUT/comment-dashes.txt" || true
  R[comment_dash_lines]=$(wc -l < "$OUT/comment-dashes.txt")
}

# Files outside an allowed regex: out-of-scope.txt and a count
scope_check() {
  grep -vE "$1" "$OUT/changed-files.txt" > "$OUT/out-of-scope.txt" || true
  R[out_of_scope_files]=$(wc -l < "$OUT/out-of-scope.txt")
}

# AGENTS.md: touched files get their copyright end year bumped. key, file, year
copyright_year() {
  local key="$1" file="$2" year="${3:-$(date +%Y)}"
  grep -qE "(\(c\)|©) *[0-9]{4} *- *$year" "$WS/$file" && R[$key]=true || R[$key]=false
}

# A CRLF legacy file must stay CRLF on every line. key, file
crlf_kept() {
  local key="$1" file="$2" lines crlf
  lines=$(wc -l < "$WS/$file"); crlf=$(grep -c $'\r$' "$WS/$file")
  [ "$lines" -gt 0 ] && [ "$lines" = "$crlf" ] && R[$key]=true || R[$key]=false
}

# Clean compile of the pc slice; ant's timestamp check can skip a source edited right after a build. key
pc_compile() {
  (cd "$WS/pc" && ant clean compile -DcleanServerSrcCopies=true) > "$OUT/compile.log" 2>&1 && R[$1]=true || R[$1]=false
}

# Run hidden JUnit 5 test classes against pc/classes. key, test .java files..., -- FQCNs...
hidden_junit() {
  local key="$1"; shift
  local files=() classes=() t; while [ "$1" != "--" ]; do files+=("$1"); shift; done; shift; classes=("$@")
  t=$(mktemp -d)
  local cp="$WS/pc/classes:$(ls "$WS"/pc/commons-lib/*.jar | tr '\n' ':')"
  if $JAVA_HOME/bin/javac -cp "$cp" -d "$t" "$CHECKS_LIB/RunTests.java" "${files[@]}" > "$OUT/$key.log" 2>&1 \
     && $JAVA_HOME/bin/java ${JUNIT_PROPS:-} -cp "$t:$cp" RunTests "${classes[@]}" >> "$OUT/$key.log" 2>&1; then
    R[$key]=true; else R[$key]=false; fi
  R[${key}_summary]=$(json_str "$(grep -o 'TESTS.*' "$OUT/$key.log" | tail -1)")
  rm -rf "$t"
}

emit_json() {
  { echo "{"; local first=1 k
    for k in $(printf '%s\n' "${!R[@]}" | sort); do
      [ $first = 1 ] || echo ","; first=0; printf '  "%s": %s' "$k" "${R[$k]}"
    done; echo; echo "}"; } > "$OUT/auto.json"
  cat "$OUT/auto.json"
}
