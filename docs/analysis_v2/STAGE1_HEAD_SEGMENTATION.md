# Analysis V2 Stage 1：直接 Cellpose 头部识别

## 已确认的技术方向

Stage 1 的目标是生成可供人工校准的头部识别中间态，不是自动生成最终测量结果。

识别中间态不通过 CellProfiler 管道。主程序准备明确的 `worker_input.json`，然后使用固定解释器：

```text
F:\MvImageID\.venv\Scripts\python.exe
```

直接运行：

```text
tools\analysis_v2\direct_cellpose_worker.py
```

一个批次只启动一个 worker 进程，`cpsam` 模型只初始化一次，所有 TRITC 按输入 JSON 的顺序依次推理。

## 通道职责

- `*_RGB_TRITC.tif`：Cellpose 头部识别的唯一图像输入。
- `*_RGB_FITC.tif`：Stage 1 仅复制和记录，供后续人工观察与测量使用。
- `*_RGB_Merge.tif`：Stage 1 仅复制和记录，供后续人工观察使用。

最终测量阶段才重新调用正式 MvImageID/CellProfiler 管道。

## 固定基线

当前固定基线来自已验证实验 `experiments/r_preview/benchmark_cellpose_r_preview.py`：

- model：`cpsam`
- channels：`[0, 0]`
- diameter：`null`（沿用已验证实验行为）
- flow_threshold：`null`（沿用已验证实验行为）
- cellprob_threshold：`null`（沿用已验证实验行为）
- normalize：`false`
- do_3D：`false`
- min_area：`20.0`
- max_area：`5000.0`
- min_circularity：`0.2`
- remove_edge_masks：`false`
- max_equivalent_diameter：`null`
- 输入归一化：1%–99% 分位映射到 float32 `[0, 1]`

这些值会完整写入 `worker_input.json`、`worker_result.json` 和每个视野的对象 JSON。

## 正式输出

每个视野输出：

```text
segmentation/head/<field_id>_HeadInitialLabels.tif
segmentation/head/<field_id>_HeadInitialOverlay.png
segmentation/head/<field_id>_HeadInitialObjects.json
```

批次输出 `worker_result.json`，记录算法环境、GPU、参数、模型初始化时间、总时间、逐视野性能和标签统计。

标签必须是二维 `uint16` 对象标签图：背景为 0、正整数标签连续、对象数大于 0，并且不能是普通二值图。

## 运行与日志

`DirectCellposeRunner` 使用 `-u` 和以下环境变量启动 worker：

```text
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
```

日志位于任务目录的 `logs`：

- `task.log`
- `events.jsonl`
- `environment.json`
- `head_segmentation_command.txt`
- `head_segmentation_stdout.log`
- `head_segmentation_stderr.log`

默认超时为 120 秒，可配置但不得低于 120 秒。单视野失败会写入真实错误和 traceback，不生成伪成功记录，也不清理失败现场。

## 当前边界

Stage 1 完成后进入人工头部校准。当前阶段不运行 RunOmnipose、不接入正式 GUI，也不执行最终测量。
