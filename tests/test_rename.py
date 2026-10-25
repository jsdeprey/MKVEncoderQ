import inspect
import queue
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_optimizer.combined_gui import CleanCoordinator, CombinedApp
from video_optimizer.gui import (OptimizerGUI, _logical_rename_mapping,
                                 _moved_row_order)
from video_optimizer.inspection import MediaInspection
from video_optimizer.queueing import LiveQueue
from video_optimizer.rename_gui import (
    RenameGUI, RenameOptions, _restore_writable_state, execute_rename_plan,
    proposed_path, transform_stem, validate_rename_plan,
)


class RenameTests(unittest.TestCase):
    def test_completed_backup_is_a_transactional_rename_companion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = (root / "Old.mkv").resolve()
            new = (root / "New.mkv").resolve()
            old_backup = (root / "Old.mkv.bak").resolve()
            new_backup = (root / "New.mkv.bak").resolve()
            old.write_bytes(b"encoded")
            old_backup.write_bytes(b"original")
            app = SimpleNamespace()
            with unittest.mock.patch(
                    "video_optimizer.combined_gui.load_config",
                    return_value=SimpleNamespace(backup_suffix=".bak")):
                companions = CombinedApp.rename_companion_plan(app, {old: new})
            execute_rename_plan({old: new, **companions})
            self.assertTrue(new.is_file())
            self.assertTrue(new_backup.is_file())
            self.assertFalse(old_backup.exists())
            self.assertEqual(new_backup.read_bytes(), b"original")

    def test_non_mkv_source_backup_follows_renamed_completed_mkv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (root / "Old.mp4").resolve()
            output = (root / "Old.mkv").resolve()
            renamed = (root / "New.mkv").resolve()
            backup = (root / "Old.mp4.bak").resolve()
            renamed_backup = (root / "New.mkv.bak").resolve()
            output.write_bytes(b"encoded")
            backup.write_bytes(b"original")
            app = SimpleNamespace(
                encoder=SimpleNamespace(completed_outputs={source: output}))
            with patch(
                    "video_optimizer.combined_gui.load_config",
                    return_value=SimpleNamespace(backup_suffix=".bak")):
                companions = CombinedApp.rename_companion_plan(
                    app, {output: renamed})
            self.assertEqual(companions, {backup: renamed_backup})
            execute_rename_plan({output: renamed, **companions})
            self.assertTrue(renamed.is_file())
            self.assertTrue(renamed_backup.is_file())
            self.assertFalse(backup.exists())

    def test_completed_output_rename_maps_back_to_non_mkv_encoder_row(self):
        source = Path("C:/Movies/Old.mp4")
        output = Path("C:/Movies/Old.mkv")
        renamed = Path("C:/Movies/New.mkv")
        self.assertEqual(
            _logical_rename_mapping(
                {output: renamed}, {source: output}, {source}),
            {source: renamed},
        )

    def test_backup_companion_collision_stops_whole_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = (root / "Old.mkv").resolve()
            new = (root / "New.mkv").resolve()
            old_backup = (root / "Old.mkv.bak").resolve()
            new_backup = (root / "New.mkv.bak").resolve()
            old.write_bytes(b"encoded")
            old_backup.write_bytes(b"original")
            new_backup.write_bytes(b"existing")
            with self.assertRaises(ValueError):
                execute_rename_plan({old: new, old_backup: new_backup})
            self.assertTrue(old.is_file())
            self.assertTrue(old_backup.is_file())
            self.assertEqual(new_backup.read_bytes(), b"existing")

    def test_temporary_rename_name_is_not_a_hidden_dotfile(self):
        source = inspect.getsource(execute_rename_plan)
        self.assertIn('f"mkvencoderq-rename-', source)
        self.assertNotIn('f".mkvencoderq-rename-', source)

    def test_rename_tab_has_compact_copyable_activity_log(self):
        source = inspect.getsource(RenameGUI._build)
        self.assertIn('text="Activity Log"', source)
        self.assertIn('height=7, wrap="word", state="disabled"', source)
        helper = inspect.getsource(RenameGUI._append_activity_log)
        self.assertIn('log.configure(state="normal")', helper)
        self.assertIn('log.configure(state="disabled")', helper)

    def test_rename_restores_writable_state_if_nas_changes_attribute(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Renamed.mkv"
            path.write_bytes(b"video")
            path.chmod(path.stat().st_mode & ~0o222)

            _restore_writable_state(path, was_writable=True)

            self.assertTrue(path.stat().st_mode & stat.S_IWRITE)

    def test_rename_preserves_intentionally_read_only_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Old.mkv"
            destination = Path(directory) / "New.mkv"
            source.write_bytes(b"video")
            source.chmod(source.stat().st_mode & ~0o222)

            execute_rename_plan({source: destination})

            self.assertFalse(destination.stat().st_mode & stat.S_IWRITE)

    def test_default_folder_button_and_dialog_are_explicit(self):
        build_source = inspect.getsource(RenameGUI._build)
        settings_source = inspect.getsource(RenameGUI.settings)
        self.assertIn('text="Set Default Folder..."', build_source)
        self.assertIn('title="Set Default Rename Folder"', settings_source)
        self.assertIn('"Default Rename Folder"', settings_source)

    def test_selected_file_action_ignores_other_checked_rows(self):
        selected = Path("Selected.mkv")
        table = SimpleNamespace(
            focus=lambda: "selected-row", selection=lambda: ("other-row",))
        gui = SimpleNamespace(
            table=table, paths={"selected-row": selected},
            _apply_paths=Mock())
        RenameGUI.apply_selected_rename(gui)
        gui._apply_paths.assert_called_once_with([selected])

    def test_checked_files_button_is_left_of_selected_file_button(self):
        source = inspect.getsource(RenameGUI._build)
        # Tk packs right-side buttons in reverse source order.
        self.assertLess(source.index('text="Rename Selected File"'),
                        source.index('text="Rename Checked Files"'))

    def test_remove_buttons_are_in_requested_order(self):
        source = inspect.getsource(RenameGUI._build)
        self.assertLess(source.index('text="Remove Checked"'),
                        source.index('text="Remove Selected"'))
        self.assertLess(source.index('text="Remove Selected"'),
                        source.index('text="Remove All"'))

    def test_spaces_to_dots_can_combine_with_find_replace(self):
        options = RenameOptions(
            find="WEB DL", replacement="WEB Download",
            spaces_to_dots=True)
        self.assertEqual(
            transform_stem("Example Movie WEB DL 1080p", options),
            "Example.Movie.WEB.Download.1080p")

    def test_spaces_to_dots_also_normalizes_numbering_separator(self):
        options = RenameOptions(
            spaces_to_dots=True, numbering=True,
            number_start=7, number_width=2)
        self.assertEqual(transform_stem("Example Movie", options),
                         "07.Example.Movie")

    def test_remove_checked_and_remove_all_only_change_the_list(self):
        class FakeTable:
            values = {"one": ["☑", "One.mkv"], "two": ["☐", "Two.mkv"],
                      "three": ["☑", "Three.mkv"]}

            def get_children(self):
                return tuple(self.values)

            def item(self, iid, option=None):
                return self.values[iid]

            def delete(self, iid):
                self.values.pop(iid)

        table = FakeTable()
        gui = SimpleNamespace(
            table=table,
            paths={iid: Path(values[1]) for iid, values in table.values.items()},
            refresh_preview=Mock(),
        )
        RenameGUI.remove_checked(gui)
        self.assertEqual(tuple(table.values), ("two",))
        self.assertEqual(tuple(gui.paths), ("two",))
        RenameGUI.remove_all(gui)
        self.assertFalse(table.values)
        self.assertFalse(gui.paths)

    def test_selected_rows_move_as_one_stable_block(self):
        rows = ["a", "b", "c", "d", "e"]
        self.assertEqual(_moved_row_order(rows, ["b", "d"], "top"),
                         ["b", "d", "a", "c", "e"])
        self.assertEqual(_moved_row_order(rows, ["b", "c"], "down"),
                         ["a", "d", "b", "c", "e"])
        self.assertEqual(_moved_row_order(rows, ["b", "c"], "up"),
                         ["b", "c", "a", "d", "e"])
        self.assertEqual(_moved_row_order(rows, ["b", "d"], "bottom"),
                         ["a", "c", "e", "b", "d"])

    def test_operations_follow_documented_order(self):
        options = RenameOptions(
            find="web-dl", replacement="", case_sensitive=False,
            prefix="Show ", suffix=" Final", remove_start=2, remove_end=1,
        )
        self.assertEqual(
            transform_stem("XXWEB-DL EpisodeZ", options),
            "Show  Episode Final",
        )

    def test_case_conversion_cannot_break_episode_normalization(self):
        options = RenameOptions(case_mode="lowercase", normalize_episode=True)
        self.assertEqual(transform_stem("My.Show.S01e02", options),
                         "my.show.S01E02")

    def test_case_conversion_can_preserve_resolution_tokens(self):
        options = RenameOptions(
            case_mode="UPPERCASE", preserve_resolution_case=True)
        self.assertEqual(
            transform_stem("Movie.720p.1080p.2160p.web-dl", options),
            "MOVIE.720p.1080p.2160p.WEB-DL",
        )

    def test_case_conversion_changes_resolution_tokens_by_default(self):
        options = RenameOptions(case_mode="UPPERCASE")
        self.assertEqual(transform_stem("Movie.1080p", options),
                         "MOVIE.1080P")

    def test_rename_table_has_remove_selected_context_action(self):
        source = inspect.getsource(RenameGUI._build)
        self.assertIn('self.table.bind("<Button-3>"', source)
        self.assertIn('label="Remove Selected from List"', source)

    def test_rename_context_menu_has_file_location_and_copy_actions(self):
        source = inspect.getsource(RenameGUI._build)
        self.assertIn('label="Open File Location"', source)
        self.assertIn('label="Copy Filename"', source)
        self.assertIn('label="Copy Directory Name"', source)

    def test_rename_copy_actions_use_clicked_file_and_parent_name(self):
        movie = Path("Shows") / "Example.S01E01.mkv"
        gui = SimpleNamespace(
            _context_path=Mock(return_value=movie),
            _copy_context_text=Mock(),
        )
        RenameGUI._copy_context_filename(gui)
        gui._copy_context_text.assert_called_once_with("Example.S01E01.mkv")
        gui._copy_context_text.reset_mock()
        RenameGUI._copy_context_directory_name(gui)
        gui._copy_context_text.assert_called_once_with("Shows")

    def test_context_menu_can_check_or_uncheck_selected_rows(self):
        class FakeTable:
            values = {"one": ["☐", "One.mkv"], "two": ["☐", "Two.mkv"],
                      "three": ["☑", "Three.mkv"]}

            def selection(self):
                return ("one", "two")

            def item(self, iid, option=None, **kwargs):
                if "values" in kwargs:
                    self.values[iid] = list(kwargs["values"])
                return self.values[iid]

        gui = SimpleNamespace(table=FakeTable(), refresh_preview=Mock())
        RenameGUI.set_selected(gui, True)
        self.assertEqual(gui.table.values["one"][0], "☑")
        self.assertEqual(gui.table.values["two"][0], "☑")
        self.assertEqual(gui.table.values["three"][0], "☑")
        RenameGUI.set_selected(gui, False)
        self.assertEqual(gui.table.values["one"][0], "☐")
        self.assertEqual(gui.table.values["two"][0], "☐")
        self.assertEqual(gui.table.values["three"][0], "☑")

    def test_x_format_conversion_is_optional(self):
        original = RenameOptions(normalize_episode=True)
        converted = RenameOptions(convert_x_format=True)
        self.assertEqual(transform_stem("Show 1x2", original), "Show 1x2")
        self.assertEqual(transform_stem("Show 1x2", converted), "Show S01E02")

    def test_multi_episode_code_is_normalized(self):
        options = RenameOptions(normalize_episode=True)
        self.assertEqual(transform_stem("Show.s1e2-e3", options),
                         "Show.S01E02-E03")

    def test_numbering_uses_checked_file_order(self):
        options = RenameOptions(numbering=True, number_start=7, number_width=3)
        self.assertEqual(transform_stem("Movie", options, 2), "009 Movie")

    def test_proposed_path_preserves_extension(self):
        path = Path("Movie.Name.MKV")
        proposed = proposed_path(path, RenameOptions(suffix=" Remastered"))
        self.assertEqual(proposed.name, "Movie.Name Remastered.MKV")

    def test_empty_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "A.mkv"
            source.write_bytes(b"video")
            with self.assertRaisesRegex(ValueError, "empty"):
                validate_rename_plan({source: source.with_name(".mkv")})

    def test_case_insensitive_destination_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "One.mkv"
            second = Path(directory) / "Two.mkv"
            first.write_bytes(b"1")
            second.write_bytes(b"2")
            with self.assertRaisesRegex(ValueError, "More than one"):
                validate_rename_plan({
                    first: first.with_name("Result.mkv"),
                    second: second.with_name("result.mkv"),
                })

    def test_reserved_windows_filename_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Movie.mkv"
            source.write_bytes(b"video")
            with self.assertRaisesRegex(ValueError, "reserved"):
                validate_rename_plan({source: source.with_name("CON.mkv")})

    def test_batch_rename_supports_filename_swap(self):
        with tempfile.TemporaryDirectory() as directory:
            first = (Path(directory) / "One.mkv").resolve()
            second = (Path(directory) / "Two.mkv").resolve()
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            execute_rename_plan({first: second, second: first})
            self.assertEqual(first.read_bytes(), b"second")
            self.assertEqual(second.read_bytes(), b"first")

    def test_live_queue_migrates_all_path_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = (root / "Old.mkv").resolve()
            new = (root / "New.mkv").resolve()
            old.write_bytes(b"video")
            queue = LiveQueue(root, 0)
            queue.refresh()
            queue.completed.add(old)
            queue.manual_roots[old] = root
            queue.reanalysis_queue.append(old)
            old.rename(new)
            queue.rename_path(old, new)
            self.assertIn(new, queue.items)
            self.assertEqual(queue.items[new].path, new)
            self.assertIn(new, queue.completed)
            self.assertIn(new, queue.manual_roots)
            self.assertEqual(list(queue.reanalysis_queue), [new])

    def test_live_queue_batch_path_swap_does_not_lose_either_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = (root / "One.mkv").resolve()
            second = (root / "Two.mkv").resolve()
            first.write_bytes(b"1")
            second.write_bytes(b"2")
            queue = LiveQueue(root, 0)
            queue.refresh()
            first_state = queue.items[first]
            second_state = queue.items[second]
            execute_rename_plan({first: second, second: first})
            queue.rename_paths({first: second, second: first})
            self.assertIs(queue.items[first], second_state)
            self.assertIs(queue.items[second], first_state)

    def test_live_queue_rename_removes_old_manual_file_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = (root / "Old.mkv").resolve()
            new = (root / "New.mkv").resolve()
            old.write_bytes(b"video")
            live = LiveQueue(root, 0)
            live.add_manual([(old, old)])

            live.rename_paths({old: new})

            self.assertNotIn(old, live.items)
            self.assertNotIn(old, live.manual_roots)
            self.assertNotIn(old, live.manual_roots.values())
            self.assertEqual(live.source_root(new), new)

    def test_large_encoder_rename_sync_does_not_resolve_every_nas_path(self):
        old = Path("//nas/share/Old.mkv")
        new = Path("//nas/share/New.mkv")
        rows = [str(old)] + [f"//nas/share/Movie-{index}.mkv"
                            for index in range(3_500)]
        values = (
            "☰", "☐ Force", old.name, "Completed", "100.0%", "50.0%",
            "4.0 GiB", "2.0 GiB", "2.0 GiB output", "[ Play ]",
            "☑ Encode", str(old.parent), "☐ Clean", "H.264", "WASTEFUL",
            "[ Present ]",
        )
        table = Mock()
        table.exists.side_effect = lambda iid: iid == str(old)
        table.index.return_value = 0
        table.item.side_effect = lambda _iid, field=None: (
            values if field == "values" else () if field == "tags" else {})
        table.selection.return_value = ()
        gui = SimpleNamespace(
            table=table, live=Mock(), reanalyze_target=None,
            force_encode_paths=set(), clean_paths=set(), file_selection={},
            dropped_entries={}, sample_variants={}, queued_reanalysis={},
            queued_force={}, missing_row_values={}, deleted_backups=set(),
            completed_outputs={Path(row): Path(row) for row in rows},
            inspection_results={}, inspection_errors={},
            inspection_lock=threading.Lock(),
            inspection_queue=queue.Queue(), inspection_enqueued=set(),
            inspection_cache=Mock(), filter_order=rows,
            filtered_rows=set(rows),
            apply_media_view=Mock(),
            _refresh_force_all_heading=Mock(),
            _refresh_encode_all_heading=Mock(),
            _refresh_clean_all_heading=Mock(),
            _refresh_missing_button=Mock())
        with patch.object(
                Path, "resolve",
                side_effect=AssertionError("rename synchronization touched NAS")):
            OptimizerGUI.paths_renamed(gui, {old: new})
        self.assertIn(str(new), gui.filter_order)
        self.assertNotIn(str(old), gui.filter_order)
        gui.apply_media_view.assert_called_once_with()

    def test_encoder_rename_migrates_pending_inspection_path(self):
        old = Path("//nas/share/Old.mkv")
        new = Path("//nas/share/New.mkv")
        values = (
            "☰", "☐ Force", old.name, "Waiting to scan", "0.0%", "—",
            "4.0 GiB", "—", "Awaiting inspection", "—", "☑ Encode",
            str(old.parent), "☐ Clean", "—", "—", "—")
        table = Mock()
        table.exists.side_effect = lambda iid: iid == str(old)
        table.index.return_value = 0
        table.item.side_effect = lambda _iid, field=None: (
            values if field == "values" else () if field == "tags" else {})
        table.selection.return_value = ()
        pending = queue.Queue()
        pending.put(old)
        gui = SimpleNamespace(
            table=table, live=None, reanalyze_target=None,
            reanalyze_preserve={old},
            force_encode_paths=set(), clean_paths=set(), file_selection={},
            dropped_entries={old: old}, sample_variants={}, queued_reanalysis={},
            queued_force={}, missing_row_values={}, deleted_backups=set(),
            completed_outputs={},
            inspection_results={old: MediaInspection(
                old, 100, 200, "h264", 1920, 1080, 1_000_000, 60.0, "OK")},
            inspection_errors={},
            inspection_lock=threading.Lock(), inspection_queue=pending,
            inspection_enqueued={old}, inspection_batch=[old],
            inspection_cache=Mock(), events=queue.Queue(), drag_iid=str(old),
            filter_order=[str(old)], filtered_rows={str(old)},
            apply_media_view=Mock(),
            _refresh_force_all_heading=Mock(),
            _refresh_encode_all_heading=Mock(),
            _refresh_clean_all_heading=Mock(),
            _refresh_missing_button=Mock())
        gui.events.put(("inspection_result", old, gui.inspection_results[old],
                        "", 1, 1, 0))

        OptimizerGUI.paths_renamed(gui, {old: new})

        self.assertEqual(gui.inspection_queue.get_nowait(), new)
        self.assertEqual(gui.inspection_enqueued, {new})
        self.assertEqual(gui.inspection_batch, [new])
        self.assertEqual(gui.reanalyze_preserve, {new})
        self.assertEqual(gui.dropped_entries, {new: new})
        self.assertEqual(gui.drag_iid, str(new))
        self.assertNotIn(old, gui.inspection_results)
        self.assertEqual(gui.inspection_results[new].path, new)
        migrated_event = gui.events.get_nowait()
        self.assertEqual(migrated_event[1], new)
        self.assertEqual(migrated_event[2].path, new)
        gui.inspection_cache.rename.assert_called_once_with(old, new)
        gui.apply_media_view.assert_called_once_with()

    def test_completed_output_becomes_missing_and_can_return(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (root / "Source.mp4").resolve()
            output = (root / "Source.mkv").resolve()
            moved = (root / "Moved.mkv").resolve()
            source.write_bytes(b"source")
            queue = LiveQueue(root, 0)
            queue.refresh()
            queue.mark_done(source, "Completed", "Finished")
            output.write_bytes(b"output")
            output.rename(moved)
            queue.refresh(existence_paths={source: output})
            self.assertEqual(queue.items[source].status, "Missing")
            self.assertEqual(queue.items[source].status_before_missing, "Completed")
            moved.rename(output)
            queue.refresh(existence_paths={source: output})
            self.assertEqual(queue.items[source].status, "Completed")
            self.assertEqual(queue.items[source].detail, "Finished")

    def test_pending_clean_request_follows_renamed_source_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = (root / "Old.mp4").resolve()
            new = (root / "New.mp4").resolve()
            old.write_bytes(b"video")
            coordinator = CleanCoordinator()
            coordinator.request(old, "Waiting", True,
                                planned_output=root / "Old.mkv")
            old.rename(new)
            coordinator.paths_renamed({old: new})
            self.assertIn(new, coordinator.pending)
            self.assertEqual(coordinator.pending_display[new], root / "New.mkv")
            self.assertIn((root / "New.mkv").resolve(), coordinator.visible)

    def test_ready_clean_request_follows_renamed_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = (root / "Old.mkv").resolve()
            new = (root / "New.mkv").resolve()
            old.write_bytes(b"video")
            coordinator = CleanCoordinator()
            coordinator.request(old, "Completed", False)
            old.rename(new)
            coordinator.paths_renamed({old: new})
            self.assertEqual(list(coordinator.ready), [new])
            self.assertIn(new, coordinator.visible)

    def test_combined_app_has_independent_rename_tab_and_drop_target(self):
        source = inspect.getsource(CombinedApp)
        self.assertIn('notebook.add(rename_tab, text="Rename")', source)
        self.assertIn("self.renamer.table.drop_target_register", source)


if __name__ == "__main__":
    unittest.main()
