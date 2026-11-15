import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import patch
from video_optimizer.benefit import analyze_benefit
from video_optimizer.config import Config
from video_optimizer.models import MediaInfo, Stream

class SampleDiagnosticsTests(unittest.TestCase):
    def test_failure_preserves_real_cause_and_command(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'Titanic.mkv';source.write_bytes(b'original')
            media=MediaInfo(source,100,1,8,(Stream(0,'video','hevc',width=3840,height=2160),))
            output='encoder initialization failed: diagnostic cause\nwork result = 3\nEncode failed\nHandBrake has exited'
            with patch('video_optimizer.benefit.subprocess.run',return_value=SimpleNamespace(returncode=3,stdout=output)), patch('video_optimizer.benefit.write_log') as log:
                with self.assertRaises(RuntimeError) as caught:
                    analyze_benefit(media,replace(Config(),temp_directory=directory),'HandBrakeCLI')
                self.assertIn('diagnostic cause',str(caught.exception))
                records='\n'.join(str(call) for call in log.call_args_list)
                self.assertIn('Command:',records)
                self.assertIn('start=',records)
                self.assertIn('diagnostic cause',records)
                self.assertEqual(source.read_bytes(),b'original')
