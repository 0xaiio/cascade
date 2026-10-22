# cascade 推广物料

两支片子，各放一个目录；每个目录里的 `README.md` 是该片的**制作与发布记录**
（成片参数、逐镜时长、保真度披露、投稿结果），`promo-script*.md` 是旁白与字幕的文稿。

| 片子 | 主题 | 时长 | 成片 | 文稿 | 发布 |
| --- | --- | --- | --- | --- | --- |
| **v1**（2026-10-17） | 完整上手：克隆 → 启动 → 解析 → 下载 → 任务中心 → B站 投稿 | 6 分 03 秒 | [cascade-promo.mp4](v1-2026-10-17/cascade-promo.mp4) | [promo-script.md](v1-2026-10-17/promo-script.md) | [BV1JQJY6LExf](https://www.bilibili.com/video/BV1JQJY6LExf) |
| **v2**（2026-10-22） | 新特性：字幕流水线、完成提醒、视频时长、B站 上传体验大修 | 5 分 09 秒 | [cascade-promo-v2.mp4](v2-2026-10-22/cascade-promo-v2.mp4) | [promo-script-v2.md](v2-2026-10-22/promo-script-v2.md) | [BV1EKn267E2T](https://www.bilibili.com/video/BV1EKn267E2T) |

两支都归在同名合集 **0xaiio**（`season_id 9343273`）里，投稿类型均为**自制**。

## 怎么用这两份物料

- 想快速了解项目：先看 v1（完整链路的六分钟），再看 v2（这半个月的增量）。
- 想核对「片子里的东西是不是真的」：每个目录的 `README.md` 都有**保真度说明**，
  逐条写明哪几处是真实执行、哪几处只做了时序压缩或后期补音。
- 想再做一支：先把 [`docs/changelog/CHANGELOG.md`](../../docs/changelog/CHANGELOG.md)
  更新到当前 HEAD（它是素材清单），再按 v2 目录 README 末尾的「后续制作注意」走。

## 素材与脚本

成片、文稿、字幕都进版本库；**制作脚本不进**（`tmp_acceptance/promo*/`，已被 `.gitignore` 忽略）。
脚本可以随时按文稿重写：旁白文本是 `scenes.py` 里的唯一事实源，字幕与硬字幕都从它生成。
