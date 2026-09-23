# 批次号查询路由遮蔽修复

这是水产养殖管理系统，后端维护塘口、养殖批次、投苗、投喂、水质、用药、成本和出塘销售资料，前端提供日常录入与周期分析页面。数据默认保存在 SQLite 文件中。

## 批次查询契约

**入口互不遮蔽**

- 数字 ID 入口：`GET /api/batches/{id:int}/`，路径段使用 Starlette `int` 转换器，非纯数字（如 `by-number`）不可能命中。
- 批次号入口（规范）：`GET /api/batches/by-number?n=<批次号>`，追溯为 `GET /api/analysis/trace-by-number?n=<批次号>`。使用查询参数，斜杠、空格、中文均可安全携带；静态路由在路由表中先于动态 ID 路由声明（双保险）。
- 前端唯一规范地址：`/trace?n=<批次号>`，分享链接、直接刷新、站内跳转一致。

**唯一规范化规则**（前后端各有一份等价实现：`backend/app/services/batch_numbers.py` 与 `frontend/src/utils/batchNumber.ts`）

1. 百分号编码按 UTF-8 解码；生成链接时一律严格编码（`/`→`%2F`、空格→`%20`），浏览器与服务端结果一致。
2. Unicode 规范化为 NFC。
3. 去除首尾空白（含全角空格）。
4. 大小写敏感：`ABC` 与 `abc` 是两个不同批次号，绝不合并；仅大小写不同地命中已有号时返回 409。
5. 非规范输入（首尾空白、NFD 等）301 到规范地址。

**结果三态分离**：找不到 404 / 格式非法 422 / 存在规范化冲突 409。

**旧链接兼容**：`/api/batches/by-number/<旧批次号>/?...` 与 `/api/analysis/trace-by-number/<旧批次号>?...` 均 301 到查询形态并原样保留全部查询参数；前端 `/trace/<旧批次号>` 同样归一到 `/trace?n=`。

**改名与持久化别名**：批次号被更正后，旧号在 `batch_names` 表中持久化为 `alias`，旧链接继续定位并 301 到当前号。`current` 与 `alias` 共用同一全局唯一命名空间，名称永远只指向一个批次，结构上杜绝一号多批与别名循环。改名是事务操作，冲突时原数据与原别名不变；进程锁加数据库唯一索引共同防护并发改号。服务启动时自动回填旧库的命名空间表，重启后旧号仍可解析。

## 测试命令

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

前端契约（规范化与编码，含与 Python `urllib.parse.quote(safe='')` 对齐的编码向量）：

```bash
node --test tests/frontend/batchNumber.test.mjs
```

后端测试需要 `backend/requirements-dev.txt` 中的 httpx（TestClient 兼容版本）。

## 编译与构建命令

先安装前端依赖，再检查后端并构建前端：

```bash
python3 -m compileall -q backend/app
npm --prefix frontend install --legacy-peer-deps
npm --prefix frontend run build
```

本地启动可使用 `docker compose up --build`。开发环境不得提交真实账号、连接凭据或生产数据。
