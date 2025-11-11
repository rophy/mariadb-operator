#!/usr/bin/env python3
"""
Stress Test: Rolling Restart Reliability

Repeatedly runs the rolling restart test to verify stability and reliability
over multiple iterations. Logs all results and terminates on first failure.
"""

import subprocess
import sys
import os
import time
from datetime import datetime
from pathlib import Path


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


def ensure_logs_directory():
    """Create logs directory if it doesn't exist."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    return log_dir


def run_single_test(iteration: int, log_dir: Path) -> bool:
    """Run a single rolling restart test and log the output."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f"rolling_restart_test_{iteration:03d}_{timestamp}.log"

    print(f"{Colors.BLUE}[{iteration}/100] Running rolling restart test...{Colors.NC}")
    print(f"  Log file: {log_file}")

    # Run the test script
    script_path = Path(__file__).parent / "test-rolling-restart.py"

    with open(log_file, 'w') as f:
        f.write(f"=== Rolling Restart Test - Iteration {iteration} ===\n")
        f.write(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")
        f.flush()

        # Run the test and capture output
        result = subprocess.run(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        # Write output to log
        f.write(result.stdout)
        f.write("\n" + "=" * 60 + "\n")
        f.write(f"Exit code: {result.returncode}\n")
        f.write(f"Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Check if test passed
    if result.returncode == 0:
        print(f"{Colors.GREEN}  ✓ Test passed{Colors.NC}")
        return True
    else:
        print(f"{Colors.RED}  ✗ Test failed with exit code {result.returncode}{Colors.NC}")
        print(f"{Colors.RED}  See log file for details: {log_file}{Colors.NC}")
        return False


def generate_summary_report(log_dir: Path, completed: int, failed: int, start_time: float):
    """Generate a summary report of all test runs."""
    end_time = time.time()
    duration = end_time - start_time

    summary_file = log_dir / f"stress_test_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    with open(summary_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("Rolling Restart Stress Test - Summary Report\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Test execution started: {datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Test execution ended:   {datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total duration:         {duration:.2f} seconds ({duration/60:.2f} minutes)\n\n")

        f.write(f"Tests planned:          100\n")
        f.write(f"Tests completed:        {completed}\n")
        f.write(f"Tests passed:           {completed - failed}\n")
        f.write(f"Tests failed:           {failed}\n")

        if completed > 0:
            success_rate = ((completed - failed) / completed) * 100
            f.write(f"Success rate:           {success_rate:.2f}%\n")
            avg_duration = duration / completed
            f.write(f"Average test duration:  {avg_duration:.2f} seconds\n")

        f.write("\n")

        if failed > 0:
            f.write("Status: FAILED - Test terminated on first failure\n")
        elif completed == 100:
            f.write("Status: SUCCESS - All 100 tests passed\n")
        else:
            f.write("Status: INTERRUPTED - Test run was interrupted\n")

        f.write("\n" + "=" * 80 + "\n")

    print(f"\n{Colors.BLUE}Summary report written to: {summary_file}{Colors.NC}")
    return summary_file


def main():
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print(f"{Colors.GREEN}Rolling Restart Stress Test{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print()
    print("This script will run the rolling restart test 100 times.")
    print("It will terminate on the first failure.")
    print("All test results will be logged to the 'logs/' directory.")
    print()

    # Create logs directory
    log_dir = ensure_logs_directory()
    print(f"Logs directory: {log_dir.absolute()}")
    print()

    # Track statistics
    start_time = time.time()
    completed = 0
    failed = 0

    try:
        for iteration in range(1, 101):
            success = run_single_test(iteration, log_dir)
            completed = iteration

            if not success:
                failed = 1
                print()
                print(f"{Colors.RED}{'=' * 80}{Colors.NC}")
                print(f"{Colors.RED}Test failed on iteration {iteration}{Colors.NC}")
                print(f"{Colors.RED}{'=' * 80}{Colors.NC}")
                break

            # Add a small delay between tests to allow system to stabilize
            if iteration < 100:
                print(f"  Waiting 10s before next test...\n")
                time.sleep(10)

        # If we completed all 100 tests successfully
        if completed == 100 and failed == 0:
            print()
            print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
            print(f"{Colors.GREEN}All 100 tests passed successfully!{Colors.NC}")
            print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")

    except KeyboardInterrupt:
        print()
        print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
        print(f"{Colors.YELLOW}Test interrupted by user{Colors.NC}")
        print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")

    finally:
        # Generate summary report
        print()
        summary_file = generate_summary_report(log_dir, completed, failed, start_time)

        # Print summary to console
        print()
        print(f"{Colors.BLUE}Test Summary:{Colors.NC}")
        print(f"  Tests completed: {completed}/100")
        print(f"  Tests passed:    {completed - failed}")
        print(f"  Tests failed:    {failed}")

        if completed > 0:
            success_rate = ((completed - failed) / completed) * 100
            print(f"  Success rate:    {success_rate:.2f}%")

        print()

        # Exit with appropriate code
        if failed > 0:
            sys.exit(1)
        elif completed == 100:
            sys.exit(0)
        else:
            sys.exit(2)  # Interrupted


if __name__ == '__main__':
    main()
