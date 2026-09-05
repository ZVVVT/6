"""C18B 尾部编辑器顺序监管器。"""

from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from core.analysis_process_registry import analysis_process_registry
from core.analysis_v2.tail_calibration_service import (
    complete_tail_calibration,
    publish_tail_final_labels,
)


class C18BTailCalibrationController(QObject):
    """依次运行 C18B 尾部编辑器并发布人工校准结果。"""

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
        self._terminal_signal_sent = False
        self.process = QProcess(self)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

    @property
    def payload(self):
        return self.payloads[self.index]

    def start(self) -> None:
        if not self.payloads:
            self._abort("没有可校准的 C18B 尾部视野。")
            return
        self._start_editor()

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
            "--unassigned-candidates", self.payload["unassigned_candidates"],
            "--output-dir", self.payload["output_dir"],
        ]

    def _start_editor(self) -> None:
        if self._stopping or self._terminal_signal_sent:
            return
        if self.process.state() != QProcess.NotRunning:
            return
        try:
            self.process.setProgram(self.payload["python_executable"])
            self.process.setArguments(self._editor_arguments())
            self.process.setWorkingDirectory(
                str(Path(self.payload["output_dir"]).resolve())
            )
            self.process.start()
            if self.process.waitForStarted(3000):
                analysis_process_registry.register(self.process)
            else:
                self._abort(
                    "C18B 尾部编辑器启动失败：{}".format(
                        self.process.errorString()
                    )
                )
                return
            self.log_signal.emit(
                "Analysis V2：已打开 C18B 尾部编辑器（视野 {}/{}：{}）。".format(
                    self.index + 1,
                    len(self.payloads),
                    self.payload["field_id"],
                )
            )
        except BaseException as exception:
            self._abort(str(exception))

    def _process_finished(self, exit_code, _exit_status) -> None:
        analysis_process_registry.unregister(self.process)
        if self._stopping or self._terminal_signal_sent:
            return
        if int(exit_code) != 0:
            self._abort(
                "C18B 尾部编辑器异常退出（视野 {}，退出码 {}）。".format(
                    self.payload["field_id"],
                    exit_code,
                )
            )
            return

        try:
            field_payload = dict(self.payload)
            field_payload["task_root"] = str(self.task_root)
            result = publish_tail_final_labels(field_payload)
            self.results.append(result)
            self.log_signal.emit(
                "Analysis V2：C18B 视野 {} 尾部人工校准已发布。".format(
                    self.payload["field_id"]
                )
            )

            self.index += 1
            if self.index < len(self.payloads):
                self._start_editor()
                return

            completed = complete_tail_calibration(self.task_root, self.results)
            completed.update({
                "tail_backend": "C18B",
                "workflow": "c18b_tail_editor",
                "manual_calibration_completed": True,
                "ready_for_measurement": True,
            })
        except BaseException as exception:
            self._abort(str(exception))
            return

        self._terminal_signal_sent = True
        self.calibration_completed.emit(completed)

    def _process_error(self, _error) -> None:
        if self._stopping or self._terminal_signal_sent:
            return
        self._abort(
            "C18B 尾部编辑器运行失败：{}".format(self.process.errorString())
        )

    def _abort(self, reason: str) -> None:
        if self._terminal_signal_sent:
            return
        self._terminal_signal_sent = True
        analysis_process_registry.unregister(self.process)
        self.calibration_aborted.emit(str(reason))

    def stop(self) -> None:
        self._stopping = True
        if self.process.state() != QProcess.NotRunning:
            pid = int(self.process.processId() or 0)
            analysis_process_registry._terminate_tree(pid, self.process)
            self.process.waitForFinished(3000)
        analysis_process_registry.unregister(self.process)
