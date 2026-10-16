import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import transient_worker as worker


def hit(rate=48000):
    t=np.arange(rate//2,dtype=np.float64)/rate
    x=((np.sin(2*np.pi*180*t)+0.2*np.sin(2*np.pi*3710*t))*np.exp(-12*t)).astype(np.float32)
    return np.column_stack((x,-x))


class DetectorTests(unittest.TestCase):
    def test_neutral_gain_partitioning_and_linked_antiphase(self):
        audio=hit()
        np.testing.assert_array_equal(worker.Detector(48000,0,0,0).process(audio),audio)
        reference=worker.Detector(48000,9,-12,0).process(audio)
        for block in [1,7,511,8192]:
            detector=worker.Detector(48000,9,-12,0)
            chunks=[detector.process(audio[start:start+block]) for start in range(0,len(audio),block)]
            np.testing.assert_array_equal(np.concatenate(chunks),reference)
        np.testing.assert_array_equal(reference[:,0],-reference[:,1])
        quiet=worker.Detector(48000,9,-12,0).process(audio*0.01)
        np.testing.assert_allclose(quiet,reference*0.01,rtol=2e-6,atol=1e-9)

    def test_attack_changes_onsets_and_sustain_changes_decay(self):
        audio=hit()
        baseline=np.sum(audio[:480].astype(np.float64)**2)
        boost=worker.Detector(48000,12,0,0).process(audio)
        cut=worker.Detector(48000,-12,0,0).process(audio)
        self.assertGreater(np.sum(boost[:480].astype(np.float64)**2),baseline*2)
        self.assertLess(np.sum(cut[:480].astype(np.float64)**2),baseline*0.5)
        tail=worker.Detector(48000,0,12,0).process(audio)
        self.assertGreater(np.sum(tail[-4800:].astype(np.float64)**2),np.sum(audio[-4800:].astype(np.float64)**2)*2)
        silence=worker.Detector(48000,15,24,24).process(np.zeros((256,1),dtype=np.float32))
        self.assertTrue(np.all(silence==0))


@unittest.skipUnless(os.environ.get("NZRDSP_NATIVE_CLI"),"set NZRDSP_NATIVE_CLI")
class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="transient-lab-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"Unicode ä"
        self.root.mkdir()
        self.runtime=self.root/"runtime.json"
        self.runtime.write_text(json.dumps({"python":sys.executable}),encoding="utf-8")
        self.source=self.root/"hit.wav"
        sf.write(self.source,hit(),48000,subtype="FLOAT")

    def call(self,output,*args,success=True,input=None):
        result=subprocess.run([os.environ["NZRDSP_NATIVE_CLI"],"transient-lab","--runtime",str(self.runtime),"-i",str(input or self.source),"-o",str(output),*map(str,args)],capture_output=True,text=True,encoding="utf-8",timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.assertEqual(result.returncode==0,success,result.stderr)
        return json.loads(result.stdout) if success else result

    def test_real_cli_roundtrip_and_provenance_at_multiple_rates(self):
        for rate in [8000,44100,48000,96000]:
            source=self.root/f"source-{rate}.wav"
            sf.write(source,hit(rate),rate,subtype="PCM_24")
            expected,_=sf.read(source,dtype="float32",always_2d=True)
            output=self.root/f"neutral-{rate}.wav"
            result=self.call(output,input=source)
            actual,actual_rate=sf.read(output,dtype="float32",always_2d=True)
            np.testing.assert_array_equal(actual,expected)
            self.assertEqual(actual_rate,rate)
            self.assertEqual(result["frames"],len(expected))
            self.assertFalse(result["vendor_parity"])
            self.assertEqual(result["latency_samples"],0)
            self.assertEqual(result["wav_sha256"],hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(result,json.loads(Path(str(output)+".json").read_text(encoding="utf-8")))
        for block in [7,512]:
            output=self.root/f"shaped-{block}.wav"
            self.call(output,"--attack-db",9,"--sustain-db",-12,"--block",block)
        np.testing.assert_array_equal(sf.read(self.root/"shaped-7.wav",dtype="float32")[0],sf.read(self.root/"shaped-512.wav",dtype="float32")[0])

    def test_mono_gain_and_rejected_writes_preserve_existing_data(self):
        mono=self.root/"mono.wav"
        sf.write(mono,hit()[:,0],48000,subtype="FLOAT")
        output=self.root/"gain.wav"
        self.call(output,"--output-db",20*math.log10(2),input=mono)
        np.testing.assert_allclose(sf.read(output,dtype="float32")[0],sf.read(mono,dtype="float32")[0]*2,rtol=1e-7,atol=1e-8)
        original=output.read_bytes();sidecar=Path(str(output)+".json").read_bytes()
        self.call(output,success=False)
        self.assertEqual(output.read_bytes(),original)
        self.assertEqual(Path(str(output)+".json").read_bytes(),sidecar)
        for key,value in [("--attack-db","NaN"),("--attack-db","16"),("--sustain-db","-25"),("--output-db","25")]:
            rejected=self.root/"rejected.wav"
            self.call(rejected,key,value,success=False)
            self.assertFalse(rejected.exists())
            self.assertFalse(Path(str(rejected)+".json").exists())
        invalid=self.root/"nan.wav"
        sf.write(invalid,np.array([0,np.nan,1],dtype=np.float32),48000,subtype="FLOAT")
        self.call(self.root/"nan-output.wav",input=invalid,success=False)
        self.assertFalse((self.root/"nan-output.wav").exists())

    def test_failed_final_publication_removes_only_its_new_manifest(self):
        output=self.root/"failed.wav"
        original=self.source.read_bytes()
        original_publish=worker.publish
        def fail_audio(source,destination):
            if destination==output: raise OSError("synthetic publication failure")
            original_publish(source,destination)
        with patch.object(worker,"publish",side_effect=fail_audio):
            with self.assertRaises(OSError):
                worker.execute({"input":str(self.source),"output":str(output),"block":512,"attack_db":0,"sustain_db":0,"output_db":0})
        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output)+".json").exists())
        self.assertEqual(self.source.read_bytes(),original)


if __name__=="__main__": unittest.main()
