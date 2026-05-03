---
created: 2026-04-30
tags:
  - hft
  - cpp
  - quant
  - engineering-blog
---

# HFT 与量化工程博客

这些不是“社群”，但质量通常比社群高。适合用来建立工程品味和技术方向感。

## 必看

### Jane Street Tech Blog

链接：https://blog.janestreet.com/

价值：

- 系统工程、性能、构建系统、调试、类型系统
- 虽然 OCaml 内容多，但工程思想很强
- 适合学习高质量工程团队如何写技术复盘

关注关键词：

- performance
- systems
- compiler
- debugging
- concurrency

### Signals and Threads

链接：https://signalsandthreads.com/

价值：

- Jane Street 的深度技术播客
- 有文字稿，适合当文章读
- 很多主题和生产系统、性能工程、分布式系统相关

### HRT Beat

链接：https://www.hudsonrivertrading.com/hrtbeat/

价值：

- Hudson River Trading 的技术和招聘博客
- 比较贴近 HFT 工程场景
- 可以了解 HFT 工程师的实际工作类型

## 低延迟与性能工程

### Mechanical Sympathy

链接：https://mechanical-sympathy.blogspot.com/

价值：

- low latency 经典博客
- 关注 CPU cache、并发、队列、JVM 性能
- 虽然很多内容偏 Java，但底层思想通用

### LMAX Disruptor

链接：https://lmax-exchange.github.io/disruptor/

价值：

- 无锁队列和事件驱动架构经典案例
- 对理解低延迟系统很有帮助

### Cloudflare Blog

链接：https://blog.cloudflare.com/

价值：

- 网络、Linux、eBPF、性能、可靠性
- 虽然不是量化公司，但底层工程很接近

## C++ 与性能

### CppCon YouTube

链接：https://www.youtube.com/user/CppCon

推荐搜索：

- low latency
- trading
- performance
- cache
- lock-free
- microsecond

### Meeting C++

链接：https://www.youtube.com/@MeetingCPP

价值：

- 欧洲 C++ 工程社区
- 低延迟和现代 C++ 内容较多

## 量化论坛

### r/quant

链接：https://reddit.com/r/quant

价值：

- 英文公开量化社区中质量较高
- 偏职业、研究、quant dev、面试和行业讨论

### r/algotrading

链接：https://reddit.com/r/algotrading

价值：

- 偏个人算法交易
- 质量参差，但适合观察散户量化项目常见问题

## 中文开源量化

### WonderTrader

链接：https://github.com/wondertrader/wondertrader

价值：

- 中文 C++ 量化框架
- 适合看工程结构和本土生态

### vn.py

链接：https://github.com/vnpy/vnpy

价值：

- 中文最大开源量化生态之一
- Python 为主，但生态和实盘接口值得研究

## 阅读方法

不要只收藏链接。每篇文章都按这个模板记录：

- 文章解决了什么问题？
- 它的工程约束是什么？
- 用了什么指标判断好坏？
- 有没有可迁移到自己项目的实践？
- 可以变成什么实验？

