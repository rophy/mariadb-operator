# MariaDB Failover Test Client

A Python-based test client that continuously writes records with an increasing sequence number to a MariaDB database. Designed to verify failover behavior and detect data consistency issues during database failovers.

## Features

- **Continuous Writes**: Writes records with auto-incrementing sequence numbers
- **Connection Resilience**: Automatically reconnects on connection failures
- **Failover Detection**: Detects when database becomes read-only (failover in progress)
- **Metrics Tracking**:
  - Write success/failure counts
  - Connection errors
  - Write latency
  - Success rate
- **Gap Detection**: Sequence numbers allow detecting lost writes
- **Multiple Clients**: Can run multiple instances with unique client IDs

## Quick Start

### Using Skaffold (Recommended)

```bash
# Run with default single-node MariaDB
skaffold dev

# Run with replication profile (includes test client)
skaffold dev -p replication
```

The test client will automatically:
1. Build the Docker image
2. Deploy to Kubernetes
3. Start writing to the MariaDB instance
4. Show logs with write statistics

### Manual Deployment

```bash
# Build the image
docker build -t mariadb-test-client:latest test/client/

# Deploy using Helm
helm install test-client test/client/chart \
  --set image.tag=latest \
  --set mariadb.host=mariadb-cluster \
  --set mariadb.passwordSecret.name=mariadb \
  --set mariadb.passwordSecret.key=root-password
```

## Configuration

### Helm Chart Values

```yaml
# MariaDB connection
mariadb:
  host: mariadb-cluster           # MariaDB service name
  port: 3306
  user: root
  passwordSecret:
    name: mariadb                 # Secret containing password
    key: root-password            # Key within the secret
  database: test

# Client settings
client:
  id: ""                          # Unique client ID (uses pod name if empty)
  writeInterval: 1.0              # Seconds between writes

# Resources
resources:
  limits:
    cpu: 100m
    memory: 128Mi
  requests:
    cpu: 50m
    memory: 64Mi
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MARIADB_HOST` | Database hostname | localhost |
| `MARIADB_PORT` | Database port | 3306 |
| `MARIADB_USER` | Database username | root |
| `MARIADB_PASSWORD` | Database password | (required) |
| `MARIADB_DATABASE` | Database name | test |
| `WRITE_INTERVAL` | Seconds between writes | 1.0 |
| `CLIENT_ID` | Unique client identifier | hostname |

## Testing Failover

### 1. Start the Test Client

```bash
# With replication profile
skaffold dev -p replication
```

### 2. Monitor the Logs

```bash
kubectl logs -f deployment/mariadb-test-client -n default
```

You should see output like:
```
2025-01-08 10:30:15 [INFO] Starting failover test client (ID: client-mariadb-test-client-xxx)
2025-01-08 10:30:15 [INFO] Write interval: 0.5s
2025-01-08 10:30:15 [INFO] Successfully connected to MariaDB (server version: 11.2.2-MariaDB)
2025-01-08 10:30:15 [INFO] Table 'failover_test' initialized
2025-01-08 10:30:16 [INFO] ✓ Wrote sequence 1 (latency: 15.23ms, success_rate: 100.0%)
2025-01-08 10:30:17 [INFO] ✓ Wrote sequence 2 (latency: 12.45ms, success_rate: 100.0%)
```

### 3. Trigger Failover

#### Option A: Delete Primary Pod
```bash
# Find current primary
kubectl get pods -l app.kubernetes.io/name=mariadb -n default

# Delete primary pod (operator will failover)
kubectl delete pod mariadb-cluster-0 -n default
```

#### Option B: Manual Switchover
```bash
# Patch MariaDB to change primary
kubectl patch mariadb mariadb-cluster -n default --type=merge \
  -p '{"spec":{"replication":{"primary":{"podIndex":1}}}}'
```

### 4. Observe Failover Behavior

Watch the test client logs during failover:

```
2025-01-08 10:35:20 [INFO] ✓ Wrote sequence 150 (latency: 13.20ms, success_rate: 100.0%)
2025-01-08 10:35:21 [ERROR] ✗ Failed to write sequence 151: (1290, 'The MariaDB server is running with the --read-only option so it cannot execute this statement') (latency: 8.50ms, success_rate: 99.3%)
2025-01-08 10:35:21 [WARNING] ⚠ Database is read-only - failover may be in progress!
2025-01-08 10:35:22 [WARNING] Connection lost, attempting to reconnect...
2025-01-08 10:35:23 [INFO] Successfully connected to MariaDB (server version: 11.2.2-MariaDB)
2025-01-08 10:35:23 [INFO] ✓ Wrote sequence 152 (latency: 45.30ms, success_rate: 99.3%)
```

### 5. Verify Data Integrity

Query the database to check for sequence gaps:

```bash
kubectl exec -it mariadb-cluster-0 -n default -- mariadb -uroot -p test

# Check for gaps in sequence
SELECT
    client_id,
    COUNT(*) as total_records,
    MIN(sequence) as min_seq,
    MAX(sequence) as max_seq,
    MAX(sequence) - MIN(sequence) + 1 - COUNT(*) as missing_records
FROM failover_test
GROUP BY client_id;

# Find specific gaps
SELECT
    t1.sequence + 1 as gap_start,
    MIN(t2.sequence) - 1 as gap_end
FROM failover_test t1
LEFT JOIN failover_test t2 ON t2.sequence > t1.sequence
WHERE t1.client_id = 'your-client-id'
GROUP BY t1.sequence
HAVING gap_start < MIN(t2.sequence);
```

## Database Schema

The client creates a table:

```sql
CREATE TABLE failover_test (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    client_id VARCHAR(255) NOT NULL,
    sequence BIGINT NOT NULL,
    write_timestamp DATETIME(6) NOT NULL,
    hostname VARCHAR(255),
    INDEX idx_client_seq (client_id, sequence),
    INDEX idx_timestamp (write_timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

## Metrics and Statistics

The client prints statistics every 10 writes:

```
============================================================
Client Statistics (ID: client-mariadb-test-client-xxx)
============================================================
Current Sequence:     450
Total Writes:         450
Successful Writes:    448
Failed Writes:        2
Connection Errors:    1
Success Rate:         99.56%
Last Success:         2025-01-08 10:40:15.123456
Last Error:           2025-01-08 10:35:21.654321
============================================================
```

## Interpreting Results

### Successful Failover
- Client detects read-only errors during switchover
- Automatic reconnection to new primary
- All sequence numbers present (no gaps)
- Brief write failures (1-3 seconds) during failover

### Connection Termination (with Istio Gateway)
- Client connections forcefully closed (TCP RST)
- Immediate reconnection to new primary
- Minimal failed writes
- Fast recovery time (<2 seconds)

### Problematic Failover
- Long periods of write failures (>10 seconds)
- Missing sequence numbers (gaps in data)
- Client stuck retrying on old primary
- Manual intervention required (pod restart)

## Running Multiple Clients

To test with multiple concurrent clients:

```yaml
# In Helm values
replicaCount: 3

client:
  writeInterval: 0.5
```

Each client will have a unique ID (pod name) and write to separate sequence ranges.

## Cleanup

```bash
# Delete the test client
helm uninstall mariadb-test-client -n default

# Or with Skaffold
skaffold delete
```

## Development

### Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (connect to port-forwarded MariaDB)
kubectl port-forward svc/mariadb-cluster 3306:3306 -n default

# In another terminal
python main.py \
  --host localhost \
  --port 3306 \
  --user root \
  --password mariadb-root-password \
  --database test \
  --write-interval 1.0
```

### Building the Image

```bash
docker build -t mariadb-test-client:latest test/client/
```

## Troubleshooting

### Client Can't Connect

Check that MariaDB service is running:
```bash
kubectl get svc mariadb-cluster -n default
kubectl get pods -l app.kubernetes.io/name=mariadb -n default
```

### Authentication Failed

Verify the password secret exists:
```bash
kubectl get secret mariadb -n default -o yaml
```

### No Writes Happening

Check client logs:
```bash
kubectl logs deployment/mariadb-test-client -n default
```

### Database Table Not Created

Ensure the client has permissions:
```bash
# The root user should have CREATE TABLE permissions
kubectl exec -it mariadb-cluster-0 -n default -- \
  mariadb -uroot -p -e "SHOW GRANTS FOR root@'%';"
```
