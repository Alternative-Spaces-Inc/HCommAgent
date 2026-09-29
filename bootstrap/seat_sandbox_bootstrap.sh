#!/usr/bin/env bash
# HCA seat_sandbox_bootstrap.sh - installs .agents surface from HCA bootstrap source.
set -u

DRY_RUN=0
if [ "${SANDBOX_BOOTSTRAP_DRY:-0}" = "1" ] || [ "${1:-}" = "--check" ] || [ "${1:-}" = "--selftest" ] || [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
is_hca_repo() {
  [ -n "$1" ] && [ -f "$1/bootstrap/HCA_MANIFEST.yaml" ]
}

find_repo() {
  [ -n "${SANDBOX_REPO_DIR:-}" ] && is_hca_repo "$SANDBOX_REPO_DIR" && { echo "$SANDBOX_REPO_DIR"; return; }
  local t; t=$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null)
  is_hca_repo "$t" && { echo "$t"; return; }
  for c in /app/conversations/*/hca-repo /app/conversations/*/repo "${HOME:-/root}/hca-repo"; do
    is_hca_repo "$c" && { echo "$c"; return; }
  done
  echo "$HERE/.."
}

REPO_DIR="$(find_repo)"
if ! is_hca_repo "$REPO_DIR"; then
  echo "MISSING: HCA repository root with bootstrap/HCA_MANIFEST.yaml"
  echo "RESULT: RED - repository root not found"
  exit 1
fi

AGENTS_DIR="${SEAT_AGENTS_DIR:-/app/.agents}"
MISSING=0
SKILLS_PRESENT=0
SKILLS_TOTAL=0
RULES_INSTALLED=0

# 1. Surface BOOTSTRAP.md
CANON_BOOT="$REPO_DIR/bootstrap/agents-surface/BOOTSTRAP.md"
LIVE_BOOT="$AGENTS_DIR/BOOTSTRAP.md"
if [ -f "$CANON_BOOT" ]; then
  if [ ! -f "$LIVE_BOOT" ] || head -c 400 "$LIVE_BOOT" | grep -q "new Superagent"; then
    if [ $DRY_RUN -eq 1 ]; then
      echo "seat bootstrap: $LIVE_BOOT absent or default (dry-run: would restore)"
    else
      mkdir -p "$AGENTS_DIR"
      cp "$CANON_BOOT" "$LIVE_BOOT"
      echo "seat bootstrap: BOOTSTRAP.md installed at $LIVE_BOOT"
    fi
  elif cmp -s "$CANON_BOOT" "$LIVE_BOOT"; then
    echo "seat bootstrap: BOOTSTRAP.md present and current"
  else
    echo "seat bootstrap: BOOTSTRAP.md present, drift from canon (live kept)"
  fi
else
  echo "MISSING: surface BOOTSTRAP.md"
  MISSING=$((MISSING + 1))
fi

# 2. Surface rules
RULES_SRC="$REPO_DIR/bootstrap/agents-surface/rules"
RULES_DST="$AGENTS_DIR/rules"
if [ -d "$RULES_SRC" ]; then
  for r in "$RULES_SRC"/*.md; do
    [ -f "$r" ] || continue
    b="$(basename "$r")"
    if [ ! -f "$RULES_DST/$b" ]; then
      if [ $DRY_RUN -eq 1 ]; then
        echo "rule: $b missing (dry-run: would install)"
      else
        mkdir -p "$RULES_DST"
        cp "$r" "$RULES_DST/$b"
        echo "rule: $b installed"
      fi
      RULES_INSTALLED=$((RULES_INSTALLED + 1))
    elif cmp -s "$r" "$RULES_DST/$b"; then
      echo "rule: $b present and current"
    else
      echo "rule: $b present, drift from canon (live kept)"
    fi
  done
fi

# 3. Surface MCP config
MCP_SRC="$REPO_DIR/bootstrap/agents-surface/mcps/config.json"
MCP_DST="$AGENTS_DIR/mcps/config.json"
if [ -f "$MCP_SRC" ]; then
  if [ ! -f "$MCP_DST" ]; then
    if [ $DRY_RUN -eq 1 ]; then
      echo "mcp: config.json missing (dry-run: would install)"
    else
      mkdir -p "$AGENTS_DIR/mcps"
      cp "$MCP_SRC" "$MCP_DST"
      echo "mcp: config.json installed"
    fi
  fi
fi

# 4. Skills from manifest
MANIFEST="$REPO_DIR/bootstrap/HCA_MANIFEST.yaml"
SKILLS_DST="$AGENTS_DIR/skills"

# Parse skills from manifest
SKILL_NAMES=$(grep -E "^\s*- name:" "$MANIFEST" | awk '{print $3}')

for sname in $SKILL_NAMES; do
  SKILLS_TOTAL=$((SKILLS_TOTAL + 1))
  SSRC="$REPO_DIR/bootstrap/skills/$sname"
  SDST="$SKILLS_DST/$sname"
  if [ -f "$SSRC" ]; then
    if [ ! -f "$SDST" ]; then
      if [ $DRY_RUN -eq 1 ]; then
        echo "skill: $sname missing (dry-run: would restore)"
      else
        mkdir -p "$SKILLS_DST"
        cp "$SSRC" "$SDST"
        chmod +x "$SDST"
        echo "skill: $sname restored"
      fi
      SKILLS_PRESENT=$((SKILLS_PRESENT + 1))
    elif cmp -s "$SSRC" "$SDST"; then
      echo "skill: $sname present and current"
      SKILLS_PRESENT=$((SKILLS_PRESENT + 1))
    else
      echo "skill: $sname present, drift from canon"
      SKILLS_PRESENT=$((SKILLS_PRESENT + 1))
    fi
  else
    echo "MISSING skill source: $sname"
    MISSING=$((MISSING + 1))
  fi
done

if [ $MISSING -eq 0 ]; then
  echo "RESULT: GREEN - skills $SKILLS_PRESENT/$SKILLS_TOTAL present, surface surface files verified"
  exit 0
else
  echo "RESULT: RED - $MISSING items missing or failed"
  exit 1
fi
