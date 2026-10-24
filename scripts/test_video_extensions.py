"""Run native extension/preview regressions in isolated Qt processes."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CASES=[
    'tests/test_video_extension_ui.py::test_brackets_use_existing_length_control_and_save_source_prompts',
    'tests/test_video_extension_ui.py::test_repeated_generate_and_switch_restores_inactive_latent_then_final',
    'tests/test_video_extension_ui.py::test_edit_next_group_reuses_completed_prefix_and_blend_only_reuses_both',
    'tests/test_video_extension_ui.py::test_single_mix_box_has_only_edge_length_controls_and_stays_centered',
    'tests/test_video_extension_ui.py::test_original_single_video_is_reused_when_extension_is_added',
    'tests/test_video_extension_ui.py::test_failed_block_prevents_continuations_and_blend',
    'tests/test_video_extension_ui.py::test_grouped_generation_uses_marked_durations_and_previous_tail',
    'tests/test_video_extension_ui.py::test_ctrl_selection_and_context_group_follow_visual_order',
    'tests/test_local_generation_ui.py::test_native_video_uses_unified_output_reference_panel_and_settings_directly',
    'tests/test_local_generation_ui.py::test_generated_video_attachment_updates_origin_and_preserves_previous_render',
    'tests/test_local_generation_ui.py::test_native_video_excludes_saved_references_in_conditioning_only_workspaces',
    'tests/test_workspace_chrome.py::test_queue_source_labels_stay_in_viewport_at_narrow_width_and_large_text',
    'tests/test_shot_sequence.py::test_workspace_panel_memory_player_and_portable_save',
    'tests/test_shot_sequence.py::test_empty_collection_tile_and_full_clip_recovery',
]
for index,case in enumerate(CASES):
    with tempfile.TemporaryDirectory(prefix='director-qt-') as folder:
        env={**os.environ,'QT_QPA_PLATFORM':'offscreen',
             **{f'XDG_{key}_HOME':str(Path(folder)/key.lower()) for key in ('CONFIG','DATA','CACHE')}}
        code=('import os,sys,pytest;rc=pytest.main('+repr(['-q',case])+');'
              'sys.stdout.flush();sys.stderr.flush();os._exit(rc)')
        print(f'Native regression {index+1}/{len(CASES)}: {case}',flush=True)
        result=subprocess.run([sys.executable,'-X','faulthandler','-c',code],cwd=ROOT,env=env,timeout=65)
        if result.returncode:raise SystemExit(result.returncode)
print(f'All {len(CASES)} isolated native regressions passed.',flush=True)
