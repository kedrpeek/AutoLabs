import argparse
import subprocess
import sys
import os
import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed


def run_instance(instance_idx, extra_args):
    """Launch a single AutoLabs.py run in a separate subprocess."""
    script_path = os.path.join(os.path.dirname(__file__), 'AutoLabs-main', 'AutoLabs.py')
    slot_arg = ['--slot', str(instance_idx % 6)]  # slots 0-5 for 3x2 grid
    cmd = [sys.executable, script_path] + slot_arg + extra_args
    print("Launching:", " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, check=False)


def main():
    parser = argparse.ArgumentParser(description="Concurrent runner for AutoLabs account creator.")
    parser.add_argument('--threads', type=int, default=2, help='How many concurrent instances to run (default: 2)')
    parser.add_argument('--instances', type=int, default=2, help='Total number of accounts to create (default: 2)')
    # Everything after a double dash (--) will be forwarded to AutoLabs.py verbatim
    parser.add_argument('forward', nargs=argparse.REMAINDER, help='Extra arguments forwarded to AutoLabs.py')

    args = parser.parse_args()

    # Strip the leading '--' that argparse keeps when using REMAINDER
    extra_args = args.forward
    if extra_args and extra_args[0] == '--':
        extra_args = extra_args[1:]

    print(f"Spawning {args.instances} AutoLabs processes with up to {args.threads} running in parallel...")

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = [executor.submit(run_instance, idx, extra_args) for idx in range(args.instances)]
        for fut in as_completed(futures):
            # We just wait for all tasks; exceptions (if any) will be raised here
            fut.result()

    print("All tasks finished.")


if __name__ == '__main__':
    main() 