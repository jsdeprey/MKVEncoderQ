import queue
import inspect
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from video_optimizer.gui import (ENCODER_DISPLAY_COLUMNS, OptimizerGUI,
                                 _display_column_number, _file_count_text,
                                 _filename_text, _inspection_summary, _priority_position,
                                 _row_tags)
from video_optimizer.queueing import FileState, LiveQueue


class DisplayTests(unittest.TestCase):
    def test_send_directory_to_cleaner_uses_exact_folder_and_logical_rows(self):
        root = Path("library").resolve()
        first = (root / "Show" / "One.mkv").resolve()
        second = (root / "Show" / "Two.mp4").resolve()
        other = (root / "Other" / "Three.mkv").resolve()
        missing = (root / "Show" / "Missing.mkv").resolve()

        class FakeTable:
            statuses = {
                str(first): "Completed", str(second): "Waiting",
                str(other): "Completed", str(missing): "Missing",
            }

            def set(self, iid, column):
                return self.statuses[iid]

        gui = SimpleNamespace(
            table=FakeTable(),
            _all_table_rows=Mock(return_value=[
                str(first), str(second), str(other), str(missing)]),
            _rename_target=Mock(side_effect=lambda path: path),
            _send_to_cleaner=Mock(),
        )
        with patch("video_optimizer.gui.messagebox.askyesno", return_value=True):
            OptimizerGUI._send_directory_to_cleaner(gui, first.parent)

        gui._send_to_cleaner.assert_called_once_with([first, second])

    def test_filename_marks_spaces_or_missing_movie_year(self):
        self.assertEqual(_filename_text(Path("Movie.Name.2024.mkv")),
                         "Movie.Name.2024.mkv")
        self.assertEqual(_filename_text(Path("Movie Name 2024.mkv")),
                         "🔸 Movie Name 2024.mkv")
        self.assertEqual(_filename_text(Path("Movie.Name.mkv")),
                         "🔸 Movie.Name.mkv")
        self.assertEqual(_filename_text(Path("Movie.1080p.mkv")),
                         "🔸 Movie.1080p.mkv")
        self.assertEqual(_filename_text(Path("Movie.1999.2160p.mkv")),
                         "Movie.1999.2160p.mkv")
        self.assertEqual(_filename_text(Path("Series.S01E01.1080p.mkv")),
                         "Series.S01E01.1080p.mkv")
        self.assertEqual(_filename_text(Path("Series.s2e7.720p.mkv")),
                         "Series.s2e7.720p.mkv")
        self.assertEqual(_filename_text(Path("Series S01E01.mkv")),
                         "🔸 Series S01E01.mkv")

    def test_filename_markers_can_be_disabled_independently(self):
        spaced = Path("Movie Name 2024.mkv")
        missing = Path("Movie.Name.mkv")
        self.assertEqual(_filename_text(spaced, False, True), spaced.name)
        self.assertEqual(_filename_text(missing, True, False), missing.name)
        self.assertEqual(_filename_text(Path("Movie Name.mkv"), False, False),
                         "Movie Name.mkv")

    def test_batch_backup_delete_updates_each_successful_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_source = root / "First.2024.mkv"
            second_source = root / "Second.2023.mkv"
            first_backup = root / "First.2024.mkv.bak"
            second_backup = root / "Second.2023.mkv.bak"
            first_backup.write_bytes(b"first")
            second_backup.write_bytes(b"second")
            gui = SimpleNamespace(
                deleted_backups=set(), _refresh_backup_cell=Mock(), _log=Mock())

            with patch("video_optimizer.gui.messagebox.askyesno", return_value=True), patch(
                    "video_optimizer.gui.messagebox.showwarning") as warning:
                OptimizerGUI._delete_backups(gui, [
                    (first_backup, first_source),
                    (second_backup, second_source),
                ])

            self.assertFalse(first_backup.exists())
            self.assertFalse(second_backup.exists())
            self.assertEqual(gui.deleted_backups, {first_source, second_source})
            self.assertEqual(gui._refresh_backup_cell.call_count, 2)
            warning.assert_not_called()

    def test_scan_now_button_precedes_changing_scan_status(self):
        source = inspect.getsource(OptimizerGUI._build)
        self.assertLess(source.index("self.scan_now_button = ttk.Button"),
                        source.index("self.scan_status_var = tk.StringVar"))

    def test_home_button_is_above_remove_missing_files(self):
        source = inspect.getsource(OptimizerGUI._build)
        self.assertLess(source.index("self.home_button = ttk.Button"),
                        source.index("self.remove_missing_button = ttk.Button"))

    def test_home_uses_configured_default_and_schedules_fresh_inventory(self):
        directory_cancel = threading.Event()
        inspection_cancel = threading.Event()
        gui = SimpleNamespace(
            running=False, start_pending=False,
            input_var=Mock(), manual_scan_requested=False,
            last_idle_scan_started=10.0,
            directory_discovery_cancel=directory_cancel,
            inspection_cancel=inspection_cancel,
            inventory_status_var=Mock(), after=Mock(),
            _finish_home_navigation=Mock(),
        )
        with patch("video_optimizer.gui.load_config",
                   return_value=SimpleNamespace(input_directory="D:/Videos")):
            OptimizerGUI.go_home(gui)

        gui.input_var.set.assert_called_once_with("D:/Videos")
        self.assertTrue(gui.manual_scan_requested)
        self.assertEqual(gui.last_idle_scan_started, 0.0)
        self.assertTrue(directory_cancel.is_set())
        self.assertTrue(inspection_cancel.is_set())
        gui.after.assert_called_once_with(100, gui._finish_home_navigation)

    def test_home_does_nothing_during_encoder_session(self):
        gui = SimpleNamespace(running=True, start_pending=False,
                              input_var=Mock())
        with patch("video_optimizer.gui.load_config") as load:
            OptimizerGUI.go_home(gui)
        load.assert_not_called()
        gui.input_var.set.assert_not_called()

    def test_active_control_uses_one_moving_acknowledgement_marker(self):
        source = inspect.getsource(OptimizerGUI._set_control_indicator)
        self.assertIn('"running": (3, "RunningIndicator.TLabel")', source)
        self.assertIn('"stopping": (4, "StoppingIndicator.TLabel")', source)
        self.assertIn('"paused": (5, "PausedIndicator.TLabel")', source)
        self.assertIn('"cancelling": (6, "CancellingIndicator.TLabel")', source)
        self.assertIn("grid_remove", source)

    def test_file_counter_width_is_stable_during_scanning_message(self):
        source = (Path(__file__).parents[1] / "video_optimizer" / "gui.py").read_text(
            encoding="utf-8")
        self.assertIn('textvariable=self.files_count_var,\n'
                      '                  font=("Segoe UI", 11, "bold"), width=50,',
                      source)

    def test_background_tab_uses_small_encoder_event_budget(self):
        source = inspect.getsource(OptimizerGUI._drain_events)
        self.assertIn('0.006 if getattr(self, "background_ui", False) else 0.030',
                      source)

    def test_file_counter_shows_total_and_selected_counts(self):
        self.assertEqual(
            _file_count_text(3497, 84, 20),
            "Files: 3,497 total / 84 filtered / 20 selected")

    def test_inspection_summary_always_keeps_all_counts_visible(self):
        text = _inspection_summary(2743, 74, 0, "waiting")
        self.assertIn("74 of 2,743 inspected", text)
        self.assertIn("74 succeeded", text)
        self.assertIn("0 failed", text)

    def test_active_encoder_rows_have_distinct_visual_tags(self):
        self.assertEqual(_row_tags("Sampling"), ("sampling",))
        self.assertEqual(_row_tags("Encoding"), ("encoding",))
        self.assertEqual(_row_tags("Paused"), ("paused",))
        self.assertEqual(_row_tags("Completed"), ())
        self.assertEqual(_row_tags("Completed", True), ("larger_output",))
        source = inspect.getsource(OptimizerGUI.apply_theme)
        self.assertIn('"encoding", background=', source)
        self.assertIn('"sampling", background=', source)
        self.assertIn('"paused", background=', source)
        self.assertIn('"larger_output", foreground=', source)

    def test_theme_can_be_applied_before_file_table_is_built(self):
        source = inspect.getsource(OptimizerGUI.apply_theme)
        self.assertIn('if hasattr(self, "table"):', source)
        self.assertIn('self.table.tag_configure("missing"', source)

    def test_large_queue_headings_use_memory_not_treeview_rows(self):
        items = [SimpleNamespace(
            path=Path(f"movie-{index}.mkv"), status="Waiting",
            selected=index % 2 == 0, force_encode=False)
            for index in range(3_500)]
        table = Mock()
        gui = SimpleNamespace(
            live=SimpleNamespace(snapshot=lambda: items), table=table,
            force_encode_paths=set(), queued_force={}, clean_paths=set(),
            clean_available=True,
        )
        OptimizerGUI._refresh_encode_all_heading(gui)
        OptimizerGUI._refresh_force_all_heading(gui)
        OptimizerGUI._refresh_clean_all_heading(gui)
        table.item.assert_not_called()
        table.set.assert_not_called()
        self.assertEqual(table.heading.call_count, 3)

    def test_filtered_row_order_does_not_query_every_tree_item(self):
        visible = str(Path("visible.mkv"))
        hidden = str(Path("hidden.mkv"))
        table = Mock()
        table.get_children.return_value = (visible,)
        gui = SimpleNamespace(
            filter_order=[visible, hidden], filtered_rows={hidden}, table=table)
        self.assertEqual(
            OptimizerGUI._all_table_rows(gui), [visible, hidden])
        table.exists.assert_not_called()

    def test_all_table_rows_recovers_detached_rows_missing_from_order(self):
        visible = str(Path("visible.mkv"))
        ordered_hidden = str(Path("ordered-hidden.mkv"))
        late_hidden = str(Path("late-hidden.mkv"))
        table = Mock()
        table.get_children.return_value = (visible,)
        gui = SimpleNamespace(
            filter_order=[visible, ordered_hidden],
            filtered_rows={ordered_hidden, late_hidden}, table=table)

        rows = OptimizerGUI._all_table_rows(gui)

        self.assertEqual(set(rows), {visible, ordered_hidden, late_hidden})
        self.assertEqual(len(rows), 3)

    def test_folder_change_clears_attached_detached_and_ordered_old_rows(self):
        visible = str(Path("old-visible.mkv"))
        hidden = str(Path("old-hidden.mkv"))
        table = Mock()
        table.get_children.return_value = (visible,)
        table.exists.side_effect = lambda iid: iid in {visible, hidden}
        gui = SimpleNamespace(
            table=table,
            filter_order=[visible, hidden],
            filtered_rows={hidden},
            filter_auto_excluded={Path(hidden)},
            missing_row_values={Path(visible): ["old"]},
            file_selection={Path(visible): False, Path(hidden): True},
        )

        OptimizerGUI._clear_inventory_view_for_new_root(gui)

        self.assertEqual(
            {entry.args[0] for entry in table.delete.call_args_list},
            {visible, hidden})
        self.assertEqual(gui.filter_order, [])
        self.assertEqual(gui.filtered_rows, set())
        self.assertEqual(gui.filter_auto_excluded, set())
        self.assertEqual(gui.missing_row_values, {})
        self.assertEqual(gui.file_selection, {})

    def test_directory_session_reset_clears_per_folder_state_only(self):
        movie = Path("old/movie.mkv")
        table = Mock()
        table.get_children.return_value = (str(movie),)
        table.exists.return_value = True
        pending = queue.Queue()
        pending.put(movie)
        events = queue.Queue()
        events.put(("row", movie))
        completed_history = {movie: Path("old/output.mkv")}
        gui = SimpleNamespace(
            table=table, events=events, urgent_events=queue.Queue(),
            filter_order=[str(movie)], filtered_rows=set(),
            filter_auto_excluded={movie}, missing_row_values={movie: []},
            file_selection={movie: True}, force_encode_paths={movie},
            clean_paths={movie}, queued_reanalysis={movie: ([], 0)},
            queued_force={movie: ([], 0)}, sample_variants={movie: 2},
            progressive_samples={movie: ((), ())}, progressive_targets={movie: 3},
            retained_failed_outputs={movie: Path("temp.mkv")},
            encode_failure_details={movie: "failed"}, deleted_backups={movie},
            dropped_entries={movie: Path("old")}, inspection_results={movie: Mock()},
            inspection_errors={movie: "bad"}, inspection_enqueued={movie},
            inspection_batch=[movie], reanalyze_preserve={movie},
            inspection_queue=pending, reanalyze_target=movie, drag_iid=str(movie),
            start_pending=True, last_file_completed=True,
            current_cancel_event=threading.Event(), inspection_active_path=movie,
            inspection_cycle_active=True, inspection_total=4, inspection_done=2,
            inspection_failed=1, inspection_started_at=1.0,
            directory_discovered_count=9, directory_scan_baseline_count=9,
            inventory_folders_scanned=8, inventory_files_scanned=9,
            inventory_top_level_folders=3, global_refresh_pending=True,
            missing_refresh_pending=True, inspection_cancel=threading.Event(),
            directory_discovery_cancel=threading.Event(),
            completed_outputs=completed_history,
        )

        OptimizerGUI._reset_directory_session(gui)

        self.assertEqual(gui.file_selection, {})
        self.assertEqual(gui.force_encode_paths, set())
        self.assertEqual(gui.clean_paths, set())
        self.assertEqual(gui.inspection_results, {})
        self.assertTrue(gui.inspection_queue.empty())
        self.assertFalse(gui.start_pending)
        self.assertIsNone(gui.reanalyze_target)
        self.assertEqual(gui.directory_discovered_count, 0)
        self.assertFalse(gui.manual_order_active)
        self.assertIs(gui.completed_outputs, completed_history)

    def test_manual_handle_order_survives_view_refresh_until_sort_changes(self):
        class OrderTable:
            def __init__(self, rows):
                self.rows = list(rows)

            def get_children(self):
                return tuple(self.rows)

            def move(self, iid, _parent, _index):
                self.rows.remove(iid)
                self.rows.append(iid)

            def heading(self, *_args, **_kwargs):
                pass

        alpha = str(Path("Alpha.mkv"))
        beta = str(Path("Beta.mkv"))
        manual_order = [beta, alpha]
        table = OrderTable(manual_order)
        gui = SimpleNamespace(
            table=table, filter_order=list(manual_order), filtered_rows=set(),
            manual_order_active=True, inspection_results={},
            sort_mode_var=SimpleNamespace(get=lambda: "Filename"),
            _all_table_rows=lambda: OptimizerGUI._all_table_rows(gui),
            _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock(),
            apply_media_view=lambda: OptimizerGUI.apply_media_view(gui),
        )
        with patch.object(OptimizerGUI, "_path_matches_active_filter",
                          return_value=True), patch.object(
                              OptimizerGUI, "_set_path_filter_visibility"):
            OptimizerGUI.apply_media_view(gui)
            self.assertEqual(gui.filter_order, manual_order)
            self.assertEqual(table.rows, manual_order)

            OptimizerGUI._media_view_option_changed(gui, sort_changed=True)
            self.assertFalse(gui.manual_order_active)
            self.assertEqual(gui.filter_order, [alpha, beta])
            self.assertEqual(table.rows, [alpha, beta])

    def test_dark_theme_covers_every_encoder_toggle(self):
        source = inspect.getsource(OptimizerGUI.apply_theme)
        for toggle in ("active_scan_check", "default_encode_toggle",
                       "inspect_new_toggle"):
            self.assertIn(f'hasattr(self, "{toggle}")', source)
            self.assertIn(
                f'self.{toggle}.configure(background=colors["bg"])', source)

    def test_dark_theme_keeps_hover_text_readable(self):
        source = inspect.getsource(OptimizerGUI.apply_theme)
        self.assertIn('"Treeview.Heading"', source)
        self.assertIn('"TCheckbutton"', source)
        self.assertIn('("active", colors["select"])', source)
        self.assertIn('("active", colors["bg"])', source)
        self.assertIn('("active", colors["fg"])', source)

    def test_encoder_toggles_are_compact_without_rectangular_focus_border(self):
        source = inspect.getsource(__import__(
            "video_optimizer.gui", fromlist=["DirectoryToggle"]).DirectoryToggle)
        self.assertIn("width=76, height=26", source)
        self.assertIn('outline = "#4f9cf9" if self._focused else color', source)
        self.assertNotIn("highlightthickness=2", source)

    def test_per_file_column_is_named_force_encode(self):
        source = Path(__file__).parents[1] / "video_optimizer" / "gui.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('(\"force\", _force_all_text([]), 100)', text)
        self.assertNotIn('(\"force\", \"Always encode\", 95)', text)

    def test_file_name_and_path_are_separate_columns(self):
        source = Path(__file__).parents[1] / "video_optimizer" / "gui.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('(\"file\", \"File\", 240), (\"path\", \"Path\", 300)', text)
        self.assertIn('OptimizerGUI._marked_filename(self, path), status', text)
        self.assertIn('str(path.parent)', text)

    def test_force_change_does_not_move_visible_row(self):
        source = inspect.getsource(OptimizerGUI._set_file_forced)
        self.assertNotIn("self.table.move", source)

    def test_reanalysis_priority_does_not_move_visible_row(self):
        source = inspect.getsource(OptimizerGUI.reanalyze)
        self.assertNotIn("self.table.move", source)
        self.assertNotIn("_place_priority_row", source)

    def test_active_row_filter_refresh_preserves_visible_sorted_position(self):
        source = inspect.getsource(OptimizerGUI._set_path_filter_visibility)
        visible_branch = source[source.index("if visible:"):]
        self.assertIn("if reattach_at_end and iid in self.filtered_rows", visible_branch)
        self.assertIn('self.table.move(iid, "", "end")', visible_branch)

    def test_normal_headings_do_not_receive_empty_command(self):
        source = Path(__file__).parents[1] / "video_optimizer" / "gui.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('if key == "selected":', text)
        self.assertIn('self.table.heading(key, text=label)', text)
        self.assertNotIn('if key == "selected" else None', text)

    def test_encoder_settings_explain_and_link_required_downloads(self):
        source = Path(__file__).parents[1] / "video_optimizer" / "gui.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('HANDBRAKE_CLI_DOWNLOAD_URL = "https://handbrake.fr/downloads2.php"', text)
        self.assertIn('FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"', text)
        self.assertIn('text="Get HandBrakeCLI…"', text)
        self.assertIn('text="Get FFmpeg (includes ffprobe)…"', text)
        self.assertIn('ffprobe.exe is included in Windows FFmpeg build packages', text)

    def test_local_user_guide_is_visible_and_shipped_by_windows_build(self):
        root = Path(__file__).parents[1]
        gui = (root / "video_optimizer" / "gui.py").read_text(encoding="utf-8")
        build = (root / "build.bat").read_text(encoding="utf-8")
        guide = (root / "USER_GUIDE.html").read_text(encoding="utf-8")
        self.assertIn('text="User Guide"', gui)
        self.assertIn('application_directory() / "USER_GUIDE.html"', gui)
        self.assertIn('copy /Y USER_GUIDE.html dist\\USER_GUIDE.html', build)
        for heading_id in ("media-inspection", "sampling-and-projected-savings",
                           "cleaner-tab", "rename-tab"):
            self.assertIn(f'id="{heading_id}"', guide)

    def test_click_columns_follow_the_displayed_column_order(self):
        self.assertEqual(_display_column_number("action"), "#11")
        self.assertEqual(_display_column_number("backup"), "#12")
        self.assertEqual(_display_column_number("selected"), "#13")
        self.assertEqual(_display_column_number("force"), "#14")

    def test_requested_clickable_column_order(self):
        self.assertLess(ENCODER_DISPLAY_COLUMNS.index("action"),
                        ENCODER_DISPLAY_COLUMNS.index("backup"))
        self.assertLess(ENCODER_DISPLAY_COLUMNS.index("selected"),
                        ENCODER_DISPLAY_COLUMNS.index("force"))

    def test_global_encode_and_force_headings_include_filtered_rows(self):
        encode_source = inspect.getsource(OptimizerGUI.toggle_all_file_selection)
        force_source = inspect.getsource(OptimizerGUI.toggle_all_force_encode)
        self.assertIn("for item in self.live.snapshot()", encode_source)
        self.assertIn("for item in self.live.snapshot()", force_source)
        self.assertIn("for iid in self._all_table_rows():", encode_source)
        self.assertIn("for iid in self._all_table_rows():", force_source)

    def test_encode_all_unchecks_rows_hidden_by_filter(self):
        visible = str(Path("visible.mkv").resolve())
        hidden = str(Path("hidden.mkv").resolve())
        values = ["☰", "☐ Force", "movie.mkv", "Waiting", "—", "—",
                  "1.0 GiB", "—", "Awaiting Start", "—", "☑ Encode",
                  ".", "☐ Clean", "H.264 • 1080p", "WASTEFUL"]
        table = Mock()
        table.item.side_effect = lambda _iid, _field: tuple(values)
        gui = SimpleNamespace(
            live=None,
            table=table,
            _all_table_rows=lambda: [visible, hidden],
            _set_file_selected=Mock(),
            _refresh_encode_all_heading=Mock(),
        )
        OptimizerGUI.toggle_all_file_selection(gui)
        self.assertEqual(gui._set_file_selected.call_count, 2)
        called_paths = {call.args[0] for call in gui._set_file_selected.call_args_list}
        self.assertEqual(called_paths, {Path(visible), Path(hidden)})
        self.assertTrue(all(call.args[1] is False
                            for call in gui._set_file_selected.call_args_list))

    def test_encode_all_selects_visible_rows_and_excludes_hidden_queue_rows(self):
        visible = Path("visible.mkv")
        hidden = Path("hidden.mkv")
        visible_item = SimpleNamespace(
            path=visible, status="Not selected", selected=False)
        hidden_item = SimpleNamespace(
            path=hidden, status="Waiting", selected=True)
        live = SimpleNamespace(
            snapshot=lambda: [visible_item, hidden_item],
            set_selected=Mock(return_value=True))
        table = Mock()
        table.get_children.return_value = (str(visible),)
        table.item.return_value = (
            "☰", "☐ Force", visible.name, "Not selected", "—", "—",
            "1.0 GiB", "—", "Excluded", "—", "☐ Encode", ".", "☐ Clean")
        gui = SimpleNamespace(
            live=live, table=table, filtered_rows={str(hidden)},
            file_selection={}, force_encode_paths=set(),
            _set_file_selected=Mock(), _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock())

        OptimizerGUI.toggle_all_file_selection(gui)

        self.assertIn(call(hidden, False),
                      live.set_selected.call_args_list)
        gui._set_file_selected.assert_called_once()
        self.assertEqual(gui._set_file_selected.call_args.args[:2],
                         (visible, True))
        self.assertFalse(gui.file_selection[hidden])

    def test_encode_all_uses_inventory_selection_not_stale_rendered_checkbox(self):
        movie = Path("visible.mkv")
        # The inventory says False while a stale rendered checkbox says True.
        # The inventory is authoritative, so Encode All selects this row.
        item = SimpleNamespace(path=movie, status="Waiting", selected=False)
        live = SimpleNamespace(
            snapshot=lambda: [item], set_selected=Mock(return_value=True))
        table = Mock()
        table.get_children.return_value = (str(movie),)
        table.item.return_value = (
            "☰", "☐ Force", movie.name, "Waiting", "—", "—",
            "1.0 GiB", "—", "Ready", "—", "☑ Encode", ".", "☐ Clean")
        gui = SimpleNamespace(
            live=live, table=table, filtered_rows=set(), file_selection={},
            force_encode_paths=set(), _set_file_selected=Mock(),
            _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock())

        OptimizerGUI.toggle_all_file_selection(gui)

        gui._set_file_selected.assert_called_once()
        self.assertEqual(gui._set_file_selected.call_args.args[:2],
                         (movie, True))

    def test_encode_all_uses_exact_tree_iid_to_find_live_unc_path(self):
        class QueuePath:
            def __str__(self):
                return r"\\server\share\movie.mkv"

        queue_path = QueuePath()
        item = SimpleNamespace(
            path=queue_path, status="Waiting", selected=True)
        live = SimpleNamespace(snapshot=lambda: [item])
        table = Mock()
        table.get_children.return_value = (str(queue_path),)
        table.item.return_value = (
            "☰", "☐ Force", "movie.mkv", "Waiting", "—", "—",
            "1.0 GiB", "—", "Ready", "—", "☑ Encode", ".", "☐ Clean")
        gui = SimpleNamespace(
            live=live, table=table, filtered_rows=set(), file_selection={},
            force_encode_paths=set(), _set_file_selected=Mock(),
            _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock())

        OptimizerGUI.toggle_all_file_selection(gui)

        gui._set_file_selected.assert_called_once()
        self.assertIs(gui._set_file_selected.call_args.args[0], queue_path)
        self.assertFalse(gui._set_file_selected.call_args.args[1])

    def test_event_drain_applies_filters_during_idle_inspection(self):
        source = inspect.getsource(OptimizerGUI._drain_events)
        row_branch = source[source.index('elif event[0] == "row":'):
                            source.index('elif event[0] == "inspection_result":')]
        result_branch = source[source.index('elif event[0] == "inspection_result":'):
                               source.index('elif event[0] == "clean_after_encode":')]
        self.assertIn('getattr(self, "_apply_filter_to_path", None)', row_branch)
        self.assertNotIn('if getattr(self, "running", False):', row_branch)
        self.assertIn('getattr(self, "_apply_filter_to_path", None)', result_branch)

    def test_manual_selection_clears_automatic_filter_restore_marker(self):
        movie = Path("visible.mkv")
        live = LiveQueue(Path("."), 0)
        live.items[movie] = FileState(
            movie, status="Waiting", selected=True, detail="Ready")
        values = [
            "☰", "☐ Force", movie.name, "Waiting", "—", "—", "—", "—",
            "Ready", "—", "☑ Encode", ".", "☐ Clean"]
        table = Mock()
        gui = SimpleNamespace(
            live=live, table=table, file_selection={movie: True},
            force_encode_paths=set(), filter_auto_excluded={movie},
            inspection_errors={}, inspection_results={},
            _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock())

        changed = OptimizerGUI._set_file_selected(
            gui, movie, False, values, refresh_heading=False)

        self.assertTrue(changed)
        self.assertFalse(live.items[movie].selected)
        self.assertNotIn(movie, gui.filter_auto_excluded)

    def test_filter_remembers_selected_inspected_row_for_later_restore(self):
        movie = Path("inspected.mkv")
        item = SimpleNamespace(
            path=movie, status="Waiting", selected=True, force_encode=False)
        live = SimpleNamespace(
            items={movie: item}, set_selected=Mock(return_value=True))
        table = Mock()
        table.exists.return_value = True
        table.item.return_value = [
            "☰", "☐ Force", movie.name, "Waiting", "—", "—", "—", "—",
            "Ready", "—", "☑ Encode", ".", "☐ Clean"]
        gui = SimpleNamespace(
            table=table, codec_filter_var=SimpleNamespace(get=lambda: "H.264"),
            resolution_filter_var=SimpleNamespace(get=lambda: "All"),
            rating_filter_var=SimpleNamespace(get=lambda: "All"),
            inspection_results={movie: SimpleNamespace()}, live=live,
            inspection_errors={}, filter_order=[],
            filter_auto_excluded=set(), default_encode_selected=True,
            file_selection={movie: True}, force_encode_paths=set(),
            filtered_rows=set(), _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock())

        with patch("video_optimizer.gui.inspection_matches", return_value=False):
            visible = OptimizerGUI._apply_filter_to_path(gui, movie)

        self.assertFalse(visible)
        self.assertIn(movie, gui.filter_auto_excluded)
        live.set_selected.assert_called_once_with(movie, False)

    def test_new_inspected_file_is_selected_only_when_it_matches_filter(self):
        movie = Path("new-movie.mkv")
        item = SimpleNamespace(
            path=movie, status="Not selected", selected=False,
            force_encode=False)
        live = SimpleNamespace(
            items={movie: item}, set_selected=Mock(return_value=True))
        table = Mock()
        table.exists.return_value = True
        table.item.return_value = [
            "☰", "☐ Force", movie.name, "Not selected", "—", "—", "—",
            "—", "Excluded", "—", "☐ Encode", ".", "☐ Clean"]
        result = SimpleNamespace()
        gui = SimpleNamespace(
            table=table, codec_filter_var=SimpleNamespace(get=lambda: "H.264"),
            resolution_filter_var=SimpleNamespace(get=lambda: "All"),
            rating_filter_var=SimpleNamespace(get=lambda: "All"),
            inspection_results={movie: result}, live=live,
            filter_auto_excluded={movie}, default_encode_selected=True,
            file_selection={movie: False}, force_encode_paths=set(),
            filtered_rows={str(movie)}, _refresh_encode_all_heading=Mock(),
            filter_order=[], inspection_errors={},
            _refresh_force_all_heading=Mock())
        with patch(
                "video_optimizer.gui.inspection_matches", return_value=True):
            visible = OptimizerGUI._apply_filter_to_path(gui, movie)

        self.assertTrue(visible)
        live.set_selected.assert_called_once_with(movie, True)
        self.assertTrue(gui.file_selection[movie])
        self.assertNotIn(movie, gui.filter_auto_excluded)
        self.assertIn(str(movie), gui.filter_order)

    def test_filter_restore_updates_stale_excluded_decision(self):
        movie = Path("restored.mkv")
        item = SimpleNamespace(
            path=movie, status="Waiting", selected=False,
            force_encode=False, detail="Excluded from processing")

        def set_selected(_path, selected):
            item.selected = selected
            item.status = "Waiting"
            item.detail = "Ready"
            return True

        live = SimpleNamespace(items={movie: item}, set_selected=set_selected)
        table = Mock()
        table.exists.return_value = True
        table.item.return_value = [
            "☰", "☐ Force", movie.name, "Not selected", "—", "—", "—",
            "—", "Excluded from processing", "—", "☐ Encode", ".",
            "☐ Clean"]
        gui = SimpleNamespace(
            table=table, codec_filter_var=SimpleNamespace(get=lambda: "All"),
            resolution_filter_var=SimpleNamespace(get=lambda: "All"),
            rating_filter_var=SimpleNamespace(get=lambda: "All"),
            inspection_results={movie: SimpleNamespace()}, inspection_errors={},
            live=live, filter_auto_excluded={movie},
            default_encode_selected=True, file_selection={movie: False},
            force_encode_paths=set(), filtered_rows={str(movie)},
            filter_order=[], _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock())

        visible = OptimizerGUI._apply_filter_to_path(gui, movie)

        self.assertTrue(visible)
        updated = table.item.call_args_list[-1].kwargs["values"]
        self.assertEqual(updated[3], "Waiting")
        self.assertEqual(updated[8], "Media inspected — Ready")
        self.assertEqual(updated[10], "☑ Encode")

    def test_large_in_progress_inventory_survives_repeated_filter_changes(self):
        class FilterTable:
            def __init__(self):
                self.rows = {}
                self.attached = []

            def exists(self, iid):
                return iid in self.rows

            def get_children(self):
                return tuple(self.attached)

            def item(self, iid, field=None, **kwargs):
                if "values" in kwargs:
                    self.rows[iid] = list(kwargs["values"])
                if field == "values":
                    return tuple(self.rows[iid])
                return {"values": tuple(self.rows[iid])}

            def move(self, iid, _parent, _index):
                if iid in self.attached:
                    self.attached.remove(iid)
                self.attached.append(iid)

            def detach(self, iid):
                if iid in self.attached:
                    self.attached.remove(iid)

            def heading(self, *_args, **_kwargs):
                pass

        total = 3_098
        paths = [Path(f"movie-{index:04}.mkv") for index in range(total)]
        items = {
            path: SimpleNamespace(
                path=path, status="Waiting", selected=True,
                force_encode=False, detail="Ready")
            for path in paths}

        def set_selected(path, selected):
            item = items[path]
            item.selected = selected
            item.status = "Waiting" if selected else "Not selected"
            item.detail = "Ready" if selected else "Excluded from processing"
            return True

        table = FilterTable()
        base_values = [
            "☰", "☐ Force", "movie.mkv", "Waiting", "—", "—", "—",
            "—", "Ready", "—", "☑ Encode", ".", "☐ Clean"]
        gui = SimpleNamespace(
            table=table, filter_order=[], filtered_rows=set(),
            filter_auto_excluded=set(), default_encode_selected=True,
            file_selection={path: True for path in paths},
            force_encode_paths=set(), inspection_results={}, inspection_errors={},
            live=SimpleNamespace(
                items=items, snapshot=lambda: list(items.values()),
                set_selected=set_selected),
            codec_filter_var=SimpleNamespace(get=lambda: "All"),
            resolution_filter_var=SimpleNamespace(get=lambda: "All"),
            rating_filter_var=SimpleNamespace(get=lambda: gui.rating),
            sort_mode_var=SimpleNamespace(get=lambda: "Filename"),
            rating="Wasteful", files_count_var=Mock())
        gui._refresh_filtered_row_selection = lambda path, item, values: (
            OptimizerGUI._refresh_filtered_row_selection(gui, path, item, values))
        gui._all_table_rows = lambda: OptimizerGUI._all_table_rows(gui)
        gui._refresh_encode_all_heading = lambda: (
            OptimizerGUI._refresh_encode_all_heading(gui))
        gui._refresh_force_all_heading = Mock()

        # Inventory rows arrive while Wasteful is active.  Only three have
        # completed inspection and match; every other row becomes detached.
        for path in paths:
            iid = str(path)
            table.rows[iid] = list(base_values)
            table.attached.append(iid)
            if path in paths[:3]:
                gui.inspection_results[path] = SimpleNamespace(rating="WASTEFUL")
            with patch(
                    "video_optimizer.gui.inspection_matches",
                    side_effect=lambda result, *_args: result.rating == "WASTEFUL"):
                OptimizerGUI._apply_filter_to_path(
                    gui, path, refresh_headings=False)

        self.assertEqual(len(gui.filter_order), total)
        self.assertEqual(len(gui.filtered_rows), total - 3)
        self.assertEqual(len(table.get_children()), 3)

        # Changing to All must recover every detached row, not merely the
        # handful that happened to be visible when the prior filter ran.
        gui.rating = "All"
        OptimizerGUI.apply_media_view(gui)
        self.assertEqual(len(table.get_children()), total)
        self.assertEqual(gui.filtered_rows, set())
        self.assertTrue(all(item.selected for item in items.values()))
        self.assertTrue(all(
            table.rows[str(path)][8] != "Excluded from processing"
            for path in paths))

        # Returning to Wasteful during the same inspection must again show
        # exactly the known matching rows and retain the complete master list.
        gui.rating = "Wasteful"
        with patch(
                "video_optimizer.gui.inspection_matches",
                side_effect=lambda result, *_args: result.rating == "WASTEFUL"):
            OptimizerGUI.apply_media_view(gui)
        self.assertEqual(len(table.get_children()), 3)
        self.assertEqual(len(gui.filter_order), total)
        self.assertEqual(len(gui.filtered_rows), total - 3)

    def test_start_synchronizes_visible_checkbox_with_real_queue(self):
        visible = Path("visible.mkv")
        hidden = Path("hidden.mkv")
        live = LiveQueue(Path("."), 0)
        live.items = {
            visible: FileState(visible, status="Waiting", selected=True),
            hidden: FileState(hidden, status="Waiting", selected=True),
        }
        values = [
            "☰", "☐ Force", visible.name, "Waiting", "—", "—", "—", "—",
            "Ready", "—", "☑ Encode", ".", "☐ Clean"]
        table = Mock()
        table.get_children.return_value = (str(visible),)
        table.exists.side_effect = lambda iid: iid == str(visible)
        table.item.return_value = values
        gui = SimpleNamespace(
            live=live, table=table, filtered_rows={str(hidden)},
            file_selection={}, force_encode_paths=set())

        count = OptimizerGUI._sync_visible_selection_for_start(gui)

        self.assertEqual(count, 1)
        self.assertTrue(live.items[visible].selected)
        self.assertEqual(live.items[visible].status, "Waiting")
        self.assertFalse(live.items[hidden].selected)
        self.assertEqual(live.items[hidden].status, "Not selected")

    def test_stop_all_cancels_a_pending_start(self):
        gui = SimpleNamespace(
            running=False, start_pending=True, start_button=Mock(),
            stop_button=Mock(), status_var=Mock(), current_var=Mock(),
            _set_control_indicator=Mock(), _set_filter_lock_state=Mock(),
            _log=Mock())

        OptimizerGUI.stop(gui)

        self.assertFalse(gui.start_pending)
        gui.start_button.configure.assert_called_once()
        gui.stop_button.configure.assert_called_once()
        gui.status_var.set.assert_called_once_with("Pending Start cancelled")

    def test_cancelled_pending_start_callback_does_not_restart(self):
        gui = SimpleNamespace(start_pending=False, start=Mock())
        OptimizerGUI._continue_pending_start(gui)
        gui.start.assert_not_called()

    def test_pending_start_locks_filters_immediately(self):
        source = inspect.getsource(OptimizerGUI.start)
        pending_branch = source[
            source.index("if inventory_busy or inspection_busy or other_work_busy:"):
            source.index("try:")]
        self.assertIn("self._set_filter_lock_state(True)", pending_branch)
        self.assertLess(
            pending_branch.index("self._set_filter_lock_state(True)"),
            pending_branch.index("self.after(100, self._continue_pending_start)"))

    def test_global_controls_use_live_queue_before_treeview_fallback(self):
        encode_source = inspect.getsource(OptimizerGUI.toggle_all_file_selection)
        force_source = inspect.getsource(OptimizerGUI.toggle_all_force_encode)
        self.assertLess(encode_source.index("if self.live is not None"),
                        encode_source.index("for iid in self._all_table_rows()"))
        self.assertLess(force_source.index("if self.live is not None"),
                        force_source.index("for iid in self._all_table_rows()"))
        self.assertIn("for item in self.live.snapshot()", encode_source)
        self.assertIn("for iid in self.table.get_children()", force_source)

    def test_control_columns_are_compact_and_fixed_width(self):
        source = Path(__file__).parents[1] / "video_optimizer" / "gui.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('("force", _force_all_text([]), 100)', text)
        self.assertIn('("selected", _encode_all_text([]), 110)', text)
        self.assertIn('("clean", _clean_all_text([]), 100)', text)
        self.assertIn('stretch=key in {"file", "path", "decision"}', text)

    def test_short_numeric_columns_give_space_to_decision_result(self):
        source = (Path(__file__).parents[1] / "video_optimizer" / "gui.py").read_text(
            encoding="utf-8")
        self.assertIn('("savings", "Savings", 72)', source)
        self.assertIn('("current_size", "Source size", 84)', source)
        self.assertIn('("projected", "Est. output", 84)', source)
        self.assertIn('("decision", "Decision / result", 250)', source)

    def test_progress_preserves_filename_heading(self):
        events = queue.Queue()
        events.put(("details", "Example Movie.mkv", "RF 22.5", "Projected size"))
        events.put(("progress", 50.0, "30 FPS • ETA 10m • Elapsed 10m"))
        events.put(("completed_progress", 100.0, "Completed • 20% saved"))
        gui = SimpleNamespace(
            events=events, current_var=Mock(), encode_stats_var=Mock(),
            settings_var=Mock(), projection_var=Mock(), progress=Mock(),
            after=Mock(), _drain_events=Mock(), last_file_completed=False)
        OptimizerGUI._drain_events(gui)
        gui.current_var.set.assert_called_once_with("Example Movie.mkv")
        self.assertIn((("30 FPS • ETA 10m • Elapsed 10m",), {}),
                      gui.encode_stats_var.set.call_args_list)
        gui.progress.set.assert_called_with(100.0)

    def test_watcher_idle_clears_stale_current_file_display(self):
        events = queue.Queue()
        events.put(("queue_idle",))
        gui = SimpleNamespace(
            events=events, current_var=Mock(), encode_stats_var=Mock(),
            settings_var=Mock(), projection_var=Mock(), progress=Mock(),
            start_button=Mock(), status_var=Mock(),
            _set_control_indicator=Mock(), after=Mock(),
            _drain_events=Mock(), last_file_completed=True)

        OptimizerGUI._drain_events(gui)

        gui.current_var.set.assert_called_once_with(
            "Queue idle — watching for new files")
        gui.encode_stats_var.set.assert_called_once_with("")
        gui.settings_var.set.assert_called_once_with("")
        gui.projection_var.set.assert_called_once_with("")
        gui.progress.set_idle.assert_called_once_with()
        gui.start_button.configure.assert_called_once_with(
            state="disabled", text="▶ Waiting", style="StartIdle.TButton")
        gui.status_var.set.assert_called_once_with("Watching for new files")
        gui._set_control_indicator.assert_called_once_with(None)
        self.assertFalse(gui.last_file_completed)

    def test_new_work_restores_running_controls_after_watcher_idle(self):
        movie = Path("Example Movie.mkv")
        events = queue.Queue()
        events.put(("row", movie, "Sampling", "—", "—", "1 GiB", "—",
                    "Sampling"))
        table = Mock()
        table.exists.return_value = False
        gui = SimpleNamespace(
            events=events, start_button=Mock(), status_var=Mock(),
            _set_control_indicator=Mock(), table=table, live=None,
            queued_reanalysis={}, queued_force={}, reanalyze_target=None,
            force_encode_paths=set(), missing_row_values={},
            file_selection={}, default_encode_selected=True,
            clean_new_encodes=False, clean_available=False, clean_paths=set(),
            inspection_results={}, run_config=None, progressive_samples={},
            status_callback=None, directory_discovery_active=False,
            inspect_new_files=False, filtered_rows=set(), filter_order=[],
            _apply_filter_to_path=Mock(), queue_inspection=Mock(),
            _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock(),
            _refresh_clean_all_heading=Mock(), _refresh_missing_button=Mock(),
            after=Mock(), _drain_events=Mock())

        OptimizerGUI._drain_events(gui)

        gui.start_button.configure.assert_called_once_with(
            state="disabled", text="▶ Running", style="StartActive.TButton")
        gui.status_var.set.assert_called_once_with("Running")
        gui._set_control_indicator.assert_called_once_with("running")

    def test_worker_reports_idle_once_while_active_scanning_waits(self):
        source = inspect.getsource(OptimizerGUI._run)
        self.assertIn("idle_reported = False", source)
        self.assertIn('self._emit("queue_idle")', source)
        self.assertIn("not idle_reported", source)

    def test_start_waits_for_inventory_and_enabled_inspection(self):
        source = inspect.getsource(OptimizerGUI.start)
        self.assertIn('getattr(self, "directory_discovery_active", False)', source)
        self.assertIn('OptimizerGUI._inspection_work_pending(self)', source)
        self.assertIn("waiting for directory inventory to finish", source)
        self.assertIn("waiting for media inspection to finish", source)

    def test_start_guard_detects_inspection_outside_cycle_flag_window(self):
        movie = Path("queued.mkv")
        pending = queue.Queue()
        pending.put(movie)
        gui = SimpleNamespace(
            inspect_new_files=True,
            inspection_thread=None,
            inspection_lock=threading.Lock(),
            inspection_cycle_active=False,
            inspection_active_path=None,
            inspection_enqueued={movie},
            inspection_queue=pending,
        )

        self.assertTrue(OptimizerGUI._inspection_work_pending(gui))
        gui.inspection_enqueued.clear()
        pending.get_nowait()
        self.assertFalse(OptimizerGUI._inspection_work_pending(gui))

    def test_start_guard_is_off_when_inspection_setting_is_disabled(self):
        worker = Mock()
        worker.is_alive.return_value = True
        gui = SimpleNamespace(
            inspect_new_files=False,
            inspection_thread=worker,
        )

        self.assertFalse(OptimizerGUI._inspection_work_pending(gui))

    def test_inspection_cycle_covers_result_delivery_and_filter_refresh(self):
        queue_source = inspect.getsource(OptimizerGUI.queue_inspection)
        worker_source = inspect.getsource(OptimizerGUI._inspection_worker)
        drain_source = inspect.getsource(OptimizerGUI._drain_events)
        finish_source = inspect.getsource(OptimizerGUI._finish_inspection_cycle)
        self.assertIn("self.inspection_cycle_active = True", queue_source)
        self.assertIn("self.inspection_cycle_active = False", finish_source)
        self.assertNotIn("self.inspection_enqueued.discard(path)",
                         worker_source.split('self._emit("inspection_result"')[0])
        inspection_result = drain_source[
            drain_source.index('event[0] == "inspection_result"'):
            drain_source.index('event[0] == "clean_after_encode"')]
        self.assertIn("self.inspection_enqueued.discard(resolved)",
                      inspection_result)
        self.assertIn("self.after_idle(self._continue_pending_start)", finish_source)
        self.assertIn("Inspection finished — applying filters", finish_source)
        self.assertLess(
            finish_source.index("self.apply_media_view()"),
            finish_source.index("self.inspection_cycle_active = False"))

    def test_worker_keeps_path_reserved_until_result_event_is_consumed(self):
        movie = Path("movie.mkv").resolve()
        pending = queue.Queue()
        pending.put(movie)
        gate = threading.Event()
        gate.set()
        emitted = []
        gui = SimpleNamespace(
            inspection_cancel=threading.Event(), inspection_inventory_gate=gate,
            inspection_lock=threading.Lock(), inspection_queue=pending,
            inspection_active_path=None, inspection_enqueued={movie},
            inspection_done=0, inspection_total=1, inspection_failed=0,
            inspection_cache=Mock(
                get=Mock(return_value=SimpleNamespace(path=movie)),
                save=Mock()),
            live=None, inspection_results={}, inspection_errors={},
            inspection_cycle_active=True, cancel_inspection_button=Mock(),
            inspection_thread=Mock(), inspection_started_at=0.0,
            _emit=lambda *event: emitted.append(event),
        )

        with patch("video_optimizer.gui.load_config"), patch(
                "video_optimizer.gui.resolve_program", return_value=Path("ffprobe")):
            OptimizerGUI._inspection_worker(gui)

        self.assertIn(movie, gui.inspection_enqueued)
        self.assertEqual(OptimizerGUI.queue_inspection(gui, [movie]), 0)
        self.assertTrue(gui.inspection_queue.empty())
        self.assertEqual(emitted[0][0], "inspection_result")
        self.assertEqual(emitted[-1], ("inspection_finished", False))

    def test_retired_inspection_worker_cannot_update_new_directory_session(self):
        movie = Path("old-root/movie.mkv")
        pending = queue.Queue()
        pending.put(movie)
        gate = threading.Event()
        gate.set()
        emitted = []
        gui = SimpleNamespace(
            inspection_generation=4,
            inspection_cancel=threading.Event(), inspection_inventory_gate=gate,
            inspection_lock=threading.Lock(), inspection_queue=pending,
            inspection_active_path=None, inspection_enqueued={movie},
            inspection_done=0, inspection_total=1, inspection_failed=0,
            inspection_cache=Mock(get=Mock(return_value=None), save=Mock()),
            _emit=lambda *event: emitted.append(event),
        )

        def finish_after_session_changed(*_args, **_kwargs):
            gui.inspection_generation = 5
            return SimpleNamespace(path=movie)

        with patch("video_optimizer.gui.load_config"), patch(
                "video_optimizer.gui.resolve_program",
                return_value=Path("ffprobe")), patch(
                "video_optimizer.gui.inspect_media",
                side_effect=finish_after_session_changed):
            OptimizerGUI._inspection_worker(gui, generation=4)

        self.assertEqual(emitted, [])
        self.assertEqual(gui.inspection_done, 0)

    def test_final_inspection_handoff_applies_filter_then_resumes_start(self):
        gui = SimpleNamespace(
            inspection_results={Path("movie.mkv"): object()},
            inspection_errors={}, directory_discovered_count=1,
            cancel_inspection_button=Mock(), inspection_progress={},
            inspection_lock=threading.Lock(), inspection_queue=queue.Queue(),
            inspection_active_path=None, inspection_enqueued=set(),
            inspection_status_var=Mock(), running=False,
            apply_media_view=Mock(), inspection_cycle_active=True,
            start_pending=True, after_idle=Mock(),
            _continue_pending_start=Mock(),
        )

        OptimizerGUI._finish_inspection_cycle(gui, False)

        gui.apply_media_view.assert_called_once_with()
        self.assertFalse(gui.inspection_cycle_active)
        gui.after_idle.assert_called_once_with(gui._continue_pending_start)

    def test_intermediate_inspection_finish_keeps_start_blocked(self):
        movie = Path("new.mkv")
        pending = queue.Queue()
        pending.put(movie)
        gui = SimpleNamespace(
            inspection_results={}, inspection_errors={},
            directory_discovered_count=1,
            cancel_inspection_button=Mock(), inspection_progress={},
            inspection_lock=threading.Lock(), inspection_queue=pending,
            inspection_active_path=None, inspection_enqueued={movie},
            inspection_status_var=Mock(), running=False,
            apply_media_view=Mock(), inspection_cycle_active=True,
            start_pending=True, after_idle=Mock(),
            _continue_pending_start=Mock(), _inspection_worker=Mock(),
        )
        worker = Mock()
        with patch("video_optimizer.gui.threading.Thread", return_value=worker):
            OptimizerGUI._finish_inspection_cycle(gui, False)

        self.assertTrue(gui.inspection_cycle_active)
        gui.apply_media_view.assert_not_called()
        gui.after_idle.assert_not_called()
        worker.start.assert_called_once_with()

    def test_force_after_completed_before_waiting(self):
        rows = [("force", "Queued"), ("done", "Completed"), ("next", "Waiting")]
        self.assertEqual(_priority_position(rows, "force"), 1)

    def test_force_below_all_finished_rows(self):
        rows = [("done", "Completed"), ("force", "Queued"), ("skip", "Skipped")]
        self.assertEqual(_priority_position(rows, "force"), 2)

    def test_force_after_active_and_existing_priority(self):
        rows = [("force", "Queued"), ("done", "Completed"),
                ("active", "Encoding"), ("priority", "Queued"), ("next", "Waiting")]
        self.assertEqual(_priority_position(rows, "force"), 3)

    def test_encoder_context_menu_starts_with_play_and_separator(self):
        source = inspect.getsource(OptimizerGUI._open_file_menu)
        menu_start = source.index("menu = tk.Menu")
        play = source.index('label="Play Video"', menu_start)
        separator = source.index("menu.add_separator()", play)
        reanalyze = source.index('label=("Reanalyze"', separator)
        self.assertLess(play, separator)
        self.assertLess(separator, reanalyze)

    def test_encoder_context_menu_ends_with_file_details(self):
        source = inspect.getsource(OptimizerGUI._open_file_menu)
        details = source.index('label="File Details..."')
        popup = source.index("menu.tk_popup", details)
        self.assertLess(details, popup)
        self.assertNotIn("menu.add_", source[details + 1:popup])

    def test_file_details_are_selectable_and_copyable(self):
        source = inspect.getsource(OptimizerGUI._show_file_details)
        self.assertIn('tk.Text(', source)
        self.assertIn('text.configure(state="disabled")', source)
        self.assertIn('text="Copy All"', source)
