# Git Commit Policy (MANDATORY)

**Commit message format:**
```
<type>: <short description>

[optional body explaining why/what changed]
```

**RULES:**
- NO "Generated with Claude Code" footer
- NO "Co-Authored-By: Claude" line
- NO mention of "Claude" or "Happy" anywhere
- Keep messages short (1-5 lines preferred)
- Types: feat, fix, refactor, chore, docs, build, test

# GitHub Issue Policy (MANDATORY)

**Rules for opening issues:**

1. NEVER open issue without my explicit agreement
2. ONLY open issues to rophy/mariadb-operator, NEVER to any other project
3. ALWAYS ask for review before opening the issue

**Using gh CLI:**
- ALWAYS use `--repo rophy/mariadb-operator` flag
- Full command: `gh issue create --repo rophy/mariadb-operator --title "..." --label "..." --body "..."`
- Use heredoc for multi-line body: `--body "$(cat <<'EOF' ... EOF)"`
- Common labels: bug, enhancement, documentation, question
