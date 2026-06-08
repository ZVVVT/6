def run_cellprofiler(self):
    if not self.current_case:
        QMessageBox.information(self, "提示", "请先选择病例。")
        return

    if not self.imported_images:
        QMessageBox.information(self, "提示", "请先导入图片。")
        return

    complete_items = [
        item for item in self.imported_images
        if item.get("status") == "完整"
    ]

    if not complete_items:
        QMessageBox.warning(self, "提示", "没有完整的 R/G 视野，无法运行分析。")
        return

    protein_name = self.protein_combo.currentText()
    case_no = str(self.current_case.get("case_no", "")).strip()

    source_project_dir = self.config.get_source_project_dir().resolve()
    venv_activate = self.config.get_venv_activate().resolve()
    module_name = self.config.get_module_name()
    pipeline_file = self.config.get_pipeline_by_protein(protein_name).resolve()
    plugins_directory = self.config.get_plugins_directory().resolve()
    log_file = self.config.get_log_file().resolve()
    powershell_exe = self.config.get_powershell_exe()

    if not source_project_dir.exists():
        QMessageBox.critical(
            self,
            "错误",
            f"MvImageID 源码目录不存在：\n{source_project_dir}\n\n请检查 config.ini 中的 source_project_dir。"
        )
        return

    if not venv_activate.exists():
        QMessageBox.critical(
            self,
            "错误",
            f"虚拟环境激活脚本不存在：\n{venv_activate}\n\n请检查 config.ini 中的 venv_activate。"
        )
        return

    if not pipeline_file.exists():
        QMessageBox.critical(
            self,
            "错误",
            f"Pipeline 文件不存在：\n{pipeline_file}\n\n请检查 config.ini 中的 pipeline 路径。"
        )
        return

    if not plugins_directory.exists():
        QMessageBox.critical(
            self,
            "错误",
            f"插件目录不存在：\n{plugins_directory}\n\n请检查 config.ini 中的 plugins_directory。"
        )
        return

    workspace_root = self.config.get_workspace_root()
    cp_input_dir = workspace_root / case_no / "cp_input" / protein_name
    cp_output_dir = workspace_root / case_no / "cp_output" / protein_name

    cp_input_dir = cp_input_dir.resolve()
    cp_output_dir = cp_output_dir.resolve()

    try:
        self.prepare_cp_input(complete_items, cp_input_dir)
    except Exception as e:
        QMessageBox.critical(self, "错误", f"准备输入目录失败：\n{e}")
        return

    cp_output_dir.mkdir(parents=True, exist_ok=True)
    self.current_cp_output_dir = cp_output_dir

    self.append_log("准备以源码环境方式运行分析。")
    self.append_log(f"源码目录：{source_project_dir}")
    self.append_log(f"虚拟环境：{venv_activate}")
    self.append_log(f"模块名称：{module_name}")
    self.append_log(f"Pipeline：{pipeline_file}")
    self.append_log(f"插件目录：{plugins_directory}")
    self.append_log(f"输入目录：{cp_input_dir}")
    self.append_log(f"输出目录：{cp_output_dir}")
    self.append_log(f"日志文件：{log_file}")

    self.set_running_state(True)

    self.cp_worker = CellProfilerWorker(
        powershell_exe=powershell_exe,
        source_project_dir=str(source_project_dir),
        venv_activate=str(venv_activate),
        module_name=module_name,
        pipeline_file=str(pipeline_file),
        input_dir=str(cp_input_dir),
        output_dir=str(cp_output_dir),
        plugins_directory=str(plugins_directory),
        log_file=str(log_file),
    )

    self.cp_worker.log_signal.connect(self.append_log)
    self.cp_worker.finished_signal.connect(self.on_cellprofiler_finished)
    self.cp_worker.start()