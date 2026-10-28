"""Check extension modules/assets and exercise a clean installed wheel."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
wheel=Path(sys.argv[1]).resolve()
with zipfile.ZipFile(wheel) as package:
    count=0
    for name in package.namelist():
        if not name.startswith('ltx_prompt_director/') or name.endswith('/'):continue
        assert not name.endswith('.pyc'),name
        source=ROOT/name
        assert source.is_file() and source.read_bytes()==package.read(name),name
        count+=1
    for name in ('video_extensions.py','render_cache.py','extension_timeline.py','collection_combine.py','timeline_preview.py','render_groups.py'):
        assert 'ltx_prompt_director/'+name in package.namelist()
    assert 'ltx_prompt_director/assets/help/images/video-extensions-timeline.png' in package.namelist()
print(f'Wheel contents match {count} source/assets.',flush=True)
with tempfile.TemporaryDirectory(prefix='director-extensions-wheel-') as folder:
    root=Path(folder);target=root/'installed'
    subprocess.run([sys.executable,'-m','pip','install','--no-deps','--target',str(target),str(wheel)],check=True)
    cases=[
        ['tests/test_video_extensions.py','tests/test_grouping_metadata.py::test_grouping_normal_saved_library_projects_with_empty_string_cache','tests/test_grouping_metadata.py::test_metadata_writer_stores_empty_cache_as_object_for_archive_and_json','tests/test_grouping_metadata.py::test_optional_cache_formats_preserve_a_valid_fallback','tests/test_grouping_metadata.py::test_grouping_keeps_saved_cache_when_legacy_metadata_cache_is_blank','tests/test_render_cache.py','tests/test_collection_combine.py','tests/test_render_job_files.py','tests/test_timeline_preview.py::test_rendered_sequence_and_cached_blocks_take_priority_with_correct_audio_position','tests/test_timeline_preview.py::test_unrendered_text_image_and_trimmed_video_are_previewed_without_editor_changes','tests/test_prompt_groups.py::test_merge_three_plus_one_is_portable_editable_groups_without_rendered_movies','tests/test_prompt_groups.py::test_changed_group_render_is_not_used_for_scrubbing','tests/test_conditioning_media_playback.py::test_analysis_upload_and_storyboard_only_contain_selected_interval','tests/test_conditioning_media_playback.py::test_local_condition_frames_stop_at_trim_out_even_with_offset_and_low_source_rate'],
        ['tests/test_video_extension_ui.py::test_single_mix_box_has_only_edge_length_controls_and_stays_centered'],
        ['tests/test_video_extension_ui.py::test_original_single_video_is_reused_when_extension_is_added'],
        ['tests/test_video_extension_ui.py::test_edit_next_group_reuses_completed_prefix_and_blend_only_reuses_both'],
        ['tests/test_video_extension_ui.py::test_repeated_generate_and_switch_restores_inactive_latent_then_final'],
        ['tests/test_timeline_preview.py::test_ruler_gesture_selects_conditioning_preview_and_keeps_editor_selection'],
        ['tests/test_timeline_preview.py::test_obsolete_preview_is_discarded_and_new_project_request_is_not_lost'],
        ['tests/test_timeline_preview.py::test_cancelled_preview_cannot_reopen_a_panel_in_the_same_project'],
        ['tests/test_timeline_preview.py::test_editing_a_prompt_discards_an_older_pending_preview'],
        ['tests/test_timeline_preview.py::test_long_conditioning_text_stays_inside_resizable_preview'],
        ['tests/test_timeline_preview.py::test_continuous_scrubbing_is_throttled_instead_of_waiting_for_drag_to_stop'],
        ['tests/test_timeline_preview.py::test_real_rendered_clip_loads_at_scrub_position_with_audio_stream'],
        ['tests/test_grouping_metadata.py::test_collection_group_action_opens_saved_video_groups_without_changing_sources'],
        ['tests/test_prompt_groups.py::test_three_plus_one_keeps_shared_bottom_controls_and_original_prompts'],
        ['tests/test_prompt_groups.py::test_group_workflow_and_references_restore_in_existing_ui'],
        ['tests/test_prompt_groups.py::test_each_generation_request_uses_its_group_unified_prompt'],
        ['tests/test_prompt_groups.py::test_blend_matches_ruler_and_keeps_ten_pixel_padding'],
        ['tests/test_prompt_groups.py::test_merging_preserves_reuse_of_the_completed_first_group'],
        ['tests/test_conditioning_media_playback.py::test_real_audio_scrub_decodes_at_source_trim_and_playback_advances'],
        ['tests/test_conditioning_media_playback.py::test_conditioning_audio_panel_seeks_and_plays_unrendered_preview'],
        ['tests/test_conditioning_media_playback.py::test_conditioning_audio_transport_follows_rendered_preview_and_restores_mute'],
    ]
    if '--grouping' in sys.argv[2:]:
        cases=[
            ['tests/test_grouping_metadata.py::test_grouping_normal_saved_library_projects_with_empty_string_cache',
             'tests/test_grouping_metadata.py::test_metadata_writer_stores_empty_cache_as_object_for_archive_and_json',
             'tests/test_grouping_metadata.py::test_optional_cache_formats_preserve_a_valid_fallback',
             'tests/test_grouping_metadata.py::test_grouping_keeps_saved_cache_when_legacy_metadata_cache_is_blank',
             'tests/test_collection_combine.py','tests/test_render_cache.py'],
            ['tests/test_grouping_metadata.py::test_collection_group_action_opens_saved_video_groups_without_changing_sources'],
            ['tests/test_prompt_groups.py::test_merging_preserves_reuse_of_the_completed_first_group'],
            ['tests/test_prompt_groups.py::test_three_plus_one_keeps_shared_bottom_controls_and_original_prompts'],
        ]
    if '--refinement' in sys.argv[2:]:
        cases=[
            ['tests/test_minimax_h3.py', 'tests/test_minimax_structured_response.py'],
            ['tests/test_minimax_editor.py::UnifiedEditorTests::test_refine_after_deleting_dialogue_and_reference_uses_current_immutable_snapshot'],
            ['tests/test_minimax_editor.py::UnifiedEditorTests::test_refinement_completion_keeps_newer_prompt_or_sound_controls'],
            ['tests/test_minimax_editor.py::UnifiedEditorTests::test_refine_uses_edited_prompt_and_notes_for_selected_mode'],
            ['tests/test_minimax_editor.py::UnifiedEditorTests::test_reference_changes_preserve_draft_during_generate_and_refine'],
        ]
    if '--scope-export' in sys.argv[2:]:
        cases=[
            ['tests/test_comfy_minimax_export.py'],
            ['tests/test_minimax_editor.py::UnifiedEditorTests::test_timeline_click_keeps_chosen_prompt_scope_including_reselected_card'],
            ['tests/test_minimax_editor.py::UnifiedEditorTests::test_comfy_export_video_combine_uses_project_name'],
            ['tests/test_prompt_groups.py::test_three_plus_one_keeps_shared_bottom_controls_and_original_prompts'],
        ]
    for index,case in enumerate(cases):
        selections=[str(ROOT/value) for value in case]
        env={**os.environ,'PYTHONPATH':str(target),'QT_QPA_PLATFORM':'offscreen','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1',
             **{f'XDG_{key}_HOME':str(root/f'{index}-{key.lower()}') for key in ('CONFIG','DATA','CACHE')}}
        code=('import requests,os,sys;from pathlib import Path;import ltx_prompt_director;'
              'assert Path(ltx_prompt_director.__file__).resolve().is_relative_to(Path(os.environ["PYTHONPATH"]));'
              'import pytest;rc=pytest.main('+repr(['-q',*selections])+');'
              'sys.stdout.flush();sys.stderr.flush();os._exit(rc)')
        print('Installed extension regression:',', '.join(case),flush=True)
        subprocess.run([sys.executable,'-X','faulthandler','-c',code],cwd=root,env=env,check=True,timeout=int(os.environ.get('DIRECTOR_WHEEL_TIMEOUT','180')))
print('Installed extension wheel verified.',flush=True)
