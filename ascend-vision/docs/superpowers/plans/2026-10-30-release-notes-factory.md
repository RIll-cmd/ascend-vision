# Release notes factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** "Vision, write release notes for <repo>" writes a changelog, a what's-new list and a post from commits.

**Architecture:** `assistant/release_notes.py`: `parse_log`, `group`, `fallback_story`, `parse_story`,
`render_notes`, `make_story_writer(router)`, `ReleaseNotes(*, repos, run_git, write_story, folder, announce, clock,
run)` with `intercept`. Uses `assistant.revival.command_runner` and `default_repos`.

**Tech Stack:** Python 3.12, git CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-release-notes-factory-design.md`

## Global Constraints

- Read-only on every repo; output only under Vision's data folder.
- `config.yaml` is not edited.

## Review Focus

- Commit subjects with `|`, unicode, markdown (`#`, `![`), or no conventional prefix.
- A stored last commit that no longer exists (rebased away) must fall back, not fail.
- Repo name matching: "ascend hub" with both `ascend_hub` and `ascend-hub` present.
- An AI reply that invents features or has the wrong shape.

---

### Task 1: Notes

Tests in `tests/test_release_notes.py`.

### Task 2: Config, wiring, README

`ReleaseNotesConfig(enabled=True, folder='')`; `main.py` builds it with the revival repo list; README row and
paragraph.
