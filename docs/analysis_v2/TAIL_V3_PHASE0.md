# Tail V3 Phase 0：Gold 与统一性能基线

Tier1 固定为四个独立 field：`ZBFY023-C-1`、`ZBFY020-C-1`、
`ZBFY016-C-1`、`ZBFY022-C-1`。Gold manifest 位于本机运行数据目录
`workspace/benchmarks/tail_v3_gold/gold_manifest.json`，只引用现有正式 run，
不复制或修改 `workspace/cases` 中的正式结果。
当前 manifest 文件 SHA256 为
`fd4e1fef2779b81fc72d4753c0e054cbb81e20a7273fae64fe8b6573820f9d30`。

## Correctness 合同

输入 FITC、TRITC、Merge 按文件 SHA256 固定，并记录 TIFF shape、dtype。
HeadFinalLabels、C18B 的 06/07 标签和三个 Tail 最终标签同时记录文件 SHA256、
shape、dtype。baseline 与 candidate 的标签比较使用 `numpy.array_equal`，不使用
IoU 或容差。

TailFinalObjects 同时记录文件 SHA256 和规范化业务语义 SHA256。业务比较默认
保留所有字段，只忽略工具中显式列出的路径、生成时间、attempt 和日志路径键。
当前正式对象字段 `tail_object_id`、`association_status`、`head_label_id`、
`pixel_count`、`source`、`fragment_label_id` 均严格比较；以后新增字段默认也参与
比较，不能未经合同评审加入忽略列表。

Image.csv 固定并严格比较 `ImageNumber`、`Count_G_objects`、
`Count_R_objects`、`Count_R_colocalized`、`Math_ColocalizationRate`。
G_objects.csv 按 field + ObjectNumber 严格对齐并比较 ObjectNumber、
AreaShape_Area、Math_MeanIntensity255。数值按精确十进制值比较，不设置浮点容差。
工具还验证每个 TailFinalLabels 对象的像素面积同时等于 TailFinalObjects.pixel_count
和 G_objects.csv 的 AreaShape_Area。

运行方式：

```powershell
python tools/analysis_v2/tail_v3_gold.py verify --manifest workspace/benchmarks/tail_v3_gold/gold_manifest.json
python tools/analysis_v2/tail_v3_gold.py compare --baseline-run <baseline-run> --candidate-run <candidate-run> --field-id <field-id>
```

## 唯一正式计时边界

T0 是 AnalysisV2TaskRunner 完成请求校验并建立任务路径后、开始 Head 分析前的
`time.perf_counter()`。此时正式 field 输入已经由请求提供。T1 是
TailMeasurementService 完成输出严格校验并将状态更新到 `tail_measured` 后返回的
`time.perf_counter()`。主指标 `T_total = T1 - T0`，事件名为
`tail_v3_timing_finished`，`duration_seconds` 与 `machine_wall_seconds` 均使用 wall
clock。

自动 benchmark 固定 `interactive=false`，因此 `human_wait_seconds=0`。如果以后测量
交互工作流，Head Editor 和 Tail Editor 从打开等待用户到用户确认的区间只累计到
`human_wait_seconds`，并从 `machine_wall_seconds` 排除。不得把人工停留计入主指标。
Publisher/DB 位于 T1 之后，事件中单独记为 0 且明确标记未包含；如需评估发布，
另立指标，不能并入 T_total。阶段 wall clock 包括 Head、Tail Core、fragment filter、
Association/Editor adapter、Finalizer、Measurement、C18B 编排开销以及 checkpoint
残差。checkpoint 残差定义为 T0/T1 内未落入上述连续阶段 wrapper 的 runner 边界
记账时间；阶段内部发生的状态/manifest 写入仍包含在对应阶段 wall clock 中，不能重复
扣除。内部 C18B timing.json 仅作阶段诊断，不能替代统一 wall clock。

## 性能采样协议

每次记录硬件（CPU、GPU、内存、存储）、操作系统、Python/MvImageID/CellProfiler
环境、代码 commit、管线文件 SHA256、参数文件 SHA256、Gold manifest SHA256 和输入
SHA256。每次运行使用新的 run 目录，禁止复用已有输出跳过阶段。

Cold run 定义为目标模型/worker 没有提前启动、不进行主动 warm-up，并使用全新 run
目录、不复用分析产物或 checkpoint 的完整运行。操作系统是否刚启动不是必要条件，
也不执行系统文件缓存清理命令。Warm run 定义为先完成一次不计入样本的同配置完整
预热，随后仍以新 run 目录执行的完整运行；允许正常 OS 文件缓存和 GPU/驱动初始化
状态，不得复用分析产物。必须记录实际 worker 生命周期；如果每个 run 都退出模型
进程，不得将 warm 描述为模型驻留。Cold 与 warm 分组报告，不能混算。

每个 Tier1 field 日常基线至少采集 3 次有效 cold 和 3 次有效 warm；正式优化关键
里程碑各采集 5 次。每个 field 分别报告 median、min、max；Tier1 汇总报告 median、
P90、max。小样本 P90 使用 nearest-rank：升序第 `ceil(0.90 * n)` 个值，并在报告中
注明。失败或 correctness comparator 不通过的运行不是性能有效样本，必须保留失败
记录且不能用补选较快结果替代。

现有 `137.303s` 和 2026-09-08 四组阶段耗时只标记为 historical baseline，因计时
边界不同，不与新协议样本合并。
