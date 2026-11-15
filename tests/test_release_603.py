import json
import logging
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_optimizer import tab_logging
from video_optimizer.encoder import _run_handbrake_with_progress, encode
from video_optimizer.gui import OptimizerGUI
from video_optimizer.models import MediaInfo, Stream
from video_optimizer.queueing import LiveQueue, FileState
from video_optimizer.rename_gui import RenameOptions, proposed_path, transform_stem, validate_rename_plan
from video_optimizer.retained import discover, record, record_path
from video_optimizer.config import Config

class Release603Tests(unittest.TestCase):
    def test_whole_filename_preserves_extensions_and_numbering(self):
        options=RenameOptions(whole_filename=True,replacement='New Movie',find='ignored')
        self.assertEqual(proposed_path(Path('/movies/Old.avi'),options,0),Path('/movies/New Movie.avi'))
        self.assertEqual(transform_stem('old',replace(options, numbering=True),1),'02 New Movie')
        self.assertEqual(transform_stem('OLD',RenameOptions(find='old',replacement='new')),'new')

    def test_whole_filename_duplicates_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=root/'one.mkv';b=root/'two.mkv'
            a.write_bytes(b'1');b.write_bytes(b'2')
            options=RenameOptions(whole_filename=True,replacement='Same')
            with self.assertRaises(ValueError):
                validate_rename_plan({a:proposed_path(a,options),b:proposed_path(b,options)})

    def test_waiting_audio_can_change_while_other_file_encodes(self):
        live=LiveQueue(Path('/movies'),0);active=Path('/movies/active.mkv');waiting=Path('/movies/waiting.mkv')
        live.items[active]=FileState(active,status='Encoding')
        live.items[waiting]=FileState(waiting,status='Waiting')
        ui=SimpleNamespace(live=live,running=True,audio_overrides={},progressive_samples={},_log=Mock())
        OptimizerGUI._set_audio_override(ui,[active,waiting],'all')
        self.assertNotIn(active,ui.audio_overrides)
        self.assertEqual(ui.audio_overrides[waiting],'all')
        self.assertEqual(live.claim_waiting().path,waiting)
        OptimizerGUI._set_audio_override(ui,[waiting],'first_english')
        self.assertEqual(ui.audio_overrides[waiting],'all')

    def test_retained_record_recovers_after_restart_and_blocks_live_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'movie.avi';source.write_bytes(b'original')
            temp=root/'.video-optimizer-movie-1234abcd.mkv';temp.write_bytes(b'partial')
            record(temp,source,'active')
            self.assertEqual(discover(source,[root]),[])
            record(temp,source,'retained')
            self.assertEqual(discover(source,[root]),[temp])
            self.assertEqual(discover(root/'another.avi',[root]),[])

    def test_legacy_matching_is_unambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'movie.avi';source.write_bytes(b'1')
            temp=root/'.video-optimizer-movie-1234abcd.mkv';temp.write_bytes(b'partial')
            with patch.dict('sys.modules', {'psutil': SimpleNamespace(process_iter=lambda *_: [])}):
                self.assertEqual(discover(source,[root]),[temp])
            (root/'movie.mp4').write_bytes(b'2')
            self.assertEqual(discover(source,[root]),[])

    def test_handbrake_failure_contains_error_excerpt(self):
        process=Mock();process.stdout=iter(['Decoder failed at frame 120\n','Failed to read input\n']);process.wait.return_value=4
        with patch('video_optimizer.encoder.subprocess.Popen',return_value=process), patch('video_optimizer.encoder.PauseController') as control, patch('video_optimizer.encoder.write_log'):
            control.return_value.cancelled=False
            with self.assertRaisesRegex(RuntimeError,'exit code 4') as caught:
                _run_handbrake_with_progress(['HandBrakeCLI'],100,'movie',Path('temp.mkv'))
            self.assertIn('Decoder failed at frame 120',str(caught.exception))
            self.assertIn('encoder.log',str(caught.exception))

    def test_partial_encode_is_registered_without_modifying_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'movie.avi';source.write_bytes(b'original')
            media=MediaInfo(source,100,None,8,(Stream(0,'video','h264',width=1920,height=1080),))
            def failure(command,*args):
                temp=Path(command[command.index('--output')+1]);temp.write_bytes(b'partial')
                raise RuntimeError('HandBrake exit 4')
            with patch('video_optimizer.encoder._run_handbrake_with_progress',side_effect=failure):
                with self.assertRaises(RuntimeError) as caught: encode(media,root,root,Config(),True)
            temp=caught.exception.retained_output
            self.assertEqual(temp.read_bytes(),b'partial')
            self.assertEqual(source.read_bytes(),b'original')
            self.assertEqual(json.loads(record_path(temp).read_text())['state'],'retained')

    def test_rotating_logs_and_independent_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch('video_optimizer.tab_logging.application_directory',return_value=Path(directory)):
            try:
                tab_logging.save_settings('encoder',1,2)
                tab_logging.save_settings('rename',3,4)
                self.assertEqual(tab_logging.read_settings('encoder'),{'max_mb':1,'backups':2})
                self.assertEqual(tab_logging.read_settings('rename'),{'max_mb':3,'backups':4})
                logger=tab_logging.get_logger('encoder');logger.handlers[0].maxBytes=100
                for n in range(20):logger.info('record %d %s',n,'x'*40)
                files=list((Path(directory)/'logs').glob('encoder.log*'))
                self.assertLessEqual(len(files),3)
                self.assertTrue((Path(directory)/'logs/encoder.log.2').exists())
            finally:
                for tab in ('encoder','rename'):
                    logger=logging.getLogger('video_optimizer.'+tab)
                    for h in list(logger.handlers):logger.removeHandler(h);h.close()
