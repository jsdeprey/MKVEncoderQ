import tempfile
import threading
import unittest
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_optimizer.combined_gui import CleanCoordinator, CombinedApp
from video_optimizer.config import Config
from video_optimizer.gui import (ENCODER_DISPLAY_COLUMNS, OptimizerGUI,
                                 _backup_path, _clean_all_text,
                                 _clean_after_encode_requested, _clean_text,
                                 _encoder_play_path,
                                 _restore_backup_transaction)
from mkvcleaner_gui import (CleanerGUI, _cleaner_play_path,
                            plan_filename_replacements)


class CombinedAppTests(unittest.TestCase):
    def test_cleaner_tab_has_compact_log_below_worker_status(self):
        source = inspect.getsource(CleanerGUI._build)
        self.assertLess(source.index('"Worker Status"'),
                        source.index('"Activity Log"'))
        self.assertIn('height=7, wrap="word", state="disabled"', source)
        self.assertIn('show="headings", height=8', source)
        self.assertIn('show="headings", height=3', source)
        helper = inspect.getsource(CleanerGUI._append_activity_log)
        self.assertIn('log.configure(state="normal")', helper)
        self.assertIn('log.configure(state="disabled")', helper)

    def test_background_inventory_keeps_all_tabs_accessible(self):
        cleaner = "cleaner-tab"
        rename = "rename-tab"
        notebook = Mock()
        notebook.select.return_value = cleaner
        app = SimpleNamespace(
            notebook=notebook, cleaner_tab=cleaner, rename_tab=rename)

        CombinedApp.set_inventory_tabs_locked(app, True)

        notebook.select.assert_not_called()
        notebook.tab.assert_any_call(cleaner, state="normal")
        notebook.tab.assert_any_call(rename, state="normal")

        notebook.reset_mock()
        CombinedApp.set_inventory_tabs_locked(app, False)
        notebook.select.assert_not_called()
        notebook.tab.assert_any_call(cleaner, state="normal")
        notebook.tab.assert_any_call(rename, state="normal")

    def test_backup_status_checks_disk_and_remembers_gui_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Movie.mkv"
            source.touch()
            config = Config(backup_originals=True)
            backup = _backup_path(source, config.backup_suffix)
            backup.touch()
            gui = SimpleNamespace(run_config=config, deleted_backups=set())
            self.assertEqual(
                OptimizerGUI._backup_status(gui, source, "Completed"),
                "[ Present ]")
            backup.unlink()
            gui.deleted_backups.add(source.resolve())
            self.assertEqual(
                OptimizerGUI._backup_status(gui, source, "Completed"),
                "Deleted")
            gui.deleted_backups.clear()
            self.assertEqual(
                OptimizerGUI._backup_status(gui, source, "Reverted"),
                "Restored")

    def test_waiting_backup_status_does_not_touch_nas(self):
        source = Path("//nas/share/Movie.mkv")
        gui = SimpleNamespace(
            run_config=Config(backup_originals=True), deleted_backups=set(),
            live=SimpleNamespace(items={source: object()}))
        with patch.object(Path, "is_file",
                          side_effect=AssertionError("waiting row touched NAS")):
            self.assertEqual(
                OptimizerGUI._backup_status(gui, source, "Waiting"), "—")

    def test_skipped_file_reports_backup_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Movie.mkv"
            source.touch()
            gui = SimpleNamespace(
                run_config=Config(backup_originals=True), deleted_backups=set())
            self.assertEqual(
                OptimizerGUI._backup_status(gui, source, "Skipped"),
                "Not created",
            )

    def test_rename_busy_reuses_known_encoder_path_without_resolving(self):
        path = Path("//nas/share/Movie.mkv")
        item = SimpleNamespace(path=path, status="Waiting")
        app = SimpleNamespace(
            encoder=SimpleNamespace(
                live=SimpleNamespace(snapshot=lambda: [item]),
                completed_outputs={}),
            cleaner=SimpleNamespace(row_ids={}, queued_row_ids={},
                                    file_tree=Mock()))
        with patch.object(
                Path, "resolve",
                side_effect=AssertionError("known NAS path was resolved")):
            self.assertEqual(CombinedApp.rename_busy(app, path), (False, ""))

    def test_rename_busy_blocks_only_the_file_currently_being_inspected(self):
        active = Path("//nas/share/Active.mkv")
        queued = Path("//nas/share/Queued.mkv")
        app = SimpleNamespace(
            encoder=SimpleNamespace(
                inspection_lock=threading.Lock(),
                inspection_active_path=active,
                live=None, completed_outputs={}),
            cleaner=SimpleNamespace(row_ids={}, queued_row_ids={},
                                    file_tree=Mock()))
        self.assertEqual(
            CombinedApp.rename_busy(app, active),
            (True, "Media inspection in progress"))
        self.assertEqual(CombinedApp.rename_busy(app, queued), (False, ""))

    def test_unrelated_encoder_status_does_not_resolve_nas_path(self):
        coordinator = CleanCoordinator()
        path = Path("//nas/share/Movie.mkv")
        with patch.object(
                Path, "resolve",
                side_effect=AssertionError("unrelated NAS path was resolved")):
            coordinator.status_changed(path, "Waiting")

    def test_send_to_rename_switches_and_defers_trusted_handoff(self):
        path = Path("//nas/share/Movie.mkv")
        callbacks = []
        app = SimpleNamespace(
            encoder=SimpleNamespace(set_background_ui=Mock()),
            notebook=SimpleNamespace(select=Mock()), rename_tab="rename",
            renamer=SimpleNamespace(add_paths=Mock()),
            after_idle=lambda callback: callbacks.append(callback))
        CombinedApp.send_to_rename(app, [path])
        app.encoder.set_background_ui.assert_called_once_with(True)
        app.notebook.select.assert_called_once_with("rename")
        app.renamer.add_paths.assert_not_called()
        callbacks[0]()
        app.renamer.add_paths.assert_called_once_with([path], trusted=True)

    def test_cleaner_release_restores_inspected_ready_detail(self):
        path = Path("Movie.mkv")
        item = SimpleNamespace(status="Waiting", detail="Ready")
        table = Mock()
        table.exists.return_value = True
        table.set.return_value = "4.2 GiB"
        encoder = SimpleNamespace(
            live=SimpleNamespace(
                release_cleaning=Mock(return_value=[path]),
                items={path: item},
            ),
            inspection_results={path: object()},
            table=table,
            _emit=Mock(),
        )
        app = SimpleNamespace(
            encoder=encoder,
            cleaner_reserved_paths={path},
            after=Mock(),
            _dispatch_ready_clean=Mock(),
        )
        CombinedApp.cleaner_finished(app, [])
        encoder._emit.assert_called_once_with(
            "row", path, "Waiting", "—", "—", "4.2 GiB", "—",
            "Media inspected — Ready",
        )
        self.assertFalse(app.cleaner_reserved_paths)

    def test_completed_context_menu_offers_single_file_backup_revert(self):
        source = inspect.getsource(OptimizerGUI._open_file_menu)
        self.assertIn('label="Revert to Backup..."', source)
        self.assertIn('command=lambda: self._revert_to_backup(path)', source)

    def test_revert_same_name_mkv_restores_backup_and_removes_encode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Movie.mkv"
            backup = root / "Movie.mkv.bak"
            source.write_bytes(b"encoded")
            backup.write_bytes(b"original")
            _restore_backup_transaction(source, source, backup)
            self.assertEqual(source.read_bytes(), b"original")
            self.assertFalse(backup.exists())
            self.assertFalse(any(root.glob("mkvencoderq-revert-*.tmp")))

    def test_revert_non_mkv_restores_source_and_removes_mkv_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Movie.mp4"
            output = root / "Movie.mkv"
            backup = root / "Movie.mp4.bak"
            output.write_bytes(b"encoded")
            backup.write_bytes(b"original")
            _restore_backup_transaction(source, output, backup)
            self.assertEqual(source.read_bytes(), b"original")
            self.assertFalse(output.exists())
            self.assertFalse(backup.exists())

    def test_revert_refuses_to_overwrite_existing_original_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Movie.mp4"
            output = root / "Movie.mkv"
            backup = root / "Movie.mp4.bak"
            source.write_bytes(b"unexpected")
            output.write_bytes(b"encoded")
            backup.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                _restore_backup_transaction(source, output, backup)
            self.assertEqual(source.read_bytes(), b"unexpected")
            self.assertEqual(output.read_bytes(), b"encoded")
            self.assertEqual(backup.read_bytes(), b"original")

    def test_remove_missing_files_removes_only_missing_rows(self):
        table = Mock()
        table.get_children.return_value = ("waiting", "missing", "completed")
        table.set.side_effect = lambda row, _column: {
            "waiting": "Waiting", "missing": "Missing",
            "completed": "Completed",
        }[row]
        gui = SimpleNamespace(
            table=table, _remove_rows_from_list=Mock(),
            _refresh_missing_button=Mock())
        OptimizerGUI.remove_missing_files(gui)
        gui._remove_rows_from_list.assert_called_once_with(["missing"])
        gui._refresh_missing_button.assert_called_once_with()

    def test_clean_selected_button_is_removed_from_encoder_toolbar(self):
        source = inspect.getsource(OptimizerGUI._build)
        self.assertNotIn('text="Clean Selected"', source)
        self.assertIn('text="Remove Missing Files"', source)

    def test_completed_encoder_row_plays_verified_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = (Path(directory) / "movie.mp4").resolve()
            output = (Path(directory) / "movie.mkv").resolve()
            source.write_bytes(b"source")
            output.write_bytes(b"output")
            self.assertEqual(
                _encoder_play_path(source, "Completed", {source: output}),
                output,
            )

    def test_encoder_active_or_missing_row_cannot_be_played(self):
        with tempfile.TemporaryDirectory() as directory:
            source = (Path(directory) / "movie.mkv").resolve()
            source.write_bytes(b"video")
            for status in ("Sampling", "Encoding", "Paused", "Missing"):
                self.assertIsNone(_encoder_play_path(source, status, {}))

    def test_encoder_safe_unfinished_row_plays_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = (Path(directory) / "movie.mkv").resolve()
            source.write_bytes(b"video")
            self.assertEqual(_encoder_play_path(source, "Skipped", {}), source)

    def test_cleaner_play_is_disabled_only_for_active_file_states(self):
        with tempfile.TemporaryDirectory() as directory:
            movie = (Path(directory) / "movie.mkv").resolve()
            movie.write_bytes(b"video")
            self.assertIsNone(_cleaner_play_path(movie, "Remuxing"))
            self.assertEqual(_cleaner_play_path(movie, "Done"), movie)

    def test_both_context_menus_offer_play_video(self):
        self.assertIn('label="Play Video"',
                      inspect.getsource(OptimizerGUI._open_file_menu))
        self.assertIn('label="Play Video"',
                      inspect.getsource(CleanerGUI._open_file_menu))

    def test_cleaner_batch_rename_can_remove_common_literal_pattern(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "Show.1x2x3x.Part1.mkv",
                     root / "Show.1x2x3x.Part2.mkv"]
            for path in paths:
                path.write_bytes(b"mkv")
            plan = plan_filename_replacements(paths, "1x2x3x.", "")
            self.assertEqual(
                [destination.name for destination in plan.values()],
                ["Show.Part1.mkv", "Show.Part2.mkv"],
            )

    def test_cleaner_batch_rename_rejects_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Show.old.mkv"
            destination = root / "Show.mkv"
            source.write_bytes(b"source")
            destination.write_bytes(b"existing")
            with self.assertRaisesRegex(ValueError, "already exists"):
                plan_filename_replacements([source], ".old", "")

    def test_cleaner_rename_guard_blocks_encoder_tracked_file(self):
        with tempfile.TemporaryDirectory() as directory:
            movie = (Path(directory) / "movie.mkv").resolve()
            live = SimpleNamespace(items={movie: object()}, completed=set())
            app = SimpleNamespace(encoder=SimpleNamespace(
                live=live, completed_outputs={}))
            allowed, reason = CombinedApp.cleaner_rename_guard(app, [movie])
            self.assertFalse(allowed)
            self.assertIn("tracked by Encoder", reason)

    def test_cleaner_rename_guard_allows_untracked_file(self):
        with tempfile.TemporaryDirectory() as directory:
            movie = (Path(directory) / "movie.mkv").resolve()
            app = SimpleNamespace(encoder=SimpleNamespace(
                live=None, completed_outputs={}))
            self.assertEqual(
                CombinedApp.cleaner_rename_guard(app, [movie]), (True, ""))

    def test_clean_request_can_be_removed_from_pending_and_visible_state(self):
        with tempfile.TemporaryDirectory() as directory:
            source = (Path(directory) / "source.mp4").resolve()
            output = source.with_suffix(".mkv")
            coordinator = CleanCoordinator()
            coordinator.request(source, "Waiting", True, planned_output=output)
            coordinator.cancel(output)
            self.assertEqual(coordinator.visible_snapshot(), {})
            self.assertNotIn(source, coordinator.pending)

    def test_cleaner_file_table_has_context_menu_bindings(self):
        source = inspect.getsource(CleanerGUI._build)
        self.assertIn('self.file_tree.bind("<Button-3>"', source)
        self.assertIn('self.file_tree.bind("<Shift-F10>"', source)

    def test_cleaner_has_granular_track_name_options_on_main_page(self):
        source = inspect.getsource(CleanerGUI._build)
        self.assertIn('text="Remove track names:"', source)
        self.assertIn('(\"Video\", self.remove_video_names_var', source)
        self.assertIn('(\"Audio\", self.remove_audio_names_var', source)
        self.assertIn('(\"Subtitles\", self.remove_subtitle_names_var', source)
        self.assertIn("This run only; defaults are set in Cleaner Settings", source)

    def test_cleaner_has_run_options_to_clear_each_subtitle_flag(self):
        build_source = inspect.getsource(CleanerGUI._build)
        options_source = inspect.getsource(CleanerGUI._config_with_run_options)
        self.assertIn('text="Clear subtitle flags:"', build_source)
        self.assertIn('text="Default"', build_source)
        self.assertIn('text="Forced"', build_source)
        self.assertIn("clear_all_subtitle_default_flags", options_source)
        self.assertIn("clear_all_subtitle_forced_flags", options_source)

    def test_cleaner_main_track_name_options_are_run_only(self):
        option_source = inspect.getsource(CleanerGUI._config_with_run_options)
        click_source = inspect.getsource(CleanerGUI._set_track_name_option)
        finish_source = inspect.getsource(CleanerGUI._finish_controls)
        self.assertIn("replace(", option_source)
        self.assertNotIn("save_config", click_source)
        self.assertIn("load_config(self.config_path)", finish_source)
        self.assertIn("_sync_track_name_options", finish_source)

    def test_clean_after_encode_toggle_is_persistent_and_defaults_on(self):
        self.assertTrue(Config().clean_new_encodes)
        build_source = inspect.getsource(OptimizerGUI._build)
        setter_source = inspect.getsource(OptimizerGUI.set_clean_new_encodes)
        self.assertIn('text="Clean After Encode"', build_source)
        self.assertIn("clean_new_encodes=enabled", setter_source)
        self.assertIn("save_config", setter_source)

    def test_automatic_clean_handoff_does_not_switch_tabs(self):
        request = Mock()
        table = Mock()
        table.exists.return_value = False
        gui = SimpleNamespace(
            clean_request_callback=request,
            clean_paths={Path("movie.mkv").resolve()},
            table=table,
            _refresh_clean_all_heading=Mock(),
        )
        source = inspect.getsource(OptimizerGUI._drain_events)
        self.assertIn('event[0] == "clean_after_encode"', source)
        self.assertIn("switch_tab=False", source)

    def test_clean_after_encode_policy_includes_already_listed_files(self):
        movie = Path("movie.mkv").resolve()
        self.assertTrue(_clean_after_encode_requested(True, movie, set()))
        self.assertTrue(_clean_after_encode_requested(False, movie, {movie}))
        self.assertFalse(_clean_after_encode_requested(False, movie, set()))

    def test_background_clean_dispatch_does_not_select_cleaner_tab(self):
        source = inspect.getsource(CombinedApp._dispatch_ready_clean)
        self.assertNotIn("notebook.select", source)

    def test_completed_output_is_started_in_cleaner_without_switching_tabs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = (Path(directory) / "source.mp4").resolve()
            output = (Path(directory) / "source.mkv").resolve()
            output.write_bytes(b"encoded")
            table = Mock()
            table.exists.return_value = False
            cleaner = SimpleNamespace(
                running=False,
                start_paths=Mock(return_value=True),
                sync_external_queue=Mock(),
            )
            app = SimpleNamespace(
                encoder=SimpleNamespace(
                    completed_outputs={source: output}, table=table, running=True),
                cleaner=cleaner,
                cleaner_tab="cleaner-tab",
                clean_coordinator=CleanCoordinator(),
                notebook=Mock(),
                _planned_output=Mock(return_value=output),
            )
            app._dispatch_ready_clean = lambda: CombinedApp._dispatch_ready_clean(app)

            CombinedApp.request_clean(app, [source], switch_tab=False)

            cleaner.start_paths.assert_called_once_with([output])
            app.notebook.select.assert_not_called()

    def test_combined_clean_request_can_leave_encoder_tab_selected(self):
        source = inspect.getsource(CombinedApp.request_clean)
        self.assertIn("switch_tab: bool = True", source)
        self.assertIn("if switch_tab:", source)

    def test_clean_again_captures_visible_run_options_before_starting(self):
        path = Path("Movie.mkv")
        saved = object()
        current = object()
        gui = SimpleNamespace(
            config_path=Path("cleaner_config.json"),
            _config_with_run_options=Mock(return_value=current),
            start_paths=Mock(return_value=True),
        )
        with patch("mkvcleaner_gui.load_config", return_value=saved):
            started = CleanerGUI._clean_again(gui, path)

        self.assertTrue(started)
        gui._config_with_run_options.assert_called_once_with(saved)
        gui.start_paths.assert_called_once_with([path], run_config=current)

    def test_clean_again_menu_uses_dedicated_fresh_run_action(self):
        source = inspect.getsource(CleanerGUI._open_file_menu)
        self.assertIn("self._clean_again(path)", source)

    def test_cleaner_remove_all_is_safe_and_disabled_while_running(self):
        build_source = inspect.getsource(CleanerGUI._build)
        remove_source = inspect.getsource(CleanerGUI._remove_all)
        begin_source = inspect.getsource(CleanerGUI._begin_run)
        finish_source = inspect.getsource(CleanerGUI._finish_controls)
        self.assertIn('text="Remove All"', build_source)
        self.assertIn("No video files will be deleted or changed", remove_source)
        self.assertIn("self._remove_paths(paths)", remove_source)
        self.assertIn('remove_all_button.configure(state="disabled")', begin_source)
        self.assertIn('remove_all_button.configure(state="normal")', finish_source)

    def test_cleaner_has_clean_selected_and_clean_all_buttons(self):
        build_source = inspect.getsource(CleanerGUI._build)
        begin_source = inspect.getsource(CleanerGUI._begin_run)
        self.assertIn('text="Clean Selected"', build_source)
        self.assertIn('text="Clean All"', build_source)
        self.assertLess(build_source.index('text="Remove All"'),
                        build_source.index('text="Clean All"'))
        self.assertLess(build_source.index('text="Clean All"'),
                        build_source.index('text="Clean Selected"'))
        self.assertIn('clean_selected_button.configure(state="disabled")',
                      begin_source)
        self.assertIn('clean_all_button.configure(state="disabled")',
                      begin_source)

    def test_clean_all_uses_available_list_files_and_leaves_waiting_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = (root / "One.mkv").resolve()
            second = (root / "Two.mkv").resolve()
            waiting = (root / "Waiting.mkv").resolve()
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            gui = SimpleNamespace(
                row_ids={first: "one"},
                queued_row_ids={second: "two", waiting: "waiting"},
                start_paths=Mock(return_value=True),
            )
            with patch("mkvcleaner_gui.messagebox.askyesno", return_value=True):
                CleanerGUI._clean_all_list(gui)

            gui.start_paths.assert_called_once_with([first, second])

    def test_backup_path_appends_suffix_to_complete_source_name(self):
        self.assertEqual(
            _backup_path(Path("Movie.mp4"), ".bak"),
            Path("Movie.mp4.bak"),
        )

    def test_delete_backup_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "Movie.mkv.bak"
            backup.write_bytes(b"original")
            gui = SimpleNamespace(_log=Mock())
            with patch("video_optimizer.gui.messagebox.askyesno",
                       return_value=False) as confirm:
                OptimizerGUI._delete_backup(gui, backup)
            confirm.assert_called_once()
            self.assertTrue(backup.exists())
            gui._log.assert_not_called()

    def test_confirmed_backup_delete_removes_only_selected_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "Movie.mkv.bak"
            neighbor = root / "Other.mkv.bak"
            backup.write_bytes(b"original")
            neighbor.write_bytes(b"other")
            gui = SimpleNamespace(_log=Mock())
            with patch("video_optimizer.gui.messagebox.askyesno",
                       return_value=True):
                OptimizerGUI._delete_backup(gui, backup)
            self.assertFalse(backup.exists())
            self.assertTrue(neighbor.exists())
            gui._log.assert_called_once()

    def test_file_table_has_right_click_and_keyboard_context_menus(self):
        source = inspect.getsource(OptimizerGUI._build)
        self.assertIn('self.table.bind("<Button-3>"', source)
        self.assertIn('self.table.bind("<Shift-F10>"', source)

    def test_inspection_progress_uses_compact_distinct_style(self):
        build_source = inspect.getsource(OptimizerGUI._build)
        theme_source = inspect.getsource(OptimizerGUI.apply_theme)
        self.assertIn('length=220', build_source)
        self.assertIn('style="Inspection.Horizontal.TProgressbar"', build_source)
        self.assertIn('"Inspection.Horizontal.TProgressbar"', theme_source)

    def test_filters_explain_when_they_are_locked(self):
        build_source = inspect.getsource(OptimizerGUI._build)
        lock_source = inspect.getsource(OptimizerGUI._set_filter_lock_state)
        self.assertIn("Filters available before Start", build_source)
        self.assertIn("Filters locked during processing", lock_source)
        self.assertIn('state="disabled" if locked else "readonly"', lock_source)

    def test_clean_checkbox_and_heading_states(self):
        self.assertEqual(_clean_text(False), "☐ Clean")
        self.assertEqual(_clean_text(True), "☑ Clean")
        self.assertEqual(_clean_text(False, available=False), "— Unavailable")
        self.assertEqual(_clean_all_text([False, False]), "☐ Clean All")
        self.assertEqual(_clean_all_text([True, False]), "◩ Clean All")
        self.assertEqual(_clean_all_text([True, True]), "☑ Clean All")
        self.assertEqual(
            _clean_all_text([], available=False), "— Clean All")

    def test_clean_column_is_hidden_from_encoder_inventory(self):
        self.assertNotIn("clean", ENCODER_DISPLAY_COLUMNS)
        self.assertIn("media", ENCODER_DISPLAY_COLUMNS)
        self.assertIn("rating", ENCODER_DISPLAY_COLUMNS)

    def test_backup_replaces_visible_row_progress_column(self):
        source = Path(__file__).parents[1] / "video_optimizer" / "gui.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("progress", ENCODER_DISPLAY_COLUMNS)
        self.assertLess(ENCODER_DISPLAY_COLUMNS.index("action"),
                        ENCODER_DISPLAY_COLUMNS.index("backup"))
        self.assertIn('(\"backup\", \"Backup\", 90)', text)

    def test_encoder_size_headers_are_compact(self):
        source = Path(__file__).parents[1] / "video_optimizer" / "gui.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('(\"savings\", \"Savings\", 72)', text)
        self.assertIn('(\"current_size\", \"Source size\", 84)', text)
        self.assertIn('(\"projected\", \"Est. output\", 84)', text)

    def test_missing_mkvtoolnix_disables_only_cleaner_features(self):
        root = Path(__file__).parents[1]
        cleaner = (root / "mkvcleaner_gui.py").read_text(encoding="utf-8")
        combined = (root / "video_optimizer" / "combined_gui.py").read_text(
            encoding="utf-8")
        encoder = (root / "video_optimizer" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("MKVToolNix not found — Cleaner features are disabled", cleaner)
        self.assertIn('self.start_button.configure(state="normal" if available else "disabled")', cleaner)
        self.assertIn("tool_availability_callback=self.cleaner_tool_availability_changed", combined)
        self.assertIn("self.encoder.set_clean_available(available)", combined)
        self.assertIn("self.clean_selected_button.configure", encoder)

    def test_active_encode_defers_cleaning_until_output_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "source.mkv"
            source.write_bytes(b"source")
            output.write_bytes(b"encoded")
            coordinator = CleanCoordinator()
            coordinator.request(source, "Encoding", True)
            self.assertEqual(coordinator.drain(), [])
            self.assertIn(source.resolve(), coordinator.pending)
            coordinator.output_ready(source, output)
            self.assertEqual(coordinator.drain(), [output.resolve()])

    def test_cleaner_poll_recovers_a_missed_completed_output_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "source.mkv"
            source.write_bytes(b"source")
            output.write_bytes(b"encoded")
            coordinator = CleanCoordinator()
            coordinator.request(source, "Encoding", True, planned_output=output)

            coordinator.reconcile_completed({source.resolve(): output.resolve()})

            self.assertEqual(coordinator.drain(), [output.resolve()])
            self.assertNotIn(source.resolve(), coordinator.pending)
            self.assertEqual(
                coordinator.visible_snapshot()[output.resolve()],
                ("Queued", "Waiting for Cleaner"),
            )

    def test_ready_clean_poll_reconciles_before_dispatch(self):
        app = SimpleNamespace(
            clean_coordinator=Mock(),
            encoder=SimpleNamespace(completed_outputs={}),
            cleaner=Mock(),
            _dispatch_ready_clean=Mock(),
            _poll_ready_clean=Mock(),
            after=Mock(),
        )
        CombinedApp._poll_ready_clean(app)
        app.clean_coordinator.reconcile_completed.assert_called_once_with({})
        app.cleaner.sync_external_queue.assert_called_once()
        app._dispatch_ready_clean.assert_called_once()

    def test_non_mkv_clean_request_before_start_waits_for_output_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "different source name.mp4"
            output = root / "encoded output name.mkv"
            source.write_bytes(b"source")
            coordinator = CleanCoordinator()
            coordinator.request(source, "Waiting", False, planned_output=output)
            self.assertEqual(coordinator.drain(), [])
            self.assertIn(source.resolve(), coordinator.pending)
            self.assertEqual(
                coordinator.visible_snapshot()[output.resolve()],
                ("Waiting for Encode",
                 "Waiting for different source name.mp4 to finish encoding"),
            )
            output.write_bytes(b"encoded")
            coordinator.output_ready(source, output)
            self.assertEqual(coordinator.drain(), [output.resolve()])
            self.assertNotIn(source.resolve(), coordinator.pending)
            self.assertEqual(
                coordinator.visible_snapshot()[output.resolve()],
                ("Queued", "Waiting for Cleaner"),
            )

    def test_clean_now_reserves_waiting_encoder_file_without_cancelling_handoff(self):
        path = Path("//nas/share/movie.mkv")
        live = Mock()
        live.reserve_for_cleaning.return_value = (True, [path], "")
        live.items = {path: SimpleNamespace(status="Waiting")}
        encoder = SimpleNamespace(
            live=live, table=Mock(), _emit=Mock())
        encoder.table.exists.return_value = True
        encoder.table.set.return_value = "4.0 GiB"
        app = SimpleNamespace(
            encoder=encoder, cleaner_reserved_paths=set())

        self.assertEqual(
            CombinedApp.cleaner_start_guard(app, [path]), (True, ""))

        self.assertEqual(app.cleaner_reserved_paths, {path})
        encoder._emit.assert_called_once()

    def test_manual_clean_does_not_satisfy_clean_after_encode_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = (Path(directory) / "movie.mkv").resolve()
            path.write_bytes(b"original")
            coordinator = CleanCoordinator()
            coordinator.request(path, "Waiting", True, planned_output=path)

            # Cleaner may clean the original immediately, but that operation
            # must not consume the distinct request for the encoded replacement.
            self.assertIn(path, coordinator.pending)
            self.assertEqual(coordinator.drain(), [])

            path.write_bytes(b"encoded replacement")
            coordinator.reconcile_completed({path: path})

            self.assertEqual(coordinator.drain(), [path])
            self.assertNotIn(path, coordinator.pending)

    def test_clean_now_rejects_file_already_encoding(self):
        live = Mock()
        live.reserve_for_cleaning.return_value = (
            False, [], "movie.mkv is currently encoding")
        app = SimpleNamespace(
            encoder=SimpleNamespace(live=live), cleaner_reserved_paths=set())

        allowed, reason = CombinedApp.cleaner_start_guard(
            app, [Path("movie.mkv")])

        self.assertFalse(allowed)
        self.assertIn("Wait for Encoder", reason)

    def test_non_mkv_clean_request_is_cleared_when_encode_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.avi"
            output = source.with_suffix(".mkv")
            source.write_bytes(b"source")
            coordinator = CleanCoordinator()
            coordinator.request(source, "Waiting", False, planned_output=output)
            coordinator.status_changed(source, "Skipped")
            self.assertEqual(coordinator.drain(), [])
            self.assertNotIn(source.resolve(), coordinator.pending)
            self.assertEqual(
                coordinator.visible_snapshot()[source.with_suffix(".mkv").resolve()],
                ("Not Cleaned", "No MKV output — encode skipped"),
            )

    def test_skipped_mkv_is_cleaned_in_place_after_encoder_passes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mkv"
            source.write_bytes(b"source")
            coordinator = CleanCoordinator()
            coordinator.request(source, "Sampling", True)
            coordinator.status_changed(source, "Skipped")
            self.assertEqual(coordinator.drain(), [source.resolve()])

    def test_ready_clean_paths_are_deduplicated_and_mkv_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            other = root / "movie.mp4"
            movie.write_bytes(b"mkv")
            other.write_bytes(b"mp4")
            coordinator = CleanCoordinator()
            coordinator.ready.extend((movie, movie, other))
            self.assertEqual(coordinator.drain(), [movie.resolve()])

    def test_ready_completed_files_can_clean_during_encoder_session(self):
        source = inspect.getsource(CombinedApp._dispatch_ready_clean)
        self.assertIn("not self.cleaner.running", source)
        self.assertNotIn("not self.encoder.running", source)
        self.assertNotIn("cleaner_safe_to_start", source)

    def test_cleaner_list_immediately_shows_external_queue(self):
        cleaner = (Path(__file__).parents[1] / "mkvcleaner_gui.py").read_text(
            encoding="utf-8")
        self.assertIn("def sync_external_queue", cleaner)
        self.assertIn('values = (\"Q\", path.name, status, detail)', cleaner)
        request = inspect.getsource(CombinedApp.request_clean)
        self.assertIn("self.cleaner.sync_external_queue", request)

    def test_encoder_and_cleaner_can_run_at_the_same_time(self):
        source = inspect.getsource(CombinedApp.__init__)
        self.assertNotIn("can_start_callback=", source)

    def test_encoder_and_cleaner_use_separate_configuration_files(self):
        source = Path(__file__).parents[1] / "mkvcleaner_gui.py"
        self.assertIn(
            'application_directory() / "cleaner_config.json"',
            source.read_text(encoding="utf-8"),
        )

    def test_encoder_active_work_ignores_idle_directory_watcher(self):
        idle = SimpleNamespace(
            encode_active=threading.Event(), pause_event=threading.Event(),
            current_cancel_event=None,
            live=SimpleNamespace(snapshot=lambda: [SimpleNamespace(status="Waiting")]),
        )
        self.assertFalse(OptimizerGUI.has_active_work(idle))
        idle.live = SimpleNamespace(
            snapshot=lambda: [SimpleNamespace(status="Sampling")])
        self.assertTrue(OptimizerGUI.has_active_work(idle))

    def test_close_stops_idle_watcher_without_requiring_stop_all(self):
        encoder = SimpleNamespace(
            running=True, stop_event=threading.Event(),
            has_active_work=lambda: False)
        app = SimpleNamespace(
            encoder=encoder, cleaner=SimpleNamespace(running=False),
            destroy=Mock())
        CombinedApp.close(app)
        self.assertTrue(encoder.stop_event.is_set())
        app.destroy.assert_called_once_with()

    def test_close_remains_blocked_during_active_work(self):
        encoder = SimpleNamespace(
            running=True, stop_event=threading.Event(),
            has_active_work=lambda: True)
        app = SimpleNamespace(
            encoder=encoder, cleaner=SimpleNamespace(running=False),
            destroy=Mock())
        with patch("video_optimizer.combined_gui.messagebox.showinfo") as notice:
            CombinedApp.close(app)
        notice.assert_called_once()
        self.assertFalse(encoder.stop_event.is_set())
        app.destroy.assert_not_called()

    def test_stop_idle_watcher_does_not_claim_an_encode_is_active(self):
        gui = SimpleNamespace(
            running=True,
            has_active_work=lambda: False,
            stop_button=Mock(),
            pause_event=threading.Event(),
            stop_event=threading.Event(),
            current_cancel_event=None,
            status_var=Mock(),
            _set_control_indicator=Mock(),
        )
        with patch("video_optimizer.gui.messagebox.askyesno") as confirm:
            OptimizerGUI.stop(gui)
        confirm.assert_not_called()
        self.assertTrue(gui.stop_event.is_set())
        gui.stop_button.configure.assert_called_once()

    def test_stop_active_encode_still_requires_confirmation(self):
        gui = SimpleNamespace(
            running=True,
            has_active_work=lambda: True,
            stop_button=Mock(),
            pause_event=threading.Event(),
            stop_event=threading.Event(),
            current_cancel_event=None,
            status_var=Mock(),
            _set_control_indicator=Mock(),
        )
        with patch("video_optimizer.gui.messagebox.askyesno", return_value=False):
            OptimizerGUI.stop(gui)
        self.assertFalse(gui.stop_event.is_set())

    def test_both_queue_tables_are_drop_targets(self):
        source = inspect.getsource(CombinedApp._enable_drop_targets)
        self.assertIn("self.encoder.table.drop_target_register", source)
        self.assertIn("self.cleaner.file_tree.drop_target_register", source)


if __name__ == "__main__":
    unittest.main()
