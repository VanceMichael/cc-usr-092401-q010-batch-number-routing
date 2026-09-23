# 批次号查询路由遮蔽修复

这是水产养殖管理系统，后端维护塘口、养殖批次、投苗、投喂、水质、用药、成本和出塘销售资料，前端提供日常录入与周期分析页面。数据默认保存在 SQLite 文件中。

## 批次查询契约

### 两个互不遮蔽的入口

| 入口 | 形状 | 含义 |
| --- | --- | --- |
| `GET /api/batches/{id}/`、`GET /api/analysis/traceability/{id}/` | 路径段为整数 | 按数字主键查询，保持 `int` 校验（非整数 422） |
| `GET /api/batches/by-number?batch_number=…`、`GET /api/analysis/trace-by-number?batch_number=…` | 静态前缀 + query 参数 | 按批次号查询/追溯 |

批次号只出现在 query 中，与整数路径段在词法层面永不相交；静态前缀路由同时先于 `{batch_id}` 动态路由注册（有回归测试断言顺序）。因此纯数字批次号（如 `20260923001`）不会再被 ID 路由吞掉。

### 唯一规范化规则

所有批次号入口在服务端统一执行（唯一权威）：

1. query 百分号解码（UTF-8）；
2. Unicode **NFKC** 规范化；
3. 去除**首尾**全部空白（内部空白保留，`AB CD` ≠ `ABCD`）；
4. 转**大写**。

存储、查询、改号都只使用规范化结果；编码固定为 RFC3986（空格 `%20`、斜杠 `%2F`、中文为 UTF-8 字节序列），与浏览器 `encodeURIComponent` 逐字符一致，`+` 与 `%20` 服务端等价解析。非规范输入 `301` 到唯一规范地址并保留其他查询参数。两个不同的原始号若规范化后相同即视为同号——撞号返回 409，**绝不静默合并**。

### 结果分级

- `400 invalid_format`：缺少参数、规范化后为空、含控制字符、超过 50 个字符；
- `404 not_found`：格式合法但号不存在（含查任何别名）；
- `409 conflict`：批次号/别名被其他批次占用，或规范化后撞号；
- `301`：输入非规范或命中旧别名，地址收敛到当前规范号。

### 旧链接兼容

- `GET /api/batches/by-number/{号}/`、`GET /api/analysis/trace-by-number/{号}/?…` 仍可访问，一律 `301` 到 query 规范地址，原查询参数全部保留；号不存在也先迁移地址形状，再由规范入口返回 404。

### 改号、别名与并发

- 改号（`PUT /api/batches/{id}/`）在单个写事务内完成：新号规范化；旧号写入 `batch_number_aliases` 持久化别名；别名是"旧号 → 批次"的单跳映射（永不指向另一个别名，结构性无环），重启后仍可解析。
- 改回历史号时消费并删除对应别名；历史别名被其他批次占用（建号或改号）返回 409；删除批次时别名随外键级联清理。
- SQLite 写连接使用 `BEGIN IMMEDIATE` + `busy_timeout` + WAL，并以 `batch_number`、别名主键的唯一约束兜底：并发改号/建号严格串行化，一个号最多归属一个批次，原别名不会丢失。

### 前端规范地址

分享链接、刷新、站内跳转统一为 `/trace?batch_number=<encodeURIComponent(号)>`（见 `src/utils/batchLink.ts`）。追溯页以响应中的规范号 `replace` 收敛地址，并区分"格式非法 / 不存在 / 冲突 / 暂不可用"四类提示，提供一键复制分享链接。

## 测试命令

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

回归测试覆盖：路由注册顺序、数字 ID 与数字批次号分离、中文/斜杠/空格编码与 `%20`/`+` 等价、NFKC/空白/大小写、400/404/409 分级、旧链 301 保参、别名持久化/复用/跨批冲突/级联删除、线程级与 HTTP 级并发改号、写事务中读不阻塞，以及子进程模拟重启后的别名解析与存量数据启动治理。

## 编译与构建命令

先安装前端依赖，再检查后端并构建前端：

```bash
python3 -m compileall -q backend/app
npm --prefix frontend install --legacy-peer-deps
npm --prefix frontend run build
```

本地启动可使用 `docker compose up --build`。开发环境不得提交真实账号、连接凭据或生产数据。
