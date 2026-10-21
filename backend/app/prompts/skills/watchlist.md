---
name: watchlist
title: 我的自选
summary: 用户问自己的自选股、关注列表、持仓里的标的怎么样时用
tools: watchlist
---
1. 用 watchlist 读当前用户自选里的标的和最近收盘价（只读）。
2. 用户想深入看其中几只时，再 instrument_search 拿到 instrument_id，查 price_bars 和 financial_metrics。
3. 自选很多时，先概括整体（哪些是同一个行业、哪些数据偏旧），再挑用户最关心的几只展开，不要逐只复读。
