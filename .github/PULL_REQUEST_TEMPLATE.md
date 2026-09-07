<!-- Title: type(scope): summary -->

## What / 改动内容

<!-- What does this PR do? -->

## Why / 动机

<!-- Link the issue: Fixes #N -->

## How tested / 测试说明

- [ ] `uv run pytest` green (N tests)
- [ ] `uv run ruff check .` clean
- [ ] `cd web && npm run build` green (if frontend touched)
- [ ] New logic covered by tests (TDD)

## Checklist / 检查清单

- [ ] No real medical device connections / 未接入真实医疗设备
- [ ] Work-order FSM not forked (use medops_common.constants) / 状态机未复制分叉
- [ ] Docs/README updated if needed
