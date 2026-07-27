# Analysis V2 Stage 3：蛋白分析页面接入审计

## 1. 审计范围与结论

本轮只审计“蛋白分析”页面，不修改业务代码，不运行 MvImageID，也不触碰旧批量分析流程。

结论：

- 蛋白分析页和批量分析页入口、线程、进度、完成回调、数据库保存函数相互独立。
- 二者目前共同调用 `ProteinAnalysisService.run_one_protein()`，并因此共同使用 `MvImageIDRunner`、`ResultParser`、`cp_input/proteinN` 和 `cp_output/proteinN`。
- `ProteinAnalysisService.run_one_protein()` 会清空当前蛋白的 `cp_input` 和 `cp_output`，不适合直接作为 V2 编排入口。
- V2 应在独立任务目录完成识别、校准、测量和验证，成功后才受控发布到当前蛋白的 `cp_output/proteinN`。
- `AnalysisV2.enabled` 应继续保持 `false`，初期仅通过蛋白分析页的独立开发入口测试。

## 2. 蛋白分析页面实际调用链

### 2.1 页面入口与当前病例

```text
MainWindow
  -> AnalysisWindow(database)
  -> MainWindow 选择病例
  -> AnalysisWindow.set_case(case_data)
     -> refresh_protein_status()
     -> on_protein_changed()
        -> refresh_current_protein_workspace()
```

具体类和函数：

- 页面类：`app.analysis_window.AnalysisWindow`
- 页面创建：`app.main_window.MainWindow` 创建 `self.page_analysis`
- 病例加载：`AnalysisWindow.set_case()`
- 病例编号：`AnalysisWindow.get_current_case_no()`
- 病例已有蛋白结果：`Database.get_protein_analysis_by_case()`

### 2.2 当前蛋白选择

```text
AnalysisWindow.load_protein_combo()
  -> 蛋白按钮 clicked
  -> set_current_protein_key()
  -> on_protein_changed()
  -> refresh_current_protein_workspace()
```

相关函数：

- `load_protein_combo()`：按配置创建蛋白按钮。
- `set_current_protein_key()`：写入 `current_protein_key`。
- `get_current_protein_key()` / `get_current_protein_name()`：读取当前蛋白。
- `on_protein_changed()`：更新表达部位、旧 Pipeline 标签并刷新当前蛋白工作区。

### 2.3 图像导入与历史图像读取

导入链：

```text
select_folder()
  -> import_images()
     -> scan_source_images_for_table()
        -> ImageChannelMatcher.match_folder()
     -> 清空并复制到 raw_images/proteinN
     -> load_images_from_raw_folder()
     -> refresh_table()
```

历史读取链：

```text
refresh_current_protein_workspace()
  -> load_images_from_raw_folder(raw_images/proteinN)
     -> ImageChannelMatcher.match_folder()
  -> refresh_table()
```

注意：`import_images()` 会清空当前蛋白的 `raw_images/proteinN`，但不会清空 `cp_output`。该函数有文件删除副作用，不应被 V2 后端隐式调用。

### 2.4 “运行分析”按钮

```text
btn_run_analysis.clicked
  -> AnalysisWindow.run_analysis()
  -> SingleProteinAnalysisWorker
  -> SingleProteinAnalysisWorker.run()
  -> ProteinAnalysisService.run_one_protein(
       source_folder="",
       overwrite=True,
       reuse_existing_raw=True
     )
  -> prepare_input_folder()
  -> prepare_output_folder()
  -> run_mvimageid()
  -> MvImageIDRunner.run()
  -> parse_result()
  -> ResultParser.parse_image_summary()
```

`ProteinAnalysisService.run_one_protein()` 是当前蛋白页与批量页共享的旧单蛋白执行核心。它会：

1. 使用或重新导入 `raw_images/proteinN`；
2. 删除并重建 `cp_input/proteinN`；
3. 删除并重建 `cp_output/proteinN`；
4. 按旧配置选择 Pipeline；
5. 调用 `MvImageIDRunner`；
6. 使用 `ResultParser` 解析输出。

### 2.5 分析状态更新

- 运行中 UI：`AnalysisWindow.set_running_state()`。
- 后台日志：`SingleProteinAnalysisWorker.log_signal -> AnalysisWindow.append_log()`。
- 完成回调：`AnalysisWindow.on_analysis_finished()`。
- 蛋白完成状态：`refresh_protein_status()` 和 `update_protein_buttons()`，数据来源是数据库，而不是 CSV 文件是否存在。
- 线程释放：`on_analysis_thread_finished()`。

### 2.6 图像结果加载

```text
refresh_current_protein_workspace()
  -> ResultViewer.set_output_dir(cp_output/proteinN)
  -> ResultViewer.refresh_results()
     -> ResultParser.scan_files()
     -> ResultViewer.refresh_image_list()
     -> classify_image_mode()
```

`ResultViewer.refresh_image_list()` 从输出目录扫描图片，根据文件名将图片归类为：

- `G_objects`：绿色对象图；
- `R_objects`：红色对象图；
- `G_colocalized` 等：共定位图。

图片显示只依赖文件存在、扩展名及文件名分类，不依赖数据库。

### 2.7 数值结果加载

页面右侧数值直接来自 CSV：

```text
ResultViewer.refresh_results()
  -> ResultViewer.refresh_summary()
  -> ResultParser.parse_image_summary()
     -> find_image_csv()
     -> find_object_csv()
     -> 读取 Image.csv 和对象 CSV
     -> 计算每视野及总计
```

其中头部优先读取 `G_colocalized.csv`。数值结果查看器不读取数据库。

### 2.8 页面刷新、结果保存和报告数据

- 页面刷新：`refresh_current_protein_workspace()`。
- 结果查看器手动“重新加载”：`ResultViewer.refresh_results()`。
- 切换蛋白：`set_current_protein_key() -> on_protein_changed() -> refresh_current_protein_workspace()`。
- 重新进入/重新选择病例：`set_case() -> on_protein_changed() -> refresh_current_protein_workspace()`。
- 当前蛋白结果入库：`AnalysisWindow.save_analysis_result_to_database()`。
- 数据库总结果：`Database.save_protein_analysis()`。
- 数据库每视野结果：`Database.save_field_result()`。
- `Database.save_protein_analysis()` 先调用 `delete_protein_analysis()`，再插入当前蛋白新结果。
- 报告页数据：`ReportWindow.refresh_analysis_results()` 和 `ReportGenerator.generate_case_report()` 均读取 `Database.get_protein_analysis_by_case()`。
- 分析完成后不会自动生成 PDF；报告页显式生成时才更新 `cases.report_path`。

## 3. 批量分析实际调用链

```text
BatchAnalysisDialog.start_batch_analysis()
  -> get_ready_tasks()
  -> BatchProteinWorker
  -> BatchProteinWorker.run()
     -> 逐个 run_one_protein(task)
        -> ProteinAnalysisService.run_one_protein()
           -> prepare_input_folder()
           -> prepare_output_folder()
           -> run_mvimageid()
              -> MvImageIDRunner.run()
           -> parse_result()
              -> ResultParser.parse_image_summary()
  -> BatchAnalysisDialog.on_finished()
  -> 对每个成功 result 调用 save_result_to_database()
     -> Database.save_protein_analysis()
     -> Database.save_field_result()
```

批量特有逻辑包括：

- 文件夹别名匹配与多蛋白任务生成；
- `BatchProteinWorker.run()` 的多任务循环；
- 进度信号、单任务状态信号及“当前完成后取消”；
- `BatchAnalysisDialog.on_finished()` 的统一入库；
- 批量覆盖确认和批量完成汇总。

这些逻辑不得被蛋白分析页 V2 调用或修改。

## 4. 蛋白分析与批量分析对照

| 功能 | 蛋白分析是否使用 | 批量分析是否使用 | 是否可被 V2 安全复用 | 复用风险 | 建议处理方式 |
|---|---:|---:|---:|---|---|
| `ImageChannelMatcher.match_folder()` | 是 | 间接是 | 是 | 低，纯扫描 | 直接复用 |
| `ProteinAnalysisService.load_images_from_raw_folder()` | 间接同规则 | 是 | 是 | 低，只读扫描 | 可直接复用或统一调用 matcher |
| `ProteinAnalysisService.prepare_input_folder()` | 是 | 是 | 有条件 | 会删除 `cp_input/proteinN`，命名仍是旧 G/R 口径 | 不直接用于 V2 正式目录；提取“标准化复制”纯函数 |
| `ProteinAnalysisService.prepare_output_folder()` | 是 | 是 | 否 | 会删除当前正式 `cp_output/proteinN` | V2 禁止调用 |
| `ProteinAnalysisService.run_one_protein()` | 是 | 是 | 否 | 串联旧 Pipeline、清目录、运行和解析 | 保持旧流程原样 |
| `ProteinAnalysisService.run_mvimageid()` | 间接是 | 间接是 | 有条件 | 配置耦合旧 Pipeline，但参数已有输入/输出目录 | 可提取 Runner 构造或增加明确 Pipeline 参数；不要改旧调用语义 |
| `MvImageIDRunner.build_command()` | 间接是 | 间接是 | 是 | 低 | 直接复用 |
| `MvImageIDRunner.validate_paths()` | 间接是 | 间接是 | 是 | 会创建输出目录 | 在 V2 临时目录使用 |
| `MvImageIDRunner.run()` | 间接是 | 间接是 | 有条件 | 无 timeout，stdout/stderr 合并，日志写入输出目录 | 可作为基础能力；V2 需超时和独立日志适配 |
| `ResultParser.scan_files()` | 是 | 结果对象间接使用 | 是 | 低，只读 | 直接复用 |
| `ResultParser.find_image_csv()` | 是 | 间接是 | 是 | 低，只读 | 直接复用 |
| `ResultParser.find_object_csv()` | 是 | 间接是 | 是 | 需传 `protein_part="head"` | 直接复用并显式传 head |
| `ResultParser.parse_image_summary()` | 是 | 是 | 是 | 输入必须通过 V2 严格校验 | 验证成功后直接复用 |
| `ResultViewer.refresh_results()` | 是 | 否 | 是（UI 发布后） | 会立即展示目录当前内容 | 发布成功后调用 |
| `AnalysisWindow.save_analysis_result_to_database()` | 是 | 否 | 有条件 | 依赖窗口当前病例、蛋白和路径状态 | V2 完成回调中复用；调用前校验上下文未切换 |
| `BatchAnalysisDialog.save_result_to_database()` | 否 | 是 | 否 | 属于批量完成链 | 不调用 |
| `Database.save_protein_analysis()` | 是 | 是 | 有条件 | 先删除当前蛋白数据库旧结果 | 仅在新文件发布成功后调用 |
| `Database.save_field_result()` | 是 | 是 | 有条件 | 多次提交，整体非事务 | 后续可增加“当前蛋白结果事务保存”新方法，不改旧方法 |
| `cp_output/proteinN` | 是 | 是 | 仅作为发布目标 | 直接运行会覆盖旧结果 | 临时验证后受控替换 |

## 5. 底层代码复用分类

### A. 可直接复用

- `ImageChannelMatcher.match_folder()`
- `ResultParser.scan_files()`
- `ResultParser.get_file_summary()`
- `ResultParser.find_image_csv()`
- `ResultParser.find_object_csv()`
- `ResultParser.parse_image_summary()`
- `ResultParser.parse_object_summary()`
- `MvImageIDRunner.get_python_executable()`
- `MvImageIDRunner.build_command()`
- `MvImageIDRunner._get_subprocess_window_options()`（当前是内部方法，若正式复用宜公开化）
- `AnalysisWindow.refresh_current_protein_workspace()`（仅在成功发布和入库后刷新）
- `ResultViewer.set_output_dir()` / `refresh_results()`

### B. 提取或增加轻量参数后复用

- `ProteinAnalysisService.run_mvimageid()`：提取 Runner 配置能力，允许显式传 `pipeline_override`、`input_dir`、`output_dir`，但不改变旧默认行为。
- `ProteinAnalysisService.prepare_input_folder()`：只提取文件匹配和复制部分；V2 不应让它删除旧 `cp_input`。
- `MvImageIDRunner.run()`：V2 需要 timeout、独立 stdout/stderr 路径及结构化时间信息。
- `AnalysisWindow.save_analysis_result_to_database()`：适合成功发布后使用，但应锁定开始时的 `case_id/protein_key`，防止后台运行期间用户切换上下文。
- `Database.save_protein_analysis()` 与 `save_field_result()`：建议新增事务型“保存单个蛋白完整结果”方法，保留旧方法不变。

### C. 不应复用

- `ProteinAnalysisService.run_one_protein()`：旧完整入口，包含删除正式目录和旧 Pipeline 调度。
- `ProteinAnalysisService.prepare_output_folder()`：直接删除正式输出。
- `BatchProteinWorker.run()` / `run_one_protein()`：批量循环和状态控制。
- `BatchAnalysisDialog.start_batch_analysis()`、`on_progress()`、`on_task_status()`、`on_finished()`。
- `BatchAnalysisDialog.save_result_to_database()`：批量结果提交入口。
- `AnalysisWindow.import_images()`：包含清空当前 `raw_images/proteinN` 的交互式导入副作用，不应由 V2 后端调用。
- 任何自动清空多个蛋白目录、重新生成全部蛋白结果或自动生成报告的函数。

## 6. CASE20260727191852 实际检查

检查目录：

`workspace/cases/CASE20260727191852/cp_output/protein1`

当前包含：

- PNG：6 个
  - 2 个 `R_objects` Overlay；
  - 2 个 `G_objects` Overlay；
  - 2 个 `G_colocalized` Overlay。
- CSV：2 个
  - `Image.csv`
  - `G_colocalized.csv`
- 其他文件：无。

当前并非完整三视野结果：只有 `ZBFY022-A-1` 和 `ZBFY022-A-2` 两组 Overlay。`Image.csv` 也只有两行，而且两行均为：

- `Count_R_objects = 383`
- `Count_G_objects = 383`
- `Count_G_colocalized = 163`
- `Math_ColocalizationRate = 0.4256`

只读调用现有 `ResultParser(..., protein_part="head").parse_image_summary()` 成功，得到：

- 视野数：2
- 总 R：766
- 总阳性：326
- 总标定率：42.56%
- 总 `Math_MeanIntensity255`：5494.48
- 当前整数显示强度：7

数据库 `data/analysis.db` 中：

- 病例 ID：8；
- 该病例的 `protein_analysis`：0 行；
- 该病例关联的 `field_results`：0 行。

### 为什么图片可显示但病例数据没有更新

图片显示和病例数据是两条独立链：

1. 图片显示只扫描 `cp_output/protein1`，手工复制 PNG 后，`ResultViewer.refresh_image_list()` 即可发现并显示。
2. 蛋白完成状态、病例详情和报告数据读取数据库。手工复制文件不会调用 `AnalysisWindow.save_analysis_result_to_database()`，因此数据库仍为空。

当前 CSV 本身可以被解析。因此：

- 点击结果查看器“重新加载”、切换到其他蛋白再切回、重新选择病例或重新进入蛋白页面，都会再次解析 CSV，并可刷新蛋白分析页右侧数值。
- 这些刷新动作不会自动保存数据库。
- `refresh_protein_status()`、病例详情和报告页仍显示“未分析/无数据”，因为它们读取数据库。

显式可复用入口是：

- 解析：`ResultParser.parse_image_summary(protein_part="head")`
- 当前蛋白入库：`AnalysisWindow.save_analysis_result_to_database()`
- 页面刷新：`AnalysisWindow.refresh_current_protein_workspace()`

但手工复制的当前文件只有两组且重复使用相同计数，不应直接作为正式三视野结果入库。

## 7. Analysis V2 蛋白分析页最小闭环

推荐流程：

```text
蛋白分析页独立 V2 开发按钮
  -> 固定当前 case_id / case_no / protein_key / protein_name
  -> 确认当前 raw_images/proteinN
  -> 创建独立 analysis_v2 run 目录
  -> 直接 Cellpose 生成头部初始标签
  -> 人工校准并生成 HeadFinalLabels
  -> 在 run 目录准备 FITC/TRITC/Merge/HeadFinalLabels
  -> 使用 measure_head_from_labels.cppipe 测量到临时 output
  -> 严格验证 CSV、对象数、Overlay、公式和日志
  -> 使用现有 ResultParser 做 UI/数据库兼容解析
  -> 受控发布到 cp_output/proteinN
  -> 保存当前蛋白数据库结果
  -> refresh_protein_status()
  -> refresh_current_protein_workspace()
```

约束：

- 不调用 `BatchAnalysisDialog` 或 `BatchProteinWorker`。
- 不调用旧 `ProteinAnalysisService.run_one_protein()`。
- 不处理其他蛋白。
- 不清空其他 `proteinN`。
- `AnalysisV2.enabled=false` 保持不变。
- 初期只提供独立开发按钮或隐藏入口。
- 后台任务必须绑定启动时的病例和蛋白；完成时若当前页面已切换，只发布和入库绑定对象，不误写当前 UI 上的新对象。
- 测量失败、验证失败或入库准备失败时保留旧正式结果。

## 8. 输出目录与发布策略

### 方案 A：直接输出到 cp_output/proteinN

优点：路径简单，旧 `ResultViewer` 和 `ResultParser` 可立即读取。

缺点：

- MvImageID 失败会留下半成品；
- 同名 CSV/Overlay 可能覆盖旧正式结果；
- 新旧文件可能混合；
- 无法在发布前完成整体校验；
- 旧页面刷新可能看到不一致状态。

不推荐。

### 方案 B：独立临时输出后发布

建议输出到：

`workspace/cases/<case>/analysis_v2/<proteinN>/runs/<run_id>/measurement/head/output`

完成全部验证后：

1. 在 `cp_output` 同级准备发布目录；
2. 将已验证结果复制或移动到完整的新目录；
3. 再次核对文件清单和哈希；
4. 将旧 `proteinN` 重命名为唯一备份目录；
5. 将新目录重命名为 `proteinN`；
6. 若发布失败，立即恢复旧目录；
7. 文件发布成功后再解析并写数据库；
8. 数据库保存成功后刷新页面；
9. 备份保留到整个操作确认成功，再按明确策略处理。

Windows 下目录替换不应假定跨卷原子性；应保证临时发布目录与 `cp_output` 位于同一卷、同一父目录，并实现可恢复的受控重命名。

推荐方案 B。

## 9. 对批量分析零影响的保证

- 不修改 `app/batch_analysis_dialog.py`。
- 不修改 `BatchProteinWorker`、批量状态、进度和完成回调。
- 不改变 `ProteinAnalysisService.run_one_protein()` 的签名或默认行为。
- 不改变 `MvImageIDRunner` 的旧调用语义；如需增强，新增兼容参数且默认值保持旧行为，或在 V2 适配层组合旧纯能力。
- 不改变 `ResultParser` 现有字段和公式。
- V2 只接收单一 `case_id + protein_key`，路径解析后验证必须位于该病例当前蛋白范围。
- 发布函数只允许替换一个明确的 `cp_output/proteinN`。
- 旧批量分析继续读取旧配置 Pipeline，V2 正式管道仅由 V2 编排器显式传入。

## 10. 当前未提交 Stage 3 代码审查

### MvImageID 调用方案（已验证）

最终采用现有正式调用器：

- 直接复用 `core/mvimageid_runner.py` 中的 `MvImageIDRunner`；
- Analysis V2 不再维护独立的外部进程调用器；
- 不再增加额外的命令行壳层；
- 不修改旧 Runner 的命令构造、工作目录、插件目录和默认调用语义；
- 不修改旧批量分析流程。

真实验证结果：

- 单视野测量成功，退出码为 0；
- `Count_R_objects=383`；
- `Count_G_objects=383`；
- `Count_G_colocalized=163`；
- `Math_ColocalizationRate=0.4256`；
- 三视野测量成功，一次运行三个视野；
- `Image.csv` 为 3 行；
- `G_colocalized.csv` 对象总数与阳性对象总数一致；
- 三个视野共生成 9 张 Overlay；
- FinalObjects 对象数与 Image.csv 中的 `Count_R_objects` 一致。

测试期间由 Codex 执行环境启动外部进程曾出现超时，但用户在普通 PowerShell 中通过现有 `MvImageIDRunner` 运行成功。因此该现象按测试执行环境差异处理，不作为管道或正式 Runner 缺陷。

### `head_measurement_service.py`

可保留的部分：

- FinalObjects JSON 动态期望对象数；
- 输入复制而非修改原文件；
- 临时 measurement 目录；
- 输出、状态、日志和 manifest 的整体思路；
- 失败保留现场。

问题：

- 服务面向独立 smoke task，尚未绑定蛋白分析页的 `case_id/protein_key`；
- 使用私有 `AnalysisTaskPaths._build()`；
- 自行构建插件路径，和旧配置读取重复；
- 输出验证、发布和数据库适配尚未分层；
- 允许从历史 `head_calibrated` 的 failed 状态重试，需要更明确的状态迁移规则；
- 不具备安全发布到 `cp_output/proteinN` 的能力。

建议：保留为内部测量阶段服务，不直接接 UI；外层新增单蛋白工作流服务负责上下文、校准交互、发布和回调。

### `head_measurement_result.py`

可保留的部分：

- 对 `Image.csv` 和 `G_colocalized.csv` 的严格一致性校验；
- 总 R、总阳性、总强度和总标定率公式正确；
- 输出结构化 JSON。

重复点：

- CSV 查找、读取和业务汇总与 `ResultParser` 重叠。

建议：将其收缩为“V2 严格验证器”，验证通过后调用现有 `ResultParser.parse_image_summary("head")` 生成兼容结果；不要维护第二套 UI/数据库计算实现。

### `smoke_head_measurement.py`

可保留：

- 独立命令行入口；
- MvImageID 源码目录前后清单；
- 结果摘要。

不应进入蛋白页面正式运行链：

- 默认任务根目录和固定实验任务属于开发测试；
- 源环境清单扫描成本较高；
- 不负责正式输出发布和数据库事务。

建议：只保留为开发工具，不由 GUI 调用。

### 其他未提交 Stage 3 文件

- `measure_head_from_labels.cppipe`：与已验证实验管道 SHA256 一致，可作为 V2 正式测量管道保留。
- `STAGE3_HEAD_MEASUREMENT.md`：可作为测量阶段技术说明保留，但应与本审计的蛋白页发布策略对齐。
- `task_state.py` 的 `head_measuring/head_measured`：若继续采用现有状态机则合理，但应在正式实施时单独审查状态迁移。
- `core/analysis_v2/__init__.py` 导出：应在保留类边界确定后再决定，当前不要继续扩展。

### 300 秒超时的代码层原因

直接原因明确：

- `smoke_head_measurement.py --timeout 300`
- `MvImageIDMeasurementRunner` 将 timeout 设为至少 300 秒；
- `subprocess.run(..., timeout=300)` 到期抛出 `TimeoutExpired`；
- Runner 捕获后写 stderr，并返回 `return_code=-2`。

当次 stdout 为空、测量输出为空，因此现有证据只能确认“外部命令 300 秒内未退出”。无法仅凭当前代码断言是 MvImageID 初始化卡住、外部子进程问题还是三视野运行时间超过 300 秒。不能把未证实原因写成结论。

## 11. 建议的最少文件改动

正式实施时建议最少涉及：

1. `app/analysis_window.py`
   - 增加仅针对当前蛋白的 V2 开发入口、工作流启动、校准入口和完成回调。
2. `core/analysis_v2/head_workflow_service.py`
   - 建议新增；只编排单病例单蛋白的识别、校准、测量、验证和发布。
3. `core/analysis_v2/head_measurement_service.py`
   - 保留并收缩为独立任务目录内的测量阶段。
4. `core/analysis_v2/head_measurement_result.py`
   - 改为严格验证器，并复用 `ResultParser` 输出兼容结果。
5. `core/analysis_v2/head_measurement_adapter.py`
   - 不建议单独新增；其职责可留在 measurement service。
6. `core/analysis_v2/head_result_adapter.py`
   - 仅在 `ResultParser` 兼容映射明显复杂时新增；当前没有必要。
7. 可选新增 `core/analysis_v2/head_result_publisher.py`
   - 若发布、备份和恢复逻辑较多，单独封装比塞入 workflow service 更安全。

初期不需要修改：

- `app/batch_analysis_dialog.py`
- `core/protein_analysis_service.py`
- `core/mvimageid_runner.py`
- `core/result_parser.py`
- `core/report_generator.py`

## 12. 推荐实施顺序

1. 先整理当前未提交 Stage 3 代码边界，不接 GUI。
2. 将 `head_measurement_result.py` 收缩为严格校验，并用 `ResultParser` 生成兼容结果。
3. 明确 Runner 复用方式，保留旧 Runner 行为。
4. 实现同父目录的安全发布器及失败恢复测试。
5. 实现 `head_workflow_service.py`，输入固定为单一病例和单一蛋白。
6. 用独立开发入口完成一个蛋白的端到端测试，`AnalysisV2.enabled` 仍为 false。
7. 成功发布后调用当前蛋白数据库保存入口，再刷新蛋白页。
8. 回归验证旧蛋白单独分析和旧批量分析结果完全不变。
9. 最后再决定是否把 V2 开发入口升级为正式按钮。
