# Analysis V2

Analysis V2 将自动识别、人工校准和最终测量分成可追踪的独立阶段。当前 `config.ini` 中 `AnalysisV2.enabled` 保持为 `false`，旧正式分析流程不受影响。

## 流程

```text
用户选择一个蛋白的视野文件
→ 配对 TRITC / FITC / Merge
→ 复制到唯一任务目录
→ 固定 F:\MvImageID Python 环境直接运行 Cellpose
→ 生成 uint16 头部初始标签、overlay 和对象 JSON
→ 人工头部校准
→ 后续尾部处理与校准
→ 最终测量阶段重新调用正式 MvImageID 管道
```

Stage 1 的识别中间态不经过 CellProfiler。CellProfiler/MvImageID 管道仅保留给后续最终测量阶段。

## Stage 1 文件

- `core/analysis_v2/task_paths.py`：任务目录规范。
- `core/analysis_v2/task_state.py`：原子状态写入和状态历史。
- `core/analysis_v2/stage_logger.py`：文本及结构化事件日志。
- `core/analysis_v2/environment_snapshot.py`：主程序和外部算法环境快照。
- `core/analysis_v2/manifest_store.py`：任务文件、大小和 SHA256 清单。
- `core/analysis_v2/direct_cellpose_runner.py`：启动固定算法解释器和正式 worker。
- `core/analysis_v2/segmentation_service.py`：复制输入、调用 worker、验证结果并推进状态。
- `tools/analysis_v2/direct_cellpose_worker.py`：直接 Cellpose 批量识别。
- `tools/analysis_v2/smoke_head_segmentation.py`：三个真实视野的限定 smoke。

## 图像职责

- TRITC：直接 Cellpose 头部识别。
- FITC：Stage 1 只记录，供后续测量和人工观察。
- Merge：Stage 1 只记录，供人工观察。

一个批次中的所有 TRITC 由同一 worker 顺序处理，模型只加载一次。当前固定模型是 `cpsam`，参数来自已经验证的 R 通道预览实验。

输出是“高召回、边界基本合理、便于人工删除与补充”的校准中间态，不代表自动最终结果。

详细运行约束和输出规范见 [STAGE1_HEAD_SEGMENTATION.md](STAGE1_HEAD_SEGMENTATION.md)。
