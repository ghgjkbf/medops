# PostgreSQL 本地开发环境（P1 起）

- 版本：PostgreSQL 16（便携版 binaries zip，解压到任意路径，下文以 `<PG_BIN>` 指其 `bin` 目录）
- 数据目录：`deploy/pgdata`（gitignore）
- 端口：**55432**（避开 5432 常规占用）
- 用户/库：`medops` / `medops`（trust 认证，仅本机开发）

## 初始化（一次性）

```bash
"<PG_BIN>/initdb.exe" -D deploy/pgdata -U medops -E UTF8
```

## 启动 / 停止

```bash
# 启动（后台，日志 deploy/pgdata/logfile.log）
"<PG_BIN>/pg_ctl.exe" -D deploy/pgdata -l deploy/pgdata/logfile.log -o "-p 55432" start

# 停止
"<PG_BIN>/pg_ctl.exe" -D deploy/pgdata stop

# 状态
"<PG_BIN>/pg_ctl.exe" -D deploy/pgdata status
```

Windows 下也可直接双击 `scripts/start.bat`（编辑脚本顶部的 `PGBIN` 变量指向你的
`<PG_BIN>`，或设置 `MEDOPS_PGBIN` 环境变量）：自动拉起 PostgreSQL → 迁移 → 后端。

## 连接

```bash
"<PG_BIN>/psql.exe" -p 55432 -U medops -h 127.0.0.1 -d medops
```

连接串（core 侧默认值，`DATABASE_URL` 环境变量可覆盖）：
`postgresql+asyncpg://medops:medops@127.0.0.1:55432/medops`
