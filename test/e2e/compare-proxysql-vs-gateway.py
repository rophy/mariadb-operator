#!/usr/bin/env python3
"""
Comparison Test: ProxySQL vs Istio Gateway during Rolling Restart

Monitors TWO test clients simultaneously during rolling restart:
- Client 1: Connects through ProxySQL
- Client 2: Connects through Istio Gateway

Compares write failures and interruptions between the two approaches.
"""

import subprocess
import json
import time
import sys
from typing import Dict, Tuple
from pathlib import Path


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


def run_kubectl(args: list, capture_output=True) -> subprocess.CompletedProcess:
    """Run kubectl command and return result."""
    cmd = ['kubectl'] + args
    return subprocess.run(cmd, capture_output=capture_output, text=True)


def get_test_client_pods(namespace: str) -> Dict[str, str]:
    """Get both test client pod names."""
    pods = {}

    # ProxySQL client
    result = run_kubectl([
        'get', 'pod', '-n', namespace,
        '-l', 'app.kubernetes.io/name=mariadb-test-client-proxysql',
        '-o', 'jsonpath={.items[0].metadata.name}'
    ])
    if result.returncode == 0 and result.stdout:
        pods['proxysql'] = result.stdout.strip()

    # Gateway client
    result = run_kubectl([
        'get', 'pod', '-n', namespace,
        '-l', 'app.kubernetes.io/name=mariadb-test-client-gateway',
        '-o', 'jsonpath={.items[0].metadata.name}'
    ])
    if result.returncode == 0 and result.stdout:
        pods['gateway'] = result.stdout.strip()

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

    return stats


def get_client_stats(namespace: str, pod: str) -> Dict[str, int]:
    """Get current test client statistics."""
    result = run_kubectl(['logs', '-n', namespace, pod, '--tail=100'])
    if result.returncode == 0:
        return parse_client_stats(result.stdout)
    return {}


def wait_for_rollout(namespace: str, statefulset: str, replicas: int, max_wait: int = 300):
    """Wait for StatefulSet rollout to complete."""
    elapsed = 0
    seen_not_ready = False

    print(f"  Monitoring rollout progress...")

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

            if ready < replicas:
                seen_not_ready = True

            # Check if rollout is complete
            if seen_not_ready and updated == replicas and ready == replicas and current_revision == update_revision:
                print(f"  {Colors.GREEN}✓ All pods restarted and ready!{Colors.NC}")
                return True

        time.sleep(5)
        elapsed += 5

    print(f"  {Colors.RED}✗ Rollout timed out{Colors.NC}")
    return False


def main():
    namespace = "mariadb"
    statefulset = "mariadb-cluster"
    replicas = 3

    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print(f"{Colors.GREEN}ProxySQL vs Istio Gateway - Rolling Restart Comparison{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print()

    # Get both test client pods
    print(f"{Colors.YELLOW}[1/5] Identifying test client pods...{Colors.NC}")
    pods = get_test_client_pods(namespace)

    if 'proxysql' not in pods or 'gateway' not in pods:
        print(f"{Colors.RED}Error: Could not find both test client pods{Colors.NC}")
        print(f"  Found: {pods}")
        sys.exit(1)

    print(f"  ProxySQL client: {pods['proxysql']}")
    print(f"  Gateway client:  {pods['gateway']}")
    print()

    # Get initial statistics for both clients
    print(f"{Colors.YELLOW}[2/5] Recording initial statistics...{Colors.NC}")
    initial_stats = {}
    for name, pod in pods.items():
        stats = get_client_stats(namespace, pod)
        initial_stats[name] = stats
        print(f"  {name.capitalize()} client:")
        print(f"    Sequence: {stats.get('sequence', 0)}")
        print(f"    Successful: {stats.get('successful', 0)}")
        print(f"    Failed: {stats.get('failed', 0)}")
    print()

    # Perform rolling restart
    print(f"{Colors.YELLOW}[3/5] Starting rolling restart...{Colors.NC}")
    start_time = time.time()
    result = run_kubectl(['rollout', 'restart', 'statefulset', '-n', namespace, statefulset])
    print(f"  Rollout initiated at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Wait for rollout
    print(f"{Colors.YELLOW}[4/5] Waiting for rollout to complete...{Colors.NC}")
    success = wait_for_rollout(namespace, statefulset, replicas)
    end_time = time.time()
    duration = int(end_time - start_time)

    if not success:
        print(f"{Colors.RED}✗ Rollout failed{Colors.NC}")
        sys.exit(1)

    print(f"  {Colors.GREEN}✓ Rollout completed in {duration}s{Colors.NC}")
    print()

    # Get final statistics for both clients
    print(f"{Colors.YELLOW}[5/5] Analyzing results for both clients...{Colors.NC}")
    print("  Waiting 5s for writes to stabilize...")
    time.sleep(5)

    final_stats = {}
    comparison = {}

    for name, pod in pods.items():
        stats = get_client_stats(namespace, pod)
        final_stats[name] = stats

        # Calculate differences
        writes_during = stats.get('sequence', 0) - initial_stats[name].get('sequence', 0)
        failures_during = stats.get('failed', 0) - initial_stats[name].get('failed', 0)
        success_during = stats.get('successful', 0) - initial_stats[name].get('successful', 0)

        comparison[name] = {
            'writes': writes_during,
            'failures': failures_during,
            'successes': success_during,
            'success_rate': (success_during / writes_during * 100) if writes_during > 0 else 0
        }

    print()
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print(f"{Colors.GREEN}Comparison Results{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print()

    print(f"Rolling restart duration: {duration}s")
    print()

    # Display comparison table
    print(f"{'Metric':<30} {'ProxySQL':<20} {'Istio Gateway':<20}")
    print(f"{'-' * 70}")
    print(f"{'Total write attempts:':<30} {comparison['proxysql']['writes']:<20} {comparison['gateway']['writes']:<20}")
    print(f"{'Successful writes:':<30} {comparison['proxysql']['successes']:<20} {comparison['gateway']['successes']:<20}")
    print(f"{'Failed writes:':<30} {comparison['proxysql']['failures']:<20} {comparison['gateway']['failures']:<20}")
    print(f"{'Success rate:':<30} {comparison['proxysql']['success_rate']:.2f}%{'':<14} {comparison['gateway']['success_rate']:.2f}%{'':<14}")

    print()

    # Determine winner
    proxysql_failures = comparison['proxysql']['failures']
    gateway_failures = comparison['gateway']['failures']

    if proxysql_failures < gateway_failures:
        diff = gateway_failures - proxysql_failures
        print(f"{Colors.GREEN}✓ ProxySQL had {diff} fewer failures than Istio Gateway{Colors.NC}")
    elif gateway_failures < proxysql_failures:
        diff = proxysql_failures - gateway_failures
        print(f"{Colors.YELLOW}⚠ Istio Gateway had {diff} fewer failures than ProxySQL{Colors.NC}")
    else:
        print(f"{Colors.GREEN}✓ Both had the same number of failures{Colors.NC}")

    print()
    print(f"Test completed at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Exit with success
    sys.exit(0)


if __name__ == '__main__':
    main()
