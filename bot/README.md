# MortalSim-Bot

QQ 群 @ 机器人 → 输入局况 → 返回 PNG 核心指标图。

## 启动顺序

1. 启动 MortalSim（固定端口 50715）：双击 `start_mortalsim.cmd`
2. 启动 NapCat/OneBot 网关（端口 5700/5701，见 `docs/QQ_BOT_DEPLOY.md`）
3. 启动机器人：双击 `start_bot.cmd`

## 配置

见 `config.toml`：
- 机器人 QQ、群白名单、管理员
- 每日限额（默认 5 次 / 2000 局 / 人）
- MortalSim 模型 ID、API 地址

## 消息格式

```
@机器人 手牌 4567m3477p134066s 宝牌 9s 候选 1s,6s 局 E1 局数 1000
```

吃碰响应输入未摸牌的 13 张手牌；`x` 是自家下一巡。`pon[东]:5p`（也支持
`[下家]`、`[对家]`、`[上家]`）指定当前风位的供牌家，`@05p` 绑定手中赤五与
普通五的消耗，`>8p` 指定碰后切牌。不写后切则由模型决定。

显式响应牌河须给出四家连续的真实弃牌前缀，截止于供牌家最新弃牌；不会自动补长，
供牌家与牌河来源不一致会拒绝。未提供牌河时生成确定性合成前缀，在对应供牌家的
真实跨巡时点停止。生成器按巡目类别权重选择可用类别，再在类别内均匀选择牌种；
空类别会重新归一化，摸切率独立采样。手牌、指示牌和目标弃牌先扣除实体预算，
普通五最多三枚、赤五最多一枚。合成牌河是近似输入，不是完整真实牌谱分布。

## 目录

```
src/
  bot.py          主程序（OneBot 11）
  parser.py       消息解析
  quota.py        每日限额 SQLite
  mortal_client.py MortalSim API 客户端
  render_png.py   PNG 渲染（Pillow）
config.toml
start_mortalsim.cmd
start_bot.cmd
```
