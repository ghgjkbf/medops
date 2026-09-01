# PostgreSQL 本地开发环境（P1 起）

- 版本：PostgreSQL 16.10（便携版，源包 `D:\ai-use\tools\postgresql-16.10-1-windows-x64-binaries.zip`，解压于 `D:\ai-use\tools\pg16\`）
- 数据目录：`deploy/pgdata`（gitignore）
- 端口：**55432**（避开 5432 常规占用）
- 用户/库：`medops` / `medops`（trust 认证，仅本机开发）

## 启动 / 停止

```bash
# 启动（后台，日志 deploy/pgdata/logfile.log）
"D:/ai-use/tools/pg16/pgsql/bin/pg_ctl.exe" -D "C:/Users/Administrator/Desktop/project/deploy/pgdata" -l "C:/Users/Administrator/Desktop/project/deploy/pgdata/logfile.log" -o "-p 55432" start

# 停止
"D:/ai-use/tools/pg16/pgsql/bin/pg_ctl.exe" -D "C:/Users/Administrator/Desktop/project/deploy/pgdata" stop

# 状态
"D:/ai-use/tools/pg16/pgsql/bin/pg_ctl.exe" -D "C:/Users/Administrator/Desktop/project/deploy/pgdata" status
```

## 连接

```bash
"D:/ai-use/tools/pg16/pgsql/bin/psql.exe" -p 55432 -U medops -h 127.0.0.1 -d medops
```

连接串（core 侧默认值，`DATABASE_URL` 环境变量可覆盖）：
`postgresql+asyncpg://medops:medops@127.0.0.1:55432/medops`
