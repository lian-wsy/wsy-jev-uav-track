"""Read-only integrity inventory; exit zero is NOT a PASS data admission."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jev_uav_track.decision_dataset_audit import audit_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    result = audit_dataset(args.root)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['integrity_status'] == 'CHECKED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
