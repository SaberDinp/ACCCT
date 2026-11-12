#!/usr/bin/env bash
# The installed DEB must provide dependencies, launchers and sandbox setup itself.
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != true || "$(uname -s)" != Linux || "$(id -u)" == 0 ]]; then
  echo 'Run this installation check as the normal user on a disposable Linux Actions runner.' >&2
  exit 1
fi
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_root"
version="$(node -p 'require("./desktop/package.json").version')"
architecture="$(dpkg --print-architecture)"
case "$architecture" in amd64|arm64) ;; *) echo "Unexpected architecture: $architecture" >&2; exit 1 ;; esac
package="$project_root/dist/ACCC-Telemetry-$version-linux-$architecture.deb"
[[ "$(dpkg-deb --field "$package" Package)" == accc-telemetry ]]
[[ "$(dpkg-deb --field "$package" Version)" == "$version" ]]
[[ "$(dpkg-deb --field "$package" Architecture)" == "$architecture" ]]
sudo apt-get install -y --no-install-recommends "$package"
[[ "$(dpkg-query -W -f='${Status}' accc-telemetry)" == 'install ok installed' ]]

executable='/opt/ACCC Telemetry/accc-telemetry'
[[ -x "$executable" ]]
[[ "$(readlink -f /usr/bin/accc-telemetry)" == "$executable" ]]
# DEB postinst chooses user namespaces/AppArmor or the root-owned setuid helper.
# Do not change permissions, sysctls, AppArmor policy or Chromium flags here.
sandbox='/opt/ACCC Telemetry/chrome-sandbox'
[[ "$(stat -c '%u:%g' "$sandbox")" == '0:0' ]]
case "$(stat -c '%a' "$sandbox")" in 755|4755) ;; *) echo 'Unexpected sandbox permissions.' >&2; exit 1 ;; esac
python3 - "$package" "$version" "$architecture" <<'PY'
import configparser
import json
import os
from pathlib import Path
import shlex
import sys

executable = '/opt/ACCC Telemetry/accc-telemetry'
entry = configparser.ConfigParser(interpolation=None)
entry.read('/usr/share/applications/accc-telemetry.desktop')
desktop = entry['Desktop Entry']
assert desktop['Name'] == 'ACCC Telemetry'
assert desktop['Type'] == 'Application'
assert shlex.split(desktop['Exec'])[0] == executable
sandbox = Path('/opt/ACCC Telemetry/chrome-sandbox').stat()
report = {'installer': sys.argv[1], 'version': sys.argv[2], 'architecture': sys.argv[3],
          'package_status': 'install ok installed', 'executable': executable,
          'desktop_entry': dict(desktop), 'sandbox_owner': sandbox.st_uid,
          'sandbox_mode': oct(sandbox.st_mode & 0o7777), 'manual_sandbox_changes': False}
folder = Path('artifacts/desktop-package')
folder.mkdir(parents=True, exist_ok=True)
(folder / 'linux-installer.json').write_text(json.dumps(report, indent=2) + '\n')
with open(os.environ['GITHUB_ENV'], 'a') as output:
    output.write(f'ACCC_INSTALLED_EXECUTABLE={executable}\n')
    output.write('ACCC_INSTALLED_BACKEND_ROOT=/opt/ACCC Telemetry/resources/backend\n')
PY
printf 'Installed ACCC Telemetry %s (%s) with desktop launcher and package-managed sandbox.\n' "$version" "$architecture"
