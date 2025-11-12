#!/usr/bin/env python3
"""
Primary Pod Failover Test for MariaDB Cluster

This script tests actual failover behavior by:
1. Identifying the current primary pod
2. Recording test client statistics before failover
3. Forcibly deleting the primary pod to trigger failover
4. Monitoring client write failures during failover
5. Verifying new primary was elected
6. Verifying data consistency across all replicas

This tests real failover scenarios, not just rolling restarts.
"""

import subprocess
import json
import time
import sys
from typing import Dict, Tuple, Optional


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color


def run_kubectl(args: list, capture_output=True) -> subprocess.CompletedProcess:
    """Run kubectl command and return result."""
    cmd = ['kubectl'] + args
    return subprocess.run(cmd, capture_output=capture_output, text=True)


def get_current_primary_pod(namespace: str, mariadb_name: str) -> Optional[str]:
    """Get the current primary pod from MariaDB CR status."""
    result = run_kubectl([
        'get', 'mariadb', '-n', namespace, mariadb_name,
        '-o', 'jsonpath={.status.currentPrimaryPodIndex}'
    ])

    if result.returncode == 0 and result.stdout.strip():
        pod_index = int(result.stdout.strip())
        return f"{mariadb_name}-{pod_index}"

    return None


def verify_primary_pod(namespace: str, pod_name: str) -> bool:
    """Verify a pod is actually the primary by checking read_only variable."""
    result = run_kubectl([
        'exec', '-n', namespace, pod_name, '-c', 'mariadb', '--',
        'mariadb', '-uroot', '-pmariadb-root-password',
        '-e', 'SELECT @@read_only;', '-sN'
    ])

    if result.returncode == 0:
        read_only = result.stdout.strip()
        return read_only == '0'  # Primary has read_only=0

    return False


def get_test_client_pods(namespace: str) -> Dict[str, str]:
    """Get all test client pods."""
    result = run_kubectl([
        'get', 'pod', '-n', namespace,
        '-o', 'json'
    ])

    pods = {}
    if result.returncode == 0:
        data = json.loads(result.stdout)
        for item in data.get('items', []):
            name = item['metadata']['name']
            # Only include test client pods
            if 'test-client' not in name:
                continue

            # Determine client type from name
            if 'proxysql' in name:
                pods['proxysql'] = name
            elif 'gateway' in name:
                pods['gateway'] = name
            else:
                pods['direct'] = name

    return pods


def parse_client_stats(logs: str) -> Dict[str, int]:
    """Parse test client statistics from logs."""
    stats = {}

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
        elif 'Total Write Attempts:' in line:
            stats['total_attempts'] = int(line.split()[-1])

    # Set interruptions to 0 since the new client doesn't track this separately
    # (it's included in failed_writes now)
    stats['interruptions'] = 0

    return stats


def get_client_stats(namespace: str, pod: str, since_time: Optional[str] = None) -> Dict[str, int]:
    """Get current test client statistics."""
    if since_time:
        # Get logs since the specified time
        result = run_kubectl(['logs', '-n', namespace, pod, '--since-time', since_time])
    else:
        # Get recent logs
        result = run_kubectl(['logs', '-n', namespace, pod, '--tail=100'])
    return parse_client_stats(result.stdout)


def wait_for_new_primary(namespace: str, mariadb_name: str, old_primary: str, max_wait: int = 60) -> Optional[str]:
    """Wait for a new primary to be elected."""
    print(f"  Waiting for new primary election (max {max_wait}s)...")
    elapsed = 0

    while elapsed < max_wait:
        new_primary = get_current_primary_pod(namespace, mariadb_name)

        if new_primary and new_primary != old_primary:
            # Verify the new primary is actually writable
            if verify_primary_pod(namespace, new_primary):
                print(f"  {Colors.GREEN}✓ New primary elected: {new_primary}{Colors.NC}")
                return new_primary

        time.sleep(2)
        elapsed += 2

    print(f"  {Colors.RED}✗ Timeout waiting for new primary{Colors.NC}")
    return None


def get_data_consistency(namespace: str, mariadb_name: str, replicas: int) -> Dict[str, Tuple[int, int]]:
    """Get row count and max sequence for all MariaDB pods."""
    stats = {}

    for i in range(replicas):
        pod = f"{mariadb_name}-{i}"
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


def verify_no_gaps_or_duplicates(namespace: str, pod: str) -> Dict[str, any]:
    """Verify each client has no sequence gaps or duplicates."""
    result = run_kubectl([
        'exec', '-n', namespace, pod, '-c', 'mariadb', '--',
        'mariadb', '-uroot', '-pmariadb-root-password', '-D', 'test', '-sN', '-e',
        """
        SELECT
            client_id,
            MIN(sequence) as min_seq,
            MAX(sequence) as max_seq,
            COUNT(*) as total_rows,
            MAX(sequence) - MIN(sequence) + 1 as expected_rows,
            COUNT(DISTINCT sequence) as unique_seqs
        FROM failover_test
        GROUP BY client_id
        """
    ])

    if result.returncode != 0:
        return {'error': 'Failed to query database'}

    clients = {}
    for line in result.stdout.strip().split('\n'):
        parts = line.split('\t')
        if len(parts) == 6:
            client_id = parts[0]
            min_seq = int(parts[1])
            max_seq = int(parts[2])
            total_rows = int(parts[3])
            expected_rows = int(parts[4])
            unique_seqs = int(parts[5])

            has_gaps = total_rows != expected_rows
            has_duplicates = unique_seqs != total_rows

            clients[client_id] = {
                'min_seq': min_seq,
                'max_seq': max_seq,
                'total_rows': total_rows,
                'expected_rows': expected_rows,
                'unique_seqs': unique_seqs,
                'has_gaps': has_gaps,
                'has_duplicates': has_duplicates
            }

    return clients


def get_attempt_tracking_stats(namespace: str, pod: str) -> Dict[str, any]:
    """Get max attempt_count and upsert_count statistics."""
    result = run_kubectl([
        'exec', '-n', namespace, pod, '-c', 'mariadb', '--',
        'mariadb', '-uroot', '-pmariadb-root-password', '-D', 'test', '-sN', '-e',
        """
        SELECT
            client_id,
            MAX(attempt_count) as max_attempts,
            MAX(upsert_count) as max_upserts,
            SUM(CASE WHEN upsert_count > 1 THEN 1 ELSE 0 END) as duplicate_writes
        FROM failover_test
        GROUP BY client_id
        """
    ])

    if result.returncode != 0:
        return {'error': 'Failed to query database'}

    clients = {}
    for line in result.stdout.strip().split('\n'):
        parts = line.split('\t')
        if len(parts) == 4:
            client_id = parts[0]
            max_attempts = int(parts[1])
            max_upserts = int(parts[2])
            duplicate_writes = int(parts[3])

            clients[client_id] = {
                'max_attempts': max_attempts,
                'max_upserts': max_upserts,
                'duplicate_writes': duplicate_writes
            }

    return clients


def main():
    # Configuration
    namespace = "mariadb"
    mariadb_name = "mariadb-cluster"
    replicas = 3

    print(f"{Colors.GREEN}{'='*80}{Colors.NC}")
    print(f"{Colors.GREEN}MariaDB Primary Pod Failover Test{Colors.NC}")
    print(f"{Colors.GREEN}{'='*80}{Colors.NC}\n")

    # Step 1: Identify current primary
    print(f"{Colors.YELLOW}[1/7] Identifying current primary pod...{Colors.NC}")
    primary_pod = get_current_primary_pod(namespace, mariadb_name)

    if not primary_pod:
        print(f"{Colors.RED}✗ Failed to identify primary pod{Colors.NC}")
        return 1

    print(f"  Primary pod: {primary_pod}")

    # Verify it's actually the primary
    if not verify_primary_pod(namespace, primary_pod):
        print(f"{Colors.RED}✗ Pod {primary_pod} is not actually primary (read_only=1){Colors.NC}")
        return 1

    print(f"  {Colors.GREEN}✓ Verified {primary_pod} is primary (read_only=0){Colors.NC}\n")

    # Step 2: Get test client pods
    print(f"{Colors.YELLOW}[2/7] Identifying test client pods...{Colors.NC}")
    client_pods = get_test_client_pods(namespace)

    if not client_pods:
        print(f"  {Colors.YELLOW}⚠ No test clients found, continuing without client monitoring{Colors.NC}\n")
    else:
        for client_type, pod_name in client_pods.items():
            print(f"  {client_type.capitalize()} client: {pod_name}")
        print()

    # Step 3: Record initial client statistics
    print(f"{Colors.YELLOW}[3/7] Recording initial client statistics...{Colors.NC}")
    initial_stats = {}

    for client_type, pod_name in client_pods.items():
        stats = get_client_stats(namespace, pod_name)
        initial_stats[client_type] = stats
        print(f"  {client_type.capitalize()} client:")
        print(f"    Sequence: {stats.get('sequence', 0)}")
        print(f"    Successful: {stats.get('successful', 0)}")
        print(f"    Failed: {stats.get('failed', 0)}")
    print()

    # Step 4: Delete primary pod to trigger failover
    print(f"{Colors.YELLOW}[4/7] Deleting primary pod to trigger failover...{Colors.NC}")
    print(f"  Deleting {primary_pod}...")

    # Start timing the failover and get RFC3339 timestamp for log filtering
    import datetime
    failover_start_time = time.time()
    failover_start_timestamp = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')

    delete_result = run_kubectl(['delete', 'pod', '-n', namespace, primary_pod])

    if delete_result.returncode != 0:
        print(f"{Colors.RED}✗ Failed to delete pod{Colors.NC}")
        return 1

    print(f"  {Colors.GREEN}✓ Pod deletion initiated at {failover_start_timestamp}{Colors.NC}\n")

    # Step 5: Wait for new primary election
    print(f"{Colors.YELLOW}[5/7] Waiting for new primary election...{Colors.NC}")
    new_primary = wait_for_new_primary(namespace, mariadb_name, primary_pod)

    if not new_primary:
        print(f"{Colors.RED}✗ Failover failed{Colors.NC}")
        return 1

    failover_election_time = time.time() - failover_start_time
    print(f"  Failover election completed in {failover_election_time:.1f}s\n")

    # Step 6: Analyze client failures during failover
    print(f"{Colors.YELLOW}[6/7] Analyzing client behavior during failover...{Colors.NC}")
    print(f"  Waiting 5s for writes to stabilize...\n")
    time.sleep(5)

    # Total failover duration from deletion to recovery
    total_failover_time = time.time() - failover_start_time

    failover_summary = {}

    for client_type, pod_name in client_pods.items():
        final_stats = get_client_stats(namespace, pod_name)
        initial = initial_stats.get(client_type, {})

        writes_during = final_stats.get('sequence', 0) - initial.get('sequence', 0)
        failures_during = final_stats.get('failed', 0) - initial.get('failed', 0)
        success_during = final_stats.get('successful', 0) - initial.get('successful', 0)
        interruptions = final_stats.get('interruptions', 0) - initial.get('interruptions', 0)
        conn_errors = final_stats.get('connection_errors', 0) - initial.get('connection_errors', 0)

        success_rate = (success_during / writes_during * 100) if writes_during > 0 else 0

        # Calculate interruption duration: failures * write_interval
        # Test clients write every 0.5 seconds
        write_interval = 0.5
        interruption_duration = failures_during * write_interval

        failover_summary[client_type] = {
            'writes': writes_during,
            'failures': failures_during,
            'successes': success_during,
            'interruptions': interruptions,
            'connection_errors': conn_errors,
            'success_rate': success_rate,
            'interruption_duration': interruption_duration
        }

        print(f"  {client_type.capitalize()} client:")
        print(f"    Write attempts: {writes_during}")
        print(f"    Successful: {success_during}")
        print(f"    Failed: {failures_during}")
        print(f"      - Connection interruptions: {interruptions}")
        print(f"      - Connection errors: {conn_errors}")
        print(f"    Success rate: {success_rate:.2f}%")
        print(f"    Interruption duration: ~{interruption_duration:.1f}s")
        print()

    # Step 7: Verify data consistency and integrity
    print(f"{Colors.YELLOW}[7/7] Verifying data consistency and integrity...{Colors.NC}")
    print(f"  Waiting 5s for replication to catch up...\n")
    time.sleep(5)

    # Check replica consistency
    data_stats = get_data_consistency(namespace, mariadb_name, replicas)

    reference_rows = None
    reference_max = None
    all_consistent = True
    max_diff = 0

    for pod, (rows, max_seq) in sorted(data_stats.items()):
        print(f"  {pod}:")
        print(f"    Rows: {rows}, Max sequence: {max_seq}")

        if reference_rows is None:
            reference_rows = rows
            reference_max = max_seq
        else:
            diff = abs(rows - reference_rows)
            max_diff = max(max_diff, diff)
            # Allow up to 3 rows difference due to continuous writes
            if diff > 3:
                all_consistent = False

    print()

    if all_consistent:
        print(f"{Colors.GREEN}✓ Data is consistent across all replicas (max diff: {max_diff} rows){Colors.NC}\n")
    else:
        print(f"{Colors.RED}✗ Data inconsistency detected (max diff: {max_diff} rows){Colors.NC}\n")

    # Verify no gaps or duplicates (using new primary)
    print(f"  Checking for sequence gaps and duplicates...")
    gap_dup_stats = verify_no_gaps_or_duplicates(namespace, new_primary)

    has_gaps = False
    has_duplicates = False

    for client_id, stats in gap_dup_stats.items():
        if 'error' in stats:
            print(f"  {Colors.RED}✗ Error checking {client_id}: {stats['error']}{Colors.NC}")
            continue

        client_short = client_id.split('-')[-1] if len(client_id) > 40 else client_id

        if stats['has_gaps']:
            has_gaps = True
            gap_count = stats['expected_rows'] - stats['total_rows']
            print(f"  {Colors.RED}✗ {client_short}: GAPS DETECTED - Missing {gap_count} sequences{Colors.NC}")
            print(f"    Range: {stats['min_seq']}-{stats['max_seq']}, Got: {stats['total_rows']}, Expected: {stats['expected_rows']}")
        elif stats['has_duplicates']:
            has_duplicates = True
            dup_count = stats['total_rows'] - stats['unique_seqs']
            print(f"  {Colors.RED}✗ {client_short}: DUPLICATES DETECTED - {dup_count} duplicate sequences{Colors.NC}")
            print(f"    Rows: {stats['total_rows']}, Unique: {stats['unique_seqs']}")
        else:
            print(f"  {Colors.GREEN}✓ {client_short}: No gaps, no duplicates ({stats['total_rows']} sequences){Colors.NC}")

    print()

    # Check attempt tracking stats
    print(f"  Analyzing retry behavior (attempt_count and upsert_count)...")
    tracking_stats = get_attempt_tracking_stats(namespace, new_primary)

    has_upsert_duplicates = False

    for client_id, stats in tracking_stats.items():
        if 'error' in stats:
            print(f"  {Colors.RED}✗ Error checking {client_id}: {stats['error']}{Colors.NC}")
            continue

        client_short = client_id.split('-')[-1] if len(client_id) > 40 else client_id

        max_attempts = stats['max_attempts']
        max_upserts = stats['max_upserts']
        dup_writes = stats['duplicate_writes']

        # Log max attempt_count
        if max_attempts > 1:
            print(f"  {Colors.YELLOW}→ {client_short}: Max retry attempts: {max_attempts} (during failover){Colors.NC}")
        else:
            print(f"  → {client_short}: Max retry attempts: {max_attempts} (no retries needed)")

        # Check for upsert_count > 1 (WARNING - indicates uncertain transaction state)
        if max_upserts > 1 or dup_writes > 0:
            has_upsert_duplicates = True
            print(f"  {Colors.RED}⚠⚠⚠ WARNING: {client_short} has upsert_count > 1!{Colors.NC}")
            print(f"      Max upsert_count: {max_upserts}")
            print(f"      Rows with duplicates: {dup_writes}")
            print(f"      This indicates write succeeded but ACK was lost (idempotency saved us!)")

    print()

    # Summary
    print(f"\n{Colors.GREEN}{'='*80}{Colors.NC}")
    print(f"{Colors.GREEN}Failover Test Summary{Colors.NC}")
    print(f"{Colors.GREEN}{'='*80}{Colors.NC}\n")

    print(f"Failover: {primary_pod} → {new_primary}")
    print(f"Election time: {failover_election_time:.1f}s")
    print(f"Total failover duration: {total_failover_time:.1f}s\n")

    if failover_summary:
        print("Client Behavior During Failover:\n")
        for client_type, stats in failover_summary.items():
            print(f"  {client_type.capitalize()}:")
            print(f"    Failures: {stats['failures']}/{stats['writes']} ({100-stats['success_rate']:.2f}%)")
            print(f"    Interruption duration: ~{stats['interruption_duration']:.1f}s")
            print(f"    Connection interruptions: {stats['interruptions']}")
            print(f"    Connection errors: {stats['connection_errors']}")
        print()

    # Determine overall test result
    test_passed = all_consistent and not has_gaps and not has_duplicates

    if test_passed:
        print(f"{Colors.GREEN}✓ Test PASSED{Colors.NC}")
        if has_upsert_duplicates:
            print(f"{Colors.YELLOW}  Note: upsert_count > 1 detected (idempotency worked as designed){Colors.NC}")
        print()
        return 0
    else:
        print(f"{Colors.RED}✗ Test FAILED{Colors.NC}")
        if not all_consistent:
            print(f"  - Replica inconsistency detected")
        if has_gaps:
            print(f"  - Sequence gaps detected")
        if has_duplicates:
            print(f"  - Duplicate sequences detected")
        print()
        return 1


if __name__ == '__main__':
    sys.exit(main())
