# Windows 一键打包说明

本目录提供可重复执行的 Windows `onedir` 打包基础设施。程序名为 `SpermProteinAnalyzer`，要求 Python 3.8.3。构建过程使用独立 `.venv_build`，不会向主项目 `.venv` 或 `F:\MvImageID` 安装任何包。

## 首次使用

1. 确认主仓库工作区 clean。
2. 确认主项目解释器存在：

   ```text
   F:\sperm_protein_analyzer\.venv\Scripts\python.exe
   ```

3. 在主源码目录执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1
   ```

也可以显式指定 Python 3.8.3：

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\packaging\windows\build.ps1 `
  -PythonExe "D:\Python38\python.exe"
```

默认构建目录：

```text
F:\sperm_protein_analyzer_pack_20260803
```

默认成品目录：

```text
F:\sperm_protein_analyzer_Test_20260803
```

任一目标目录已存在时，脚本会停止，不会覆盖。

## 后续重复打包

每次使用新日期：

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\packaging\windows\build.ps1 `
  -BuildDate 20260804
```

也可以指定两个全新目录：

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\packaging\windows\build.ps1 `
  -BuildDate 20260804 `
  -BuildRoot F:\builds\spa_20260804 `
  -OutputRoot F:\releases\spa_20260804
```

构建目录和成品目录不能位于主源码内、不能互相包含，也不能指向旧成品、`F:\MvImageID`、隔离目录或项目外备份目录。

## 构建过程

脚本会依次：

1. 检查 Python 3.8.3 和 Git clean。
2. 使用 `git archive HEAD` 导出干净源码。
3. 再次确认 Git 导出副本中正式 tools 文件完整。
4. 创建独立 `.venv_build`。
5. 将独立环境的 pip 固定为 `24.3.1`。
6. 只安装有预编译 wheel 的锁定依赖，避免意外源码编译。
7. 执行 `pip check` 和关键模块导入检查。
8. 使用 PyInstaller 6.20.0 生成 `onedir` 成品。
9. 自动整理 EXE 同级的 `assets`、`core`、`pipelines`、正式 `tools` 和两个 INI。
10. 启用 Analysis V2，并用 UTF-8 无 BOM 保存 `config.ini`。
11. 创建空的 `data`、`reports`、`workspace`。
12. 使用成品 `config.ini` 配置的 MvImageID Python，静态验证外置 `core`、联合尾部提升脚本及成品根目录识别；不运行真实分析或写入数据。
13. 检查备份管道和开发工具未进入成品。
14. 生成构建环境、Git 来源和 SHA-256 文件清单。

失败时保留构建目录和日志，不自动删除现场。

## 成品结构

```text
SpermProteinAnalyzer.exe
_internal\
assets\
core\
pipelines\
tools\
config.ini
data\
reports\
workspace\
build_environment.txt
git_source_commit.txt
build_manifest.txt
```

`assets`、`pipelines` 和两个 INI 同时在 `_internal` 与 EXE 同级保留，这是为了兼容当前冻结资源路径和可编辑外置配置。

EXE 同级的外置 `core` 专供 `config.ini` 中配置的 MvImageID Python 子进程导入。主 EXE 仍使用 PyInstaller 冻结模块，外置目录不会替换其冻结 `core`。联合尾部原子提升脚本需要调用项目内的状态、清单和测量服务，因此不得删除外置 `core`。

`tools`、`core`、`pipelines` 必须来自同一个 Git 提交版本。构建脚本统一从 `git archive HEAD` 的干净副本复制这些目录，禁止从当前工作树、旧成品或其他版本拼装。

## 数据迁移

成品不携带数据库、病例、PDF、TIFF、CSV、日志或历史运行结果。

客户端升级时：

1. 关闭旧程序并完整备份旧软件目录。
2. 将新版完整文件夹复制到 F 盘新目录。
3. 保留新版 `config.ini`，不要用旧版整体覆盖；旧配置可能缺少新版字段或保留不兼容路径，只应逐项核对并迁移确有需要的值。
4. 按需迁移旧版 `data`、`reports`、`workspace`。
5. 检查新版目录可写。
6. 验证病例、报告、头部分析、尾部分析、人工校准和报告生成。
7. 验证完成后再更新快捷方式。
8. 旧版备份至少保留到客户完成一轮真实测试。

## 常见错误

- **pip 或 OpenCV 安装失败**：脚本先固定独立环境 pip，并强制使用 wheel。不要向主 `.venv` 安装 OpenCV。
- **cv2 未收集**：查看 `build.log`，确认关键模块导入检查通过，检查 `_internal` 中 cv2 和相关 DLL。
- **tools 缺失**：脚本会同时检查当前工作树、Git HEAD 导出副本和最终成品。
- **core 缺失或被删除**：MvImageID Python 无法使用 `_internal` 中仅供冻结 EXE 使用的模块；重新部署同一 Git 提交构建出的完整外置 `core`。
- **MvImageID 路径错误**：检查成品 `config.ini` 中的 `source_project_dir`、`plugins_directory`、`python_exe` 和 `module_name`。
- **config.ini BOM**：脚本写入后会检查 BOM，并使用 Python `configparser` 再次读取验证。
- **目录无写入权限**：不要将客户测试版放在普通用户不可写的目录。
- **Analysis V2 未启用**：脚本会强制设置 `enabled = true` 并验证。
- **已有同名目录**：脚本不会覆盖，改用新的 `BuildDate` 或新目录。
