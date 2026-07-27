# Analysis V2 Stage 2A：人工头部校准 MVP

Stage 2A 用于人工检查和修正 Stage 1 生成的头部初始标签。它是独立工具，暂未接入正式蛋白分析页面，也不会再次运行 Cellpose、Omnipose 或最终测量管道。

## 启动方法

在项目目录执行：

```powershell
F:\sperm_protein_analyzer\.venv\Scripts\python.exe `
  tools\analysis_v2\smoke_head_calibration.py `
  --task-root F:\sperm_protein_analyzer\workspace\analysis_v2_smoke\20260727_162808_89cf95
```

工具默认加载任务中的三个视野，默认显示 Merge 底图。底图下拉框可以在 Merge 和 TRITC 之间切换；FITC 已保留在任务数据中，本版不单独显示。

## 界面按钮

- `上一视野`、`下一视野`：切换相邻视野。
- 视野下拉框：直接选择指定视野。
- `选择`：回到默认对象选择模式。
- `新增头部`：在原图上拖动椭圆。
- `删除选中头部`：删除黄色选中的对象，Delete 键作用相同。
- `撤销`、`重做`：对应 Ctrl+Z 和 Ctrl+Y。
- `适应窗口`：显示整幅图。
- `1:1`：一个图像像素对应一个显示像素。
- `放大`、`缩小`：调整画布缩放。
- `完成头部校准`：验证三个视野并生成最终文件。

鼠标滚轮可以缩放；中键拖动或按住空格后用左键拖动可以平移。

## 删除对象

在选择模式中单击白色轮廓内部，对象轮廓变为黄色。状态栏会显示视野号、对象 ID、面积和对象总数。点击删除按钮或按 Delete 后，该对象从工作标签中删除并立即自动保存。

单击背景会取消选择。

## 新增椭圆

点击 `新增头部`，然后在图像上按住左键拖动。松开后，椭圆会按原图坐标写入标签图。有效面积过小时会被拒绝；新增成功后自动返回选择模式并自动保存。

新增区域只写入当前背景像素，不覆盖已有对象。

## 撤销和重做

删除对象和新增椭圆都可以撤销或重做。最多保留最近 100 步局部变化，新操作会清空重做栈。操作历史保存的是局部标签区域，不会为每一步复制整张 2200×2748 标签图。

## 自动保存和恢复

每次删除、新增、撤销、重做、切换视野和关闭窗口时都会保存：

```text
calibration/head/<field>_HeadWorkingLabels.tif
calibration/head/<field>_HeadCalibrationState.json
```

初始标签 `HeadInitialLabels.tif` 始终保持不变。中途退出后再次打开同一任务，工具优先读取 WorkingLabels，继续上次编辑。

## 完成后的文件

点击完成后会连续重编号为 `1..N`，并生成：

```text
calibration/head/<field>_HeadFinalLabels.tif
calibration/head/<field>_HeadFinalObjects.json
calibration/head/<field>_HeadCalibrationOverlay.png
calibration/head/<field>_HeadCalibrationState.json
```

FinalLabels 是最终校准标签；InitialLabels 是 Stage 1 的原始识别结果，两者用途不同。FinalObjects 会记录面积、质心、包围框，以及根据初始标签主要重叠判断的 `initial` 或 `manual` 来源。

## 日志

日志继续写入原任务：

```text
logs/task.log
logs/events.jsonl
```

保存失败会保留内存标签、阻止窗口关闭并显示明确错误。

## 后续接入

下一步可在正式蛋白分析页面中增加“人工校准头部”入口，向窗口传入当前 Analysis V2 任务目录；校准后的测量阶段只读取 HeadFinalLabels，不再读取 HeadInitialLabels。
