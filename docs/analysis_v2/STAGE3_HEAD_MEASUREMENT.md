# Analysis V2 Stage 3：校准后头部测量

## 阶段边界

Stage 3 只测量已经人工校准的头部标签，不运行 Cellpose、Omnipose，也不包含尾部分析或 GUI 接入。

识别阶段直接调用 Cellpose，是为了获得可编辑的初始标签并避免把识别模型耦合进 MvImageID 管道。测量阶段重新使用 MvImageID，是为了复用已经在图形界面验证通过的对象扩展、强度测量、共定位筛选、CSV 导出和 Overlay 生成逻辑。

## 测量对象链

正式管道通过 `NamesAndTypes` 将每个 `HeadFinalLabels` 标签图直接加载为 `R_objects`，不再过滤、删除或重建它。随后：

1. `R_objects` 扩展得到对位后的 `G_objects`；
2. 测量 `G_objects` 的绿色强度；
3. 按已验证阈值筛选得到 `G_colocalized`；
4. 导出 `Image.csv`、`G_colocalized.csv` 及三类对象 Overlay。

核心阈值、强度公式和标定率公式均保持实验管道原样。

## 输入、输出和日志

- 原始输入：`<task_root>/input`
- 校准标签：`<task_root>/calibration/head/*_HeadFinalLabels.tif`
- 测量副本：`<task_root>/measurement/head/input`
- MvImageID 输出：`<task_root>/measurement/head/output`
- 输入清单：`<task_root>/measurement/head/measurement_input.json`
- 汇总结果：`<task_root>/measurement/head/head_measurement_result.json`
- 日志：`<task_root>/logs`

Runner 分别保存命令、标准输出和标准错误；任务级日志还包括 `task.log`、`events.jsonl` 和 `environment.json`。失败时现场不会被清理。

## 三视野汇总规则

头部荧光强度：

`所有 G_colocalized.Math_MeanIntensity255 之和 / 所有 Count_R_objects 之和`

头部标定率：

`所有 Count_G_colocalized 之和 / 所有 Count_R_objects 之和`

不能对各视野百分比做简单平均。

## 运行 smoke

```powershell
F:\sperm_protein_analyzer\.venv\Scripts\python.exe `
  tools\analysis_v2\smoke_head_measurement.py `
  --task-root F:\sperm_protein_analyzer\workspace\analysis_v2_stage2_gui_smoke\20260727_164913 `
  --pipeline F:\sperm_protein_analyzer\pipelines\analysis_v2\measure_head_from_labels.cppipe `
  --mvimageid-root F:\MvImageID `
  --timeout 300
```

## 成功判据

- MvImageID 返回码为 0；
- `Image.csv` 恰好三行；
- 每个视野 `Count_R_objects` 与对应 FinalObjects JSON 的对象数一致；
- `Count_G_objects == Count_R_objects`；
- 阳性数不超过 R 对象数，标定率与计数相除一致；
- `G_colocalized.csv` 对象行数等于各视野阳性数之和；
- 每视野三类 Overlay 均存在；
- state 为 `head_measured`，输出和日志已登记 manifest；
- MvImageID 指定源码及插件目录运行前后无变化。

## 后续 GUI 接入

正式界面下一步只需在 `head_calibrated` 后调用 `HeadMeasurementService`，展示 Runner 进度与错误，并从结果 JSON 读取汇总字段。Stage 3 本身不修改现有 GUI。
