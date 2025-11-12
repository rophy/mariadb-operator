#!/usr/bin/env python3
"""
Stress Test: Repeated Primary Pod Failover

Runs the failover test multiple times to validate failover reliability.
Logs detailed statistics to logs/ directory for analysis.

This script:
1. Runs the failover test N times
2. Terminates on first failure
3. Logs each test result to separate log files
4. Generates a summary report with statistics

Usage:
  ./stress-test-failover.py [num_iterations]

  num_iterations: Number of times to run the failover test (default: 10)
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


def run_single_test(iteration: int, log_dir: Path, script_path: Path) -> Dict:
    """Run a single failover test and log the output."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f"failover_test_{iteration:03d}_{timestamp}.log"

    print(f"{Colors.BLUE}[{iteration}/{num_iterations}] Running failover test...{Colors.NC}")
    print(f"  Log file: {log_file}")

    start_time = time.time()

    with open(log_file, 'w') as f:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        f.write(result.stdout)

    duration = time.time() - start_time

    # Parse test results from output
    success = result.returncode == 0

    # Extract statistics from output
    stats = parse_test_output(result.stdout)
    stats['duration'] = duration
    stats['success'] = success
    stats['log_file'] = str(log_file)

    if success:
        print(f"{Colors.GREEN}  ✓ Test passed{Colors.NC}")
    else:
        print(f"{Colors.RED}  ✗ Test failed{Colors.NC}")

    return stats


def parse_test_output(output: str) -> Dict:
    """Parse statistics from test output."""
    stats = {
        'proxysql_failures': None,
        'proxysql_writes': None,
        'proxysql_interruption_duration': None,
        'gateway_failures': None,
        'gateway_writes': None,
        'gateway_interruption_duration': None,
        'election_time': None,
        'total_failover_duration': None,
    }

    lines = output.split('\n')
    current_client = None

    for line in lines:
        # Detect which client section we're in
        if 'Proxysql:' in line or 'proxysql client:' in line.lower():
            current_client = 'proxysql'
        elif 'Gateway:' in line or 'gateway client:' in line.lower():
            current_client = 'gateway'

        # Parse statistics
        if 'Failures:' in line and '/' in line:
            parts = line.split('/')
            if len(parts) >= 2:
                failures = int(parts[0].split()[-1])
                writes = int(parts[1].split()[0])
                if current_client == 'proxysql':
                    stats['proxysql_failures'] = failures
                    stats['proxysql_writes'] = writes
                elif current_client == 'gateway':
                    stats['gateway_failures'] = failures
                    stats['gateway_writes'] = writes

        if 'Interruption duration:' in line:
            duration_str = line.split('~')[1].split('s')[0]
            duration = float(duration_str)
            if current_client == 'proxysql':
                stats['proxysql_interruption_duration'] = duration
            elif current_client == 'gateway':
                stats['gateway_interruption_duration'] = duration

        if 'Election time:' in line:
            stats['election_time'] = float(line.split(':')[1].strip().rstrip('s'))

        if 'Total failover duration:' in line:
            stats['total_failover_duration'] = float(line.split(':')[1].strip().rstrip('s'))

    return stats


def generate_summary_report(test_results: List[Dict], log_dir: Path, num_iterations: int):
    """Generate a summary report of all test results."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    summary_file = log_dir / f"stress_test_summary_{timestamp}.txt"

    passed = sum(1 for r in test_results if r['success'])
    failed = num_iterations - passed

    # Calculate statistics
    proxysql_stats = {
        'total_failures': 0,
        'total_writes': 0,
        'interruption_durations': [],
    }
    gateway_stats = {
        'total_failures': 0,
        'total_writes': 0,
        'interruption_durations': [],
    }
    election_times = []

    for result in test_results:
        if result.get('proxysql_failures') is not None:
            proxysql_stats['total_failures'] += result['proxysql_failures']
            proxysql_stats['total_writes'] += result['proxysql_writes']
            if result.get('proxysql_interruption_duration'):
                proxysql_stats['interruption_durations'].append(result['proxysql_interruption_duration'])

        if result.get('gateway_failures') is not None:
            gateway_stats['total_failures'] += result['gateway_failures']
            gateway_stats['total_writes'] += result['gateway_writes']
            if result.get('gateway_interruption_duration'):
                gateway_stats['interruption_durations'].append(result['gateway_interruption_duration'])

        if result.get('election_time'):
            election_times.append(result['election_time'])

    with open(summary_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("Failover Stress Test - Summary Report\n")
        f.write("=" * 80 + "\n\n")

        f.write(f"Test execution started: {test_results[0]['log_file'].split('_')[-2] if test_results else 'N/A'}\n")
        f.write(f"Test execution ended:   {test_results[-1]['log_file'].split('_')[-2] if test_results else 'N/A'}\n")
        f.write(f"Total duration:         {sum(r['duration'] for r in test_results):.2f} seconds\n\n")

        f.write(f"Tests planned:          {num_iterations}\n")
        f.write(f"Tests completed:        {len(test_results)}\n")
        f.write(f"Tests passed:           {passed}\n")
        f.write(f"Tests failed:           {failed}\n")
        f.write(f"Success rate:           {(passed/len(test_results)*100):.2f}%\n")
        f.write(f"Average test duration:  {sum(r['duration'] for r in test_results)/len(test_results):.2f} seconds\n\n")

        if election_times:
            f.write(f"Election time stats:\n")
            f.write(f"  Min:     {min(election_times):.1f}s\n")
            f.write(f"  Max:     {max(election_times):.1f}s\n")
            f.write(f"  Average: {sum(election_times)/len(election_times):.1f}s\n\n")

        if proxysql_stats['total_writes'] > 0:
            f.write("ProxySQL Client Statistics:\n")
            f.write(f"  Total write attempts:   {proxysql_stats['total_writes']}\n")
            f.write(f"  Total failures:         {proxysql_stats['total_failures']}\n")
            f.write(f"  Failure rate:           {(proxysql_stats['total_failures']/proxysql_stats['total_writes']*100):.2f}%\n")
            if proxysql_stats['interruption_durations']:
                avg_int = sum(proxysql_stats['interruption_durations']) / len(proxysql_stats['interruption_durations'])
                f.write(f"  Avg interruption:       {avg_int:.1f}s\n")
            f.write("\n")

        if gateway_stats['total_writes'] > 0:
            f.write("Istio Gateway Client Statistics:\n")
            f.write(f"  Total write attempts:   {gateway_stats['total_writes']}\n")
            f.write(f"  Total failures:         {gateway_stats['total_failures']}\n")
            f.write(f"  Failure rate:           {(gateway_stats['total_failures']/gateway_stats['total_writes']*100):.2f}%\n")
            if gateway_stats['interruption_durations']:
                avg_int = sum(gateway_stats['interruption_durations']) / len(gateway_stats['interruption_durations'])
                f.write(f"  Avg interruption:       {avg_int:.1f}s\n")
            f.write("\n")

        if passed == num_iterations:
            f.write("Status: SUCCESS - All tests passed\n")
        else:
            f.write(f"Status: FAILED - {failed} test(s) failed\n")

        f.write("\n" + "=" * 80 + "\n")

    return summary_file


def main():
    global num_iterations

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Run failover stress test multiple times')
    parser.add_argument('num_iterations', type=int, nargs='?', default=10,
                        help='Number of times to run the failover test (default: 10)')
    parser.add_argument('--wait', type=int, default=10,
                        help='Seconds to wait between tests (default: 10)')
    args = parser.parse_args()

    num_iterations = args.num_iterations
    wait_between_tests = args.wait

    print(f"{Colors.GREEN}{'='*80}{Colors.NC}")
    print(f"{Colors.GREEN}Failover Stress Test{Colors.NC}")
    print(f"{Colors.GREEN}{'='*80}{Colors.NC}\n")

    # Setup paths
    script_dir = Path(__file__).parent
    script_path = script_dir / "test-failover.py"
    log_dir = script_dir / "logs"

    if not script_path.exists():
        print(f"{Colors.RED}Error: test-failover.py not found at {script_path}{Colors.NC}")
        return 1

    # Create logs directory
    log_dir.mkdir(exist_ok=True)

    print(f"This script will run the failover test {num_iterations} times.")
    print(f"It will terminate on the first failure.")
    print(f"All test results will be logged to the 'logs/' directory.\n")
    print(f"Logs directory: {log_dir.absolute()}\n")

    # Run tests
    test_results = []

    for i in range(1, num_iterations + 1):
        result = run_single_test(i, log_dir, script_path)
        test_results.append(result)

        if not result['success']:
            print(f"\n{Colors.RED}Test {i} failed. Terminating stress test.{Colors.NC}\n")
            break

        if i < num_iterations:
            print(f"  Waiting {wait_between_tests}s before next test...\n")
            time.sleep(wait_between_tests)

    # Generate summary
    print(f"\n{Colors.GREEN}{'='*80}{Colors.NC}")
    if len(test_results) == num_iterations and all(r['success'] for r in test_results):
        print(f"{Colors.GREEN}All {num_iterations} tests passed successfully!{Colors.NC}")
    else:
        print(f"{Colors.YELLOW}Completed {len(test_results)}/{num_iterations} tests{Colors.NC}")
    print(f"{Colors.GREEN}{'='*80}{Colors.NC}\n")

    summary_file = generate_summary_report(test_results, log_dir, num_iterations)
    print(f"{Colors.BLUE}Summary report written to: {summary_file}{Colors.NC}\n")

    # Print summary stats
    print(f"{Colors.BLUE}Test Summary:{Colors.NC}")
    print(f"  Tests completed: {len(test_results)}/{num_iterations}")
    print(f"  Tests passed:    {sum(1 for r in test_results if r['success'])}")
    print(f"  Tests failed:    {sum(1 for r in test_results if not r['success'])}")
    print(f"  Success rate:    {(sum(1 for r in test_results if r['success'])/len(test_results)*100):.2f}%\n")

    return 0 if all(r['success'] for r in test_results) else 1


if __name__ == '__main__':
    sys.exit(main())
