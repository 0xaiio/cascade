# 036 · 两个进程可以同时跑在同一份 `data/` 上，同时恢复、同时写同一个 `.part`

## 问题

启动时会自动恢复孤儿任务（[015](015-orphan-items-never-resume.md)）。默认端口只允许一个实例，
但 `--auto-port` 在端口被占时会**换一个端口继续起** —— 于是两个进程连同一个 SQLite、
同时把同一批 `queued`/`running` 条目重新排队、同时往同一个 `.part` 写。

没有任何报错、没有任何日志，坏的是产物。这是本轮唯一会**损坏数据**的条目
（[ai/plan/brainstorming.md](../ai/plan/brainstorming.md) D-05）。

## 根因

启动路径上只有端口预检，没有任何「这份数据目录已经被占用」的概念：

```
main() → _resolve_port() → uvicorn.run()
         ↑ 端口被占就换一个（--auto-port）
```

端口是**网络资源**，库与 `.part` 是**磁盘资源**；换端口对后者没有任何保护。

## 修法

新增 `backend/app/host/instance_lock.py`：对 `data/app.instance.lock` 取**操作系统的非阻塞文件锁**
（Windows `msvcrt.locking` / POSIX `flock`）。`main()` 在**解析端口之前**取锁，拿不到就打印
「已有一个实例在跑（锁文件 …，持有者：pid=… port=…）」并退出 2。

四条设计约束：

1. **用文件锁，不用「存在即占用」的标记文件。** 强杀进程不会删掉标记文件，
   那会让下一次启动被一个已经死掉的进程挡在门外；操作系统锁在进程退出时自动释放。
   已实测：`taskkill /F` 之后重新启动正常。
2. **锁跟着 `data/` 走**，不是全局单例。用户开两份、各用各的 `YTDL_DATA_DIR` 是合法用法。
3. **锁文件不删**（避免「别人刚好打开同一个文件」的 inode 竞态）；内容只是给下一个人看的。
4. **`--auto-port` 不能绕过**：锁在端口解析之前取 —— 换端口换不掉同一份库。

## 两个踩到的坑（都是实测出来的）

**① Windows 的字节锁连「读」都禁止。** 最初把备注（pid/端口）写在被锁的第 0 字节上，
于是**第二个进程读不到持有者是谁** —— 报错只剩「锁文件路径」，最有用的一半没了。
改为：锁第 0 字节，备注从**第 1 字节**起写，读取时 `lseek(1)`。

**② 对空文件锁 1 字节 = 没锁。** 单元测试里两个句柄能正确互斥，但**真实双进程**跑出来是：
第一个 `python -m app` 起来之后，第二个还能绑到另一个端口继续跑。
`_locking` 在长度为 0 的文件上锁的是一个长度为 0 的区间。修法：取锁前先写出第 0 个字节
（同时作为「文件非空」的标记）。补了一条 `test_the_lock_file_is_never_empty` 钉住它 ——
这条用例的价值在于：**单元测试的进程内互斥并不能证明跨进程互斥**。

## 测试

| 用例 | 钉住什么 |
| --- | --- |
| `test_a_second_instance_on_the_same_data_dir_is_refused` | 第二个实例被拒，异常里带**持有者的 pid/端口** |
| `test_releasing_lets_the_next_instance_in` | 释放后可再取（不是一次性的） |
| `test_the_lock_file_is_never_empty` | 取锁前必须先写出第 0 字节（空文件锁 = 没锁） |
| `test_a_leftover_lock_file_does_not_block` | **残留文件不阻挡** —— 区分文件锁与标记文件 |
| `test_two_data_dirs_can_run_at_the_same_time` | 两个 `data/` 各取各的（合法用法） |
| `test_main_refuses_to_start_when_another_instance_holds_the_lock` | 入口层退出 2，且**绝不启动 uvicorn** |
| `test_main_writes_the_port_and_releases_on_exit` | 运行期锁持有、文件里写着端口、退出后释放 |

真实双进程（手动验收，见下）：

```
A：python -m app --port 8022                → /health 200
B：同 data 目录、--port 8032 --auto-port     → EXIT=2
   已有一个实例在跑（锁文件 …\app.instance.lock，持有者：pid=48988 port=8022）
A：taskkill /F
C：python -m app --port 8022                → 启动成功（锁随进程退出释放）
```

## 边界

- **只有 `python -m app` 这条路加锁**。直接 `uvicorn app.main:app` 不经过 `main()`，不受保护 ——
  文档里以 `python -m app` 为准（`init/` 脚本用的也是它）。
- 锁保护的是「同一份 `data/`」，不保护「两个进程写同一批**下载目录**但不同 `data/`」——
  那是用户显式选择的两套库，工具无法判断它们是否指向同一批文件。
- 网络盘（SMB）上的文件锁语义依服务端实现而定，未验证。
