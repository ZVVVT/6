"""Minimal Analysis V2 entry point shared by future callers.

Non-interactive head and protein3 tail calibration reuse the formal services.
Batch integration is separate.
"""


def run_analysis_v2(
    start_analysis,
    *,
    complete_items,
    protein_key: str,
    protein_name: str,
    workflow: str = "head",
    interactive: bool = True,
):
    """Delegate once, preserving inputs, return value and exceptions."""
    return start_analysis(
        complete_items=complete_items,
        protein_key=protein_key,
        protein_name=protein_name,
        workflow=workflow,
        interactive=interactive,
    )


def complete_automatic_tail_calibration(task_root, fields):
    """Schedule verified builders, then advance the shared calibration state."""
    from pathlib import Path
    from core.analysis_v2.tail_calibration_service import (
        save_initial_c18b_tail_workset,
        build_automatic_tail_final_contract,
        register_tail_final_contract,
        complete_tail_calibration,
    )

    results = []
    for field in fields:
        payload = dict(field)
        payload["task_root"] = str(task_root)
        output_dir = Path(payload["output_dir"]).resolve()
        head_labels = Path(payload["head_labels"]).resolve()
        # save calls build_initial_c18b_tail_workset exactly once.
        save_initial_c18b_tail_workset(
            Path(payload["fragments"]).resolve().parent, head_labels, output_dir,
        )
        contract = build_automatic_tail_final_contract(
            payload["field_id"], output_dir, head_labels,
        )
        results.append(register_tail_final_contract(payload, contract))
    completed = complete_tail_calibration(task_root, results, automatic=True)
    completed.update({
        "tail_backend": "C18B",
        "workflow": "c18b_tail_editor",
        "manual_calibration_completed": False,
        "automatic_calibration_completed": True,
        "ready_for_measurement": True,
    })
    return completed
