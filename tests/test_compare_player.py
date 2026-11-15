import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from video_optimizer.compare_player import PairControls, CompareWindow, reported_audio_status, comparison_colors
from video_optimizer.gui import comparison_command, OptimizerGUI


class ComparisonTests(unittest.TestCase):
    def controls(self, lengths=(100000, 100000)):
        players = [Mock(), Mock()]
        for player, length in zip(players, lengths):
            player.get_length.return_value = length
            player.audio_get_track.return_value = 1
            player.audio_get_track_description.return_value = [(1, b"Audio")]
        return PairControls(*players)

    def test_shared_seek_moves_both_to_same_timestamp(self):
        controls = self.controls()
        controls.seek(42000)
        for player in controls.players:
            player.set_time.assert_called_once_with(42000)

    def test_offset_changes_backup_time_only(self):
        controls = self.controls()
        controls.offset_ms = 240
        controls.seek(42000)
        controls.players[0].set_time.assert_called_once_with(42240)
        controls.players[1].set_time.assert_called_once_with(42000)

    def test_negative_offset_does_not_seek_before_start(self):
        controls = self.controls()
        controls.offset_ms = -240
        self.assertEqual(controls.seek(0), 240)
        controls.players[0].set_time.assert_called_once_with(0)

    def test_seek_clamps_to_shorter_duration(self):
        controls = self.controls((90000, 100000))
        self.assertEqual(controls.seek(95000), 89999)
        controls.offset_ms = 1000
        self.assertEqual(controls.seek(95000), 88999)

    def test_pause_and_resume_are_explicit_for_both_players(self):
        controls = self.controls()
        controls.pause()
        self.assertFalse(controls.playing)
        controls.resume()
        self.assertTrue(controls.playing)
        for player in controls.players:
            self.assertEqual([call.args for call in player.set_pause.call_args_list], [(1,), (0,)])

    def test_audio_selects_one_track_or_disables_both(self):
        controls = self.controls()
        controls.audio(1)
        controls.players[0].audio_set_track.assert_called_with(-1)
        controls.players[1].audio_set_mute.assert_called_with(False)
        controls.audio(-1)
        for player in controls.players:
            player.audio_set_track.assert_called_with(-1)

    def test_selection_restores_previously_disabled_track(self):
        controls = self.controls()
        controls.players[0].audio_get_track.return_value = 3
        controls.audio(1)
        controls.players[0].audio_get_track.return_value = -1
        controls.audio(0)
        controls.players[0].audio_set_track.assert_called_with(3)

    def test_late_initializing_audio_is_disabled_on_next_update(self):
        controls = self.controls()
        controls.players[0].audio_get_track.return_value = -1
        controls.audio(1)
        controls.players[0].audio_set_track.assert_not_called()
        controls.players[0].audio_get_track.return_value = 2
        controls.apply_audio()
        controls.players[0].audio_set_track.assert_called_with(-1)

    def test_shared_volume_is_preserved_when_switching_sources(self):
        controls = self.controls()
        controls.volume(37)
        controls.players[1].audio_set_volume.assert_called_with(37)
        controls.audio(0)
        controls.players[0].audio_set_volume.assert_called_with(37)

    def test_volume_clamps_without_changing_unselected_output_volume(self):
        controls = self.controls()
        controls.volume(150)
        controls.players[1].audio_set_volume.assert_called_with(100)
        controls.volume(-10)
        controls.players[1].audio_set_volume.assert_called_with(0)
        controls.players[0].audio_set_volume.assert_not_called()

    def test_disabled_track_can_be_selected_after_initialization(self):
        controls = self.controls()
        controls.players[0].audio_get_track.return_value = -1
        controls.players[0].audio_get_track_description.return_value = [(-1, b"Disable"), (4, b"English")]
        controls.audio(0)
        controls.players[0].audio_set_track.assert_called_with(4)

    def test_theme_palettes_match_application_dark_and_light(self):
        self.assertEqual(comparison_colors("dark")["bg"], "#1e1f22")
        self.assertEqual(comparison_colors("light")["bg"], "#f0f0f0")

    def reported_player(self, track=1, mute=0, volume=80, state="State.Playing"):
        player = Mock()
        player.audio_get_track.return_value = track
        player.audio_get_mute.return_value = mute
        player.audio_get_volume.return_value = volume
        player.get_state.return_value = state
        return player

    def test_indicator_reports_vlc_disabled_track(self):
        state, text = reported_audio_status(self.reported_player(track=-1))
        self.assertEqual(state, "off")
        self.assertIn("disabled", text)

    def test_indicator_reports_enabled_playing_track(self):
        state, text = reported_audio_status(self.reported_player())
        self.assertEqual(state, "on")
        self.assertIn("track 1", text)
        self.assertIn("80%", text)

    def test_indicator_uses_reported_mute_and_volume(self):
        for player in (self.reported_player(mute=1), self.reported_player(volume=0)):
            self.assertEqual(reported_audio_status(player)[0], "off")

    def test_indicator_does_not_call_paused_audio_on(self):
        state, text = reported_audio_status(self.reported_player(state="State.Paused"))
        self.assertEqual(state, "off")
        self.assertIn("Paused", text)

    def test_unknown_audio_status_is_not_reported_as_on(self):
        self.assertEqual(reported_audio_status(self.reported_player(mute=-1))[0], "unknown")
        self.assertEqual(reported_audio_status(self.reported_player(volume=-1))[0], "unknown")

    def test_both_reported_active_tracks_are_shown_even_if_selection_should_be_exclusive(self):
        # No selection variable is consulted; an incorrect backend state is
        # visible rather than painted as the intended state.
        players = (self.reported_player(track=2), self.reported_player(track=4))
        self.assertEqual([reported_audio_status(p)[0] for p in players], ["on", "on"])

    def test_mute_toggle_restores_source_and_volume(self):
        controls = self.controls()
        controls.audio(0)
        controls.volume(43)
        controls.toggle_mute()
        self.assertEqual(controls.audio_side, -1)
        controls.toggle_mute()
        self.assertEqual(controls.audio_side, 0)
        self.assertEqual(controls.volume_percent, 43)
        controls.players[0].audio_set_volume.assert_called_with(43)

    def test_listen_choice_while_muted_becomes_next_restore_target(self):
        controls = self.controls()
        controls.toggle_mute()
        controls.audio(0)
        controls.toggle_mute()
        controls.toggle_mute()
        self.assertEqual(controls.audio_side, 0)

    def test_explicit_audio_choice_survives_stale_backend_readback(self):
        controls = self.controls()
        controls.players[1].audio_get_track_description.return_value = [(1, b"English"), (3, b"French")]
        controls.choose_audio_track(1, 3)
        # A subsequent asynchronous read still returns the old track 1.
        controls.apply_audio()
        self.assertEqual(controls.preferred_audio_tracks[1], 3)
        controls.players[1].audio_set_track.assert_called_with(3)

    def test_inactive_audio_choice_does_not_enable_second_source(self):
        controls = self.controls()
        controls.players[0].audio_get_track_description.return_value = [(4, b"Commentary")]
        controls.choose_audio_track(0, 4)
        controls.players[0].audio_set_track.assert_called_with(-1)
        self.assertEqual(controls.audio_side, 1)
        controls.audio(0)
        controls.players[0].audio_set_track.assert_called_with(4)

    def test_invalid_audio_track_is_rejected(self):
        controls = self.controls()
        with self.assertRaises(ValueError):
            controls.choose_audio_track(0, 99)
        self.assertEqual(controls.preferred_audio_tracks, [None, None])

    def test_subtitles_are_independent_and_restored_after_layout_reload(self):
        controls = self.controls()
        controls.players[0].video_get_spu_description.return_value = [(7, b"English")]
        controls.choose_subtitle_track(0, 7)
        controls.choose_subtitle_track(1, -1)
        controls.restore_subtitles()
        controls.players[0].video_set_spu.assert_called_with(7)
        controls.players[1].video_set_spu.assert_called_with(-1)
        self.assertEqual(controls.audio_side, 1)

    def test_failed_subtitle_change_does_not_replace_saved_choice(self):
        controls = self.controls()
        controls.players[0].video_set_spu.return_value = -1
        with self.assertRaises(RuntimeError):
            controls.choose_subtitle_track(0, -1)
        self.assertIsNone(controls.subtitle_tracks[0])

    def test_mute_button_highlight_toggles_back(self):
        controls = self.controls()
        window = Mock()
        window.controls = controls
        CompareWindow.mute_both(window)
        CompareWindow.refresh_mute_button(window)
        window.mute_button.configure.assert_called_with(text="Unmute Both", style="Muted.TButton")
        CompareWindow.mute_both(window)
        CompareWindow.refresh_mute_button(window)
        window.mute_button.configure.assert_called_with(text="Mute Both", style="TButton")

    def test_frozen_launch_passes_paths_as_separate_arguments(self):
        backup = Path("D:/Movies/My Film.mkv.bak")
        output = Path("D:/Movies/My Film.mkv")
        with patch.object(sys, "frozen", True, create=True):
            command = comparison_command(backup, output)
        self.assertEqual(command, [sys.executable, "--compare-videos", str(backup), str(output)])

    def test_source_launch_runs_separate_player_module(self):
        with patch.object(sys, "frozen", False, create=True):
            command = comparison_command(Path("movie.mkv.bak"), Path("movie.mkv"))
        self.assertEqual(command[1:3], ["-m", "video_optimizer.compare_player"])

    def test_comparison_launch_does_not_touch_encode_worker(self):
        gui = Mock()
        worker, running = gui.worker, gui.running
        with patch.object(Path, "is_file", return_value=True), \
             patch("video_optimizer.gui.subprocess.Popen") as launch:
            OptimizerGUI._compare_with_backup(gui, Path("movie.mkv.bak"), Path("movie.mkv"))
        launch.assert_called_once()
        self.assertIs(gui.worker, worker)
        self.assertIs(gui.running, running)

    def test_layout_stops_players_before_destroying_old_surfaces(self):
        events = []
        players = [Mock(), Mock()]
        for player in players:
            player.stop.side_effect = lambda: events.append("stop")
            player.get_time.return_value = 12000
        surfaces = [(Mock(), Mock()), (Mock(), Mock())]
        for container, canvas in surfaces:
            container.destroy.side_effect = lambda: events.append("destroy")
        window = Mock()
        window.loading = False
        window.players = players
        window.panels = surfaces
        window.detached = [Mock(), Mock()]
        window.meters = []
        window.controls.playing = True
        CompareWindow.toggle_layout(window)
        self.assertEqual(events, ["stop", "stop", "destroy", "destroy"])
        window.start_at.assert_called_once_with(12000, True)


if __name__ == "__main__":
    unittest.main()
