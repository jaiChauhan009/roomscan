"""Select and boot an available iPhone or iPad; print its id for GitHub Actions."""
import argparse
import json
import re
import subprocess
import sys

def choose_device(devices, family='iphone'):
    prefix = 'iPad' if family == 'ipad' else 'iPhone'
    candidates = [(runtime, device) for runtime, entries in devices.items() if 'iOS' in runtime
                  for device in entries if device['name'].startswith(prefix) and device.get('isAvailable', True)]
    if not candidates:
        raise RuntimeError(f'No available {prefix} simulator; install an iOS runtime before testing')
    # Prefer the newest installed runtime; keep the historical iPhone 16 fixture when available.
    return max(candidates, key=lambda p: (tuple(int(x) for x in re.findall(r'\d+', p[0])),
                                         p[1]['name'] == 'iPhone 16', p[1]['name']))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--family', choices=['iphone', 'ipad'], default='iphone')
    args = parser.parse_args()
    data = json.loads(subprocess.check_output(['xcrun', 'simctl', 'list', 'devices', 'available', '-j']))['devices']
    runtime, device = choose_device(data, args.family)
    print(f"simulator: {device['name']} ({runtime})", file=sys.stderr)
    if device.get('state') != 'Booted':
        subprocess.run(['xcrun', 'simctl', 'boot', device['udid']], check=True)
    subprocess.run(['xcrun', 'simctl', 'bootstatus', device['udid'], '-b'], check=True, stdout=sys.stderr)
    print(f"udid={device['udid']}")


if __name__ == '__main__':
    main()
