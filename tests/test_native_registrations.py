"""Probe actual native registration surface without credentials or network."""
from pathlib import Path
import json,platform,subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('kind,type_', [('source','postgres'),('resource','file'),('resource','text'),('prompt','custom')])
def test_unapproved_native_registration_is_unavailable(tmp_path,kind,type_):
 arch='arm64' if platform.machine().lower() in {'arm64','aarch64'} else 'amd64'
 config=tmp_path/'config.yaml';config.write_text(json.dumps({'kind':kind,'name':'probe','type':type_}))
 result=subprocess.run([str(ROOT/'bin'/('linux-'+arch)/'toolbox'),'--config',str(config),'--stdio','--disable-reload','--disable-version-check'],input=b'',capture_output=True,timeout=10,env={'LANG':'C.UTF-8'})
 assert result.returncode != 0,(result.stdout,result.stderr)
 assert ('unknown '+kind+' type').encode() in result.stderr+result.stdout,(result.stdout,result.stderr)
