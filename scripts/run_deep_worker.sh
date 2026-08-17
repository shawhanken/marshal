#!/usr/bin/env bash
# Launch a DEEP-capable Marshal worker.
#
# Deep reviews run the real `claude -p` headlessly to perform a full /marshal review
# in a throwaway git worktree of the target repo, then write a verdict file the worker
# reads back. Headless claude needs tool access to do that. Instead of
# --dangerously-skip-permissions (all-or-nothing bypass), we grant a SCOPED
# --allowedTools list so the review can run its tools and write the verdict — nothing
# more. Override MARSHAL_CLAUDE_ALLOWED_TOOLS to widen/narrow it.
#
# SECURITY: a deep review runs claude with broad tool access (incl. Bash) inside a
# checkout of the target repo. The "local-only, do not post" instruction is a prompt,
# not a sandbox — treat deep-worker verdicts as advisory. See CLAUDE.md / the dashboard
# memo on this trust boundary.
#
# Usage:  bash scripts/run_deep_worker.sh
set -euo pipefail
cd "$(dirname "$0")/.."

export MARSHAL_DB="${MARSHAL_DB:-sqlite:////home/ubuntu/workspace/marshal/marshal.db}"
export MARSHAL_CLAUDE_ALLOWED_TOOLS="${MARSHAL_CLAUDE_ALLOWED_TOOLS:-Bash Read Write Edit Grep Glob Task TodoWrite WebFetch WebSearch Skill}"
export MARSHAL_DEEP_TIMEOUT_S="${MARSHAL_DEEP_TIMEOUT_S:-3600}"
# CRITICAL for speed: deep reviews run the repo's cargo/proptest invariants in a FRESH
# throwaway worktree that has no build cache. Without a shared target dir, the first
# `cargo test` cold-compiles the whole repo (node: 20-40min) and busts the timeout with
# no verdict. Point cargo at a persistent, SHARED target so runs are incremental. Default
# is an isolated dir (does NOT touch your main checkout's target) that warms over reviews;
# the very first review of a large repo still pays one cold compile. To trade isolation for
# instant warmth, set CARGO_TARGET_DIR=/home/ubuntu/workspace/<repo>/target (reuses your
# 21GB cache but rebuilds the review-commit delta into it).
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.marshal/cargo-target}"
mkdir -p "$CARGO_TARGET_DIR"
: "${GITHUB_TOKEN:=$(gh auth token 2>/dev/null || true)}"; export GITHUB_TOKEN

echo "Starting deep-capable Marshal worker"
echo "  DB:            $MARSHAL_DB"
echo "  tools:         $MARSHAL_CLAUDE_ALLOWED_TOOLS"
echo "  timeout:       ${MARSHAL_DEEP_TIMEOUT_S}s per deep job"
echo "  cargo target:  $CARGO_TARGET_DIR (shared, so invariant builds are incremental)"
exec venv/bin/python -m marshal_core.worker
