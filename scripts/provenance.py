"""Record exact source, patches, toolchain and both native payload digests."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
info=json.loads((root/'scripts/build-config.json').read_text())
info['go_image_role']='bootstrap'
info['go_toolchain_install']={
 'helper_path':'scripts/install-go-toolchain.sh',
 'helper_sha256':digest(root/'scripts/install-go-toolchain.sh'),
 'sources':json.loads((root/'scripts/go-toolchain-sources.json').read_text()),
 'selected_sdk':json.loads((root/'scripts/go-toolchain-selected.json').read_text()),
}
if info.get('patch'):info['patch_sha256']=digest(root/info['patch'])
if 'driver' in info and info['driver'].get('patch'):info['driver']['patch_sha256']=digest(root/info['driver']['patch'])
if (root/'native/main.go').exists():info['source_sha256']=digest(root/'native/main.go')
info['approved_profiles']={'patch':'scripts/patches/0000-approved-prebuilt-profiles.patch','sha256':digest(root/'scripts/patches/0000-approved-prebuilt-profiles.patch')}
info['binaries']={}
for target in ('linux-amd64','linux-arm64'):
 p=root/'bin'/target/'toolbox';info['binaries'][target]={'sha256':digest(p),'size':p.stat().st_size}
(root/'toolbox-provenance.json').write_text(json.dumps(info,indent=2)+'\n')
