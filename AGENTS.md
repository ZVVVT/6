# AGENTS.md

## 项目说明

这是精子蛋白荧光分析软件项目。

项目源码目录：F:\sperm_protein_analyzer

以下目录是打包测试或临时目录，除非用户明确要求，禁止修改：
- F:\sperm_protein_analyzer_pack
- F:\sperm_protein_analyzer_Test

## 技术栈

- Python 3.8.3
- PyQt / Qt 界面
- CellProfiler / MvImageID 源码环境后台分析
- CSV 结果读取与统计
- reportlab 生成 PDF 报告
- GitHub 仓库：https://github.com/ZVVVT/6

## 重要约束

- 必须兼容 Python 3.8，不要使用 Python 3.9+ 才支持的语法，例如 Path | None。
- 不要一次性大重构。
- 每次只处理一个明确问题。
- 不要擅自修改核心计算公式。
- 不要擅自修改 CellProfiler 管线文件，除非任务明确要求。
- 不要擅自修改报告字段口径，除非任务明确要求。
- 不要擅自删除病例数据、workspace 数据、测试输出。
- 不要把绝对路径写死到业务代码里，除非原项目已经这样设计且任务要求保持一致。
- 不要修改 Git 远程地址。
- 不要自动执行 git commit 或 git push，除非用户明确要求。
- 修改完成后必须说明改了哪些文件、为什么改、如何测试。
- 修改完成后优先提供 git diff 摘要。

## 开发习惯

每次修改前建议先查看：git status

修改完成后建议用户手动执行：
git status
git add .
git commit -m "本次修改说明"
git push

## 用户偏好

- 使用简体中文回复。
- 给出专业、直接、可执行的方案。
- 复杂问题先做方案，再小步执行。
- 代码修改应尽量提供完整可替换文件或明确 diff。
- 中文界面、报告、图片文字优先使用：SimHei、SimSun、Microsoft YaHei、Noto Sans SC。
