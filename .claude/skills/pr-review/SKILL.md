---
name: pr-review
description: Graph-aware four-stage review of a PR, a branch, or uncommitted changes for the APDL monorepo. Use when the user asks to review a PR, review a diff, review changes before pushing, or check whether a branch is safe to merge. Collects the change set and its blast radius, judges it, verifies every finding in an independent session, and reports only what survived. Follows the shared repo workflow in docs/agent-workflows/pr-review.md.
allowed-tools: Bash(scripts/pr-review/*), Bash(graphify:*), Bash(git diff:*), Bash(git log:*), Bash(git status:*), Read, Write, Grep, Glob, mcp__github_inline_comment__create_inline_comment
---

# PR review

This Claude skill is a thin wrapper around the repository-wide workflow.

When triggered, read and follow `docs/agent-workflows/pr-review.md` from the
repository root. That file is canonical for every agent; do not add divergent
review instructions to this Claude-specific skill.

Two rules from it are worth repeating because they are what make the review
trustworthy: every finding must cite something you actually opened, and nothing
reaches the contributor until Stage 3 has verified it independently. Report the
kept/downgraded/dropped counts either way.
