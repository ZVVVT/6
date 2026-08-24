"""Analysis V2 尾部编辑器顺序监管器（不显示第三个窗口）。"""

from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import QMessageBox

from core.analysis_v2.tail_calibration_service import (
    complete_tail_calibration,
    publish_tail_final_labels,
)
from core.analysis_process_registry import analysis_process_registry


class TailCalibrationController(QObject):
    """依次启动真实尾部编辑器，并在每次关闭时检查保存产物。"""

    calibration_completed = Signal(object)
    calibration_aborted = Signal(str)
    log_signal = Signal(str)

    def __init__(self, task_root: Path, field_payloads, parent=None) -> None:
        super().__init__(parent)
        self.task_root = Path(task_root).resolve()
        self.payloads = [dict(item) for item in field_payloads]
        self.results = []
        self.index = 0
        self._stopping = False
        self.process = QProcess(self)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

    def start(self) -> None:
        if not self.payloads:
            self.calibration_aborted.emit("没有可校准的尾部视野。")
            return
        self._start_editor()

    @property
    def payload(self):
        return self.payloads[self.index]

    def _editor_arguments(self):
        return [
            str(Path(self.payload["editor_script"]).resolve()),
            "--merge", self.payload["merge"],
            "--green", self.payload["green"],
            "--probability", self.payload["probability"],
            "--fragments", self.payload["fragments"],
            "--head-labels", self.payload["head_labels"],
            "--entries", self.payload["entries"],
            "--paths", self.payload["paths"],
            "--global-results", self.payload["global_results"],
            "--output-dir", self.payload["output_dir"],
            "--manual-margin", "60",
            "--manual-radius", "5",
            "--display-max-dim", "1400",
        ]

    def _start_editor(self) -> None:
        if self.process.state() != QProcess.NotRunning:
            return
        self.process.setProgram(self.payload["python_executable"])
        self.process.setArguments(self._editor_arguments())
        self.process.setWorkingDirectory(str(Path(self.payload["output_dir"])))
        self.process.start()
        if self.process.waitForStarted(3000):
            analysis_process_registry.register(self.process)
        self.log_signal.emit(
            "Analysis V2：已打开尾部编辑器（视野 {}/{}：{}）。".format(
                self.index + 1, len(self.payloads), self.payload["field_id"]
            )
        )

    def _process_finished(self, exit_code, _exit_status) -> None:
        analysis_process_registry.unregister(self.process)
        if self._stopping:
            return
        if exit_code != 0:
            self.calibration_aborted.emit(
                "尾部编辑器异常退出（视野 {}，退出码 {}）。".format(
                    self.payload["field_id"], exit_code
                )
            )
            return
        try:
            payload = dict(self.payload)
            payload["task_root"] = str(self.task_root)
            result = publish_tail_final_labels(payload)
        except FileNotFoundError:
            answer = QMessageBox.warning(
                self.parent(),
                "尾部校准尚未完成",
                "请在尾部编辑器中点击保存结果。\n\n"
                "选择“重开编辑器”可继续当前视野；选择“稍后处理”将保留任务目录和编辑状态。",
                QMessageBox.Retry | QMessageBox.Cancel,
                QMessageBox.Retry,
            )
            if answer == QMessageBox.Retry:
                self._start_editor()
            else:
                self.calibration_aborted.emit("请在尾部编辑器中点击保存结果")
            return
        except ValueError as exception:
            message = str(exception)
            if "有效区域冲突" not in message:
                self.calibration_aborted.emit(message)
                return

            answer = QMessageBox.warning(
                self.parent(),
                "尾部区域存在冲突",
                "{}\n\n"
                "这表示两条已接受尾部使用了同一批尾部碎片，不能直接进入测量。\n\n"
                "选择“重开编辑器”将继续当前视野，并自动恢复编辑状态。"
                "请根据提示中的 Head 编号删除或重新绘制冲突尾部，"
                "然后再次点击“保存结果”。\n\n"
                "选择“稍后处理”会保留当前任务目录和编辑数据。".format(
                    message
                ),
                QMessageBox.Retry | QMessageBox.Cancel,
                QMessageBox.Retry,
            )
            if answer == QMessageBox.Retry:
                self._start_editor()
            else:
                self.calibration_aborted.emit(message)
            return
        except BaseException as exception:
            self.calibration_aborted.emit(str(exception))
            return

        self.results.append(result)
        self.index += 1
        if self.index < len(self.payloads):
            self._start_editor()
            return
        try:
            completed = complete_tail_calibration(self.task_root, self.results)
        except BaseException as exception:
            self.calibration_aborted.emit(str(exception))
            return
        self.calibration_completed.emit(completed)

    def _process_error(self, _error) -> None:
        if self._stopping:
            return
        self.calibration_aborted.emit(
            "尾部编辑器启动失败：{}".format(self.process.errorString())
        )

    def stop(self) -> None:
        self._stopping = True
        if self.process.state() != QProcess.NotRunning:
            pid = int(self.process.processId() or 0)
            analysis_process_registry._terminate_tree(pid, self.process)
            self.process.waitForFinished(3000)
        analysis_process_registry.unregister(self.process)
