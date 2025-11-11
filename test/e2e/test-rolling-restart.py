#!/usr/bin/env python3
"""
Rolling Restart Test for MariaDB Cluster with ProxySQL

This script performs a rolling restart of MariaDB pods and verifies:
1. Test client write failures during restart
2. Data consistency across all replicas after restart
"""

import subprocess
import json
import time
import re
import sys
from typing import Dict, Tuple


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color


def run_kubectl(args: list, capture_output=True) -> subprocess.CompletedProcess:
    """Run kubectl command and return result."""
    cmd = ['kubectl'] + args
    return subprocess.run(cmd, capture_output=capture_output, text=True)


def get_test_client_pod(namespace: str) -> str:
    """Get the test client pod name."""
    result = run_kubectl([
        'get', 'pod', '-n', namespace,
        '-l', 'app.kubernetes.io/name=mariadb-test-client',
        '-o', 'jsonpath={.items[0].metadata.name}'
    ])
    return result.stdout.strip()


def parse_client_stats(logs: str) -> Dict[str, int]:
    """Parse test client statistics from logs."""
    stats = {}

    # Find the last statistics block
    lines = logs.strip().split('\n')
    for line in lines:
        if 'Current Sequence:' in line:
            stats['sequence'] = int(line.split()[-1])
        elif 'Successful Writes:' in line:
            stats['successful'] = int(line.split()[-1])
        elif 'Failed Writes:' in line:
            stats['failed'] = int(line.split()[-1])
        elif 'Connection Errors:' in line:
            stats['connection_errors'] = int(line.split()[-1])

    return stats


def get_client_stats(namespace: str, pod: str) -> Dict[str, int]:
    """Get current test client statistics."""
    result = run_kubectl(['logs', '-n', namespace, pod, '--tail=100'])
    return parse_client_stats(result.stdout)


def get_gtid_positions(namespace: str, statefulset: str, replicas: int) -> Dict[str, str]:
    """Get GTID positions for all MariaDB pods."""
    positions = {}

    for i in range(replicas):
        pod = f"{statefulset}-{i}"
        result = run_kubectl([
            'exec', '-n', namespace, pod, '-c', 'mariadb', '--',
            'mariadb', '-uroot', '-pmariadb-root-password',
            '-e', 'SELECT @@global.gtid_current_pos;', '-sN'
        ])

        if result.returncode == 0:
            positions[pod] = result.stdout.strip()

    return positions


def get_data_stats(namespace: str, statefulset: str, replicas: int) -> Dict[str, Tuple[int, int]]:
    """Get row count and max sequence for all MariaDB pods."""
    stats = {}

    for i in range(replicas):
        pod = f"{statefulset}-{i}"
        result = run_kubectl([
            'exec', '-n', namespace, pod, '-c', 'mariadb', '--',
            'mariadb', '-uroot', '-pmariadb-root-password', '-D', 'test',
            '-e', 'SELECT COUNT(*) as row_count, MAX(sequence) as max_seq FROM failover_test;', '-sN'
        ])

        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) == 2:
                rows = int(parts[0])
                max_seq = int(parts[1])
                stats[pod] = (rows, max_seq)

    return stats


def wait_for_rollout(namespace: str, statefulset: str, replicas: int, max_wait: int = 300):
    """Wait for StatefulSet rollout to complete."""
    elapsed = 0
    seen_not_ready = False  # Track if we've seen pods restarting

    while elapsed < max_wait:
        result = run_kubectl([
            'get', 'statefulset', '-n', namespace, statefulset, '-o', 'json'
        ])

        if result.returncode == 0:
            sts = json.loads(result.stdout)
            status = sts.get('status', {})

            updated = status.get('updatedReplicas', 0)
            ready = status.get('readyReplicas', 0)
            current_revision = status.get('currentRevision', '')
            update_revision = status.get('updateRevision', '')

            print(f"  StatefulSet status: Updated={updated}/{replicas}, Ready={ready}/{replicas}")

            # Show pod status
            pod_result = run_kubectl([
                'get', 'pods', '-n', namespace,
                '-l', 'app.kubernetes.io/name=mariadb',
                '-o', 'json'
            ])

            if pod_result.returncode == 0:
                pods = json.loads(pod_result.stdout)
                for item in pods.get('items', []):
                    name = item['metadata']['name']
                    if statefulset in name:
                        phase = item['status']['phase']
                        ready_status = '0/0'
                        for condition in item['status'].get('conditions', []):
                            if condition['type'] == 'Ready':
                                ready_status = '1/1' if condition['status'] == 'True' else '0/1'
                                if condition['status'] != 'True':
                                    seen_not_ready = True
                        print(f"    {name:20s} {phase:15s} {ready_status}")

            # Check if rollout is complete
            # We need to see at least one pod not ready (meaning restart happened)
            # Then all pods updated and ready, and revisions match
            if seen_not_ready and updated == replicas and ready == replicas and current_revision == update_revision:
                print(f"  {Colors.GREEN}✓ All pods restarted and ready!{Colors.NC}")
                return True

            # If pods are not ready, we've seen the restart happening
            if ready < replicas:
                seen_not_ready = True

        time.sleep(5)
        elapsed += 5

    print(f"  {Colors.RED}✗ Rollout timed out{Colors.NC}")
    return False


def main():
    # Configuration
    namespace = "mariadb"
    statefulset = "mariadb-cluster"
    replicas = 3

    print(f"{Colors.GREEN}=== Rolling Restart Test for MariaDB Cluster ==={Colors.NC}")

    # Get test client pod
    test_client = get_test_client_pod(namespace)
    print(f"Namespace: {namespace}")
    print(f"StatefulSet: {statefulset}")
    print(f"Test Client: {test_client}")
    print()

    # Step 1: Get initial statistics
    print(f"{Colors.YELLOW}[1/6] Getting initial test client statistics...{Colors.NC}")
    initial_stats = get_client_stats(namespace, test_client)
    print(f"  Initial Sequence: {initial_stats.get('sequence', 0)}")
    print(f"  Initial Successful Writes: {initial_stats.get('successful', 0)}")
    print(f"  Initial Failed Writes: {initial_stats.get('failed', 0)}")
    print()

    # Step 2: Get initial GTID positions
    print(f"{Colors.YELLOW}[2/6] Recording initial GTID positions...{Colors.NC}")
    initial_gtid = get_gtid_positions(namespace, statefulset, replicas)
    for pod, gtid in initial_gtid.items():
        print(f"  {pod}: {gtid}")
    print()

    # Step 3: Perform rolling restart
    print(f"{Colors.YELLOW}[3/6] Starting rolling restart...{Colors.NC}")
    start_time = time.time()
    result = run_kubectl(['rollout', 'restart', 'statefulset', '-n', namespace, statefulset])
    print(f"  Rollout initiated at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Step 4: Wait for rollout
    print(f"{Colors.YELLOW}[4/6] Monitoring rollout progress...{Colors.NC}")
    print("  This will wait for all pods to be restarted and ready...")

    success = wait_for_rollout(namespace, statefulset, replicas)
    end_time = time.time()
    duration = int(end_time - start_time)

    if not success:
        print(f"{Colors.RED}✗ Rollout failed{Colors.NC}")
        sys.exit(1)

    print(f"  {Colors.GREEN}✓ Rollout completed successfully in {duration}s{Colors.NC}")
    print()

    # Step 5: Analyze test client results
    print(f"{Colors.YELLOW}[5/6] Analyzing test client results during rolling restart...{Colors.NC}")
    time.sleep(2)  # Wait for a couple more writes

    final_stats = get_client_stats(namespace, test_client)

    writes_during = final_stats.get('sequence', 0) - initial_stats.get('sequence', 0)
    failures_during = final_stats.get('failed', 0) - initial_stats.get('failed', 0)
    success_during = final_stats.get('successful', 0) - initial_stats.get('successful', 0)

    print(f"  Statistics during rolling restart:")
    print(f"    Duration: {duration}s")
    print(f"    Total write attempts: {writes_during}")
    print(f"    Successful writes: {success_during}")
    print(f"    Failed writes: {failures_during}")
    print(f"    Connection errors: {final_stats.get('connection_errors', 0)}")

    if writes_during > 0:
        success_rate = (success_during / writes_during) * 100
        print(f"    Success rate: {success_rate:.2f}%")
    print()

    # Step 6: Verify data consistency
    print(f"{Colors.YELLOW}[6/6] Verifying data consistency across all replicas...{Colors.NC}")
    print("  Waiting 5s for replication to catch up...")
    time.sleep(5)

    final_gtid = get_gtid_positions(namespace, statefulset, replicas)
    data_stats = get_data_stats(namespace, statefulset, replicas)

    all_consistent = True
    reference_rows = None
    reference_max = None

    for i in range(replicas):
        pod = f"{statefulset}-{i}"

        gtid = final_gtid.get(pod, 'N/A')
        initial = initial_gtid.get(pod, 'N/A')

        if pod in data_stats:
            rows, max_seq = data_stats[pod]

            print(f"  {pod}:")
            print(f"    GTID: {gtid} (was: {initial})")
            print(f"    Total rows: {rows}")
            print(f"    Max sequence: {max_seq}")

            # Check consistency
            if reference_rows is None:
                reference_rows = rows
                reference_max = max_seq
            else:
                if rows != reference_rows or max_seq != reference_max:
                    all_consistent = False
                    print(f"    {Colors.RED}⚠ Data mismatch detected!{Colors.NC}")

    print()

    # Final verdict
    print(f"{Colors.GREEN}=== Test Results ==={Colors.NC}")

    if failures_during == 0:
        print(f"{Colors.GREEN}✓ No write failures during rolling restart{Colors.NC}")
    else:
        print(f"{Colors.RED}✗ {failures_during} write failures occurred during rolling restart{Colors.NC}")

    if all_consistent:
        print(f"{Colors.GREEN}✓ All replicas have consistent data ({reference_rows} rows, max sequence: {reference_max}){Colors.NC}")
    else:
        print(f"{Colors.RED}✗ Data inconsistency detected across replicas{Colors.NC}")

    print()
    print(f"Test completed at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Exit with appropriate code
    if failures_during == 0 and all_consistent:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
