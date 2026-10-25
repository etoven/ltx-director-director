"""Run native extension/preview regressions in isolated Qt processes."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CASES=[
    'tests/test_grouping_metadata.py::test_collection_group_action_opens_saved_video_groups_without_changing_sources',
    'tests/test_prompt_groups.py::test_three_plus_one_keeps_shared_bottom_controls_and_original_prompts',
    'tests/test_prompt_groups.py::test_group_workflow_and_references_restore_in_existing_ui',
    'tests/test_prompt_groups.py::test_each_generation_request_uses_its_group_unified_prompt',
    'tests/test_prompt_groups.py::test_blend_matches_ruler_and_keeps_ten_pixel_padding',
    'tests/test_prompt_groups.py::test_merging_preserves_reuse_of_the_completed_first_group',
    'tests/test_conditioning_media_playback.py::test_real_audio_scrub_decodes_at_source_trim_and_playback_advances',
    'tests/test_conditioning_media_playback.py::test_conditioning_audio_panel_seeks_and_plays_unrendered_preview',
    'tests/test_conditioning_media_playback.py::test_conditioning_audio_transport_follows_rendered_preview_and_restores_mute',
    'tests/test_video_extension_ui.py::test_brackets_use_existing_length_control_and_save_source_prompts',
    'tests/test_video_extension_ui.py::test_repeated_generate_and_switch_restores_inactive_latent_then_final',
    'tests/test_video_extension_ui.py::test_edit_next_group_reuses_completed_prefix_and_blend_only_reuses_both',
    'tests/test_video_extension_ui.py::test_single_mix_box_has_only_edge_length_controls_and_stays_centered',
    'tests/test_video_extension_ui.py::test_original_single_video_is_reused_when_extension_is_added',
    'tests/test_video_extension_ui.py::test_failed_block_prevents_continuations_and_blend',
    'tests/test_video_extension_ui.py::test_grouped_generation_uses_marked_durations_and_previous_tail',
    'tests/test_video_extension_ui.py::test_ctrl_selection_and_context_group_follow_visual_order',
    'tests/test_timeline_preview.py::test_ruler_gesture_selects_conditioning_preview_and_keeps_editor_selection',
    'tests/test_timeline_preview.py::test_obsolete_preview_is_discarded_and_new_project_request_is_not_lost',
    'tests/test_timeline_preview.py::test_rendered_seek_auditions_audio_and_condition_video_seeks_silently',
    'tests/test_timeline_preview.py::test_cancelled_preview_cannot_reopen_a_panel_in_the_same_project',
    'tests/test_timeline_preview.py::test_editing_a_prompt_discards_an_older_pending_preview',
    'tests/test_timeline_preview.py::test_long_conditioning_text_stays_inside_resizable_preview',
    'tests/test_timeline_preview.py::test_continuous_scrubbing_is_throttled_instead_of_waiting_for_drag_to_stop',
    'tests/test_timeline_preview.py::test_real_rendered_clip_loads_at_scrub_position_with_audio_stream',
    'tests/test_local_generation_ui.py::test_native_video_uses_unified_output_reference_panel_and_settings_directly',
    'tests/test_local_generation_ui.py::test_generated_video_attachment_updates_origin_and_preserves_previous_render',
    'tests/test_local_generation_ui.py::test_native_video_excludes_saved_references_in_conditioning_only_workspaces',
    'tests/test_workspace_chrome.py::test_queue_source_labels_stay_in_viewport_at_narrow_width_and_large_text',
    'tests/test_shot_sequence.py::test_workspace_panel_memory_player_and_portable_save',
    'tests/test_shot_sequence.py::test_empty_collection_tile_and_full_clip_recovery',
]
for index,case in enumerate(CASES):
    with tempfile.TemporaryDirectory(prefix='director-qt-') as folder:
        env={**os.environ,'QT_QPA_PLATFORM':'offscreen','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1',
             **{f'XDG_{key}_HOME':str(Path(folder)/key.lower()) for key in ('CONFIG','DATA','CACHE')}}
        code=('import requests,os,sys,pytest;rc=pytest.main('+repr(['-q',case])+');'
              'sys.stdout.flush();sys.stderr.flush();os._exit(rc)')
        print(f'Native regression {index+1}/{len(CASES)}: {case}',flush=True)
        result=subprocess.run([sys.executable,'-X','faulthandler','-c',code],cwd=ROOT,env=env,timeout=180)
        if result.returncode:raise SystemExit(result.returncode)
print(f'All {len(CASES)} isolated native regressions passed.',flush=True)
