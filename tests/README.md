# 测试说明

生产代码（`bili-comment/`）面向 Python 2.7，本机仅有 Python 3.12，因此测试不修改
生产源码，而是在加载时做最小的 py2→py3 语法转换（仅 `print` 语句与 `except E, e` 语法），
并用假模块替换 `requests`、登录、动态、提交评论等外部依赖，全程不访问外网。

运行方式（Python 3.8+）：

```
python -I acceptance_test.py   # 17 个用例：get_floor 单测 + usage00/usage01 验收
python -I fake_api_test.py     # 本地 http.server 假接口端到端验证 get_floor
```

## acceptance_test.py 覆盖的验收情形

get_floor 层：
- 请求异常（超时/连接错误）→ 返回 `GET_FLOOR_FAILED`，不抛异常
- HTTP 状态码非 200 → 返回失败标记
- 响应缺少 `page`、楼层字段残缺、楼层非数字 → 返回失败标记
- 正常响应 → 返回楼层字符串；无评论时仍返回 `'0'`（保持旧行为）

usage00（单线程）/ usage01（多线程）各覆盖：
- 偶发失败后恢复，最终正常抢到（成功后连续失败计数清零）
- 持续失败达到 `max_get_failures` 上限 → 打印 `[failed] ...` 结局，
  不会误报 `I am sorry.` 或 `Done.Good luck.`，且不进入提交评论阶段
- 无失败正常抢到楼层
- 缺省（`max_get_failures=0`）时失败不终止、一直轮询（旧行为）
- 多线程专属：失败期间工作线程不释放锁、不提前退出；结束后所有锁释放、主线程不死等
