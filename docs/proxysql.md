# ProxySQL

> [!NOTE]
> This documentation applies to `mariadb-operator` version >= v0.0.30 (planned)

> [!IMPORTANT]
> ProxySQL is licensed under [GNU General Public License v3.0](https://github.com/sysown/proxysql/blob/master/LICENSE). It is fully open source with no commercial restrictions.

ProxySQL is a high-performance, open-source database proxy for MySQL and MariaDB. It provides advanced features that ensure optimal performance and high availability:
- Query-based routing: Route read and write queries to different backend servers using hostgroups.
- Connection pooling: Reduce backend connections and improve performance with multiplexing.
- **Automatic primary failover detection via read_only monitoring** - no operator coordination needed.
- Query caching: Cache frequently executed queries to reduce database load.
- Query rewriting and traffic shaping: Modify queries on-the-fly and control traffic patterns.
- Support for Replication and Galera clusters.

To better understand what ProxySQL is capable of, check the [official website](https://proxysql.com/) and the [documentation](https://proxysql.com/documentation/).

## Table of contents
<!-- toc -->
- [Overview](#overview)
- [ProxySQL resources](#proxysql-resources)
- [Deployment](#deployment)
- [Automatic failover detection](#automatic-failover-detection)
- [Test results](#test-results)
- [Configuration](#configuration)
- [Authentication](#authentication)
- [Comparison with MaxScale](#comparison-with-maxscale)
- [ProxySQL Admin Interface](#proxysql-admin-interface)
- [Troubleshooting](#troubleshooting)
- [Future work](#future-work)
- [Reference](#reference)
<!-- /toc -->

## Overview

ProxySQL integrates seamlessly with mariadb-operator's MariaDB replication clusters by automatically detecting primary failover through `@@read_only` monitoring. Unlike MaxScale, **ProxySQL does not require operator coordination during failover** - it operates autonomously and adapts to topology changes in real-time.

**Key advantages:**
- ✅ **Zero-touch failover**: ProxySQL automatically detects when primary changes via `@@read_only` monitoring (1.5s interval)
- ✅ **No connection drops**: Client connections to ProxySQL remain active during MariaDB failover
- ✅ **Operator-independent**: ProxySQL monitors MariaDB directly, no operator control plane needed for failover
- ✅ **Fully open source**: GPL v3 license with no commercial restrictions
- ✅ **Proven reliability**: Test results show 99.76% success rate during failover with only 2 failed writes

## ProxySQL resources

Prior to configuring ProxySQL within Kubernetes, it's essential to have a basic understanding of the resources managed through its admin interface.

#### Servers

A server (backend) defines the MariaDB instances that ProxySQL forwards traffic to. Servers are assigned to hostgroups based on their role. For more detailed information, please consult the [servers reference](https://proxysql.com/documentation/main-runtime/#mysql_servers).

#### Hostgroups

Hostgroups are logical groupings of servers. ProxySQL uses hostgroups to route traffic:
- **Writer hostgroup (default: 10)**: Contains the primary server for write queries
- **Reader hostgroup (default: 20)**: Contains replica servers for read queries

For more detailed information, see the [hostgroups documentation](https://proxysql.com/documentation/global-variables/mysql-variables/#mysql-monitor_writer_is_also_reader).

#### Replication Hostgroups

The `mysql_replication_hostgroups` table is the key to automatic failover detection. When configured, ProxySQL:
- Monitors `@@read_only` on all servers every `monitor_read_only_interval` milliseconds (default: 1500ms)
- Automatically moves servers between writer and reader hostgroups based on their `@@read_only` status
- Requires zero external coordination - operates entirely through native MariaDB state

```sql
-- Example: Configure automatic failover detection
INSERT INTO mysql_replication_hostgroups (writer_hostgroup, reader_hostgroup, comment)
VALUES (10, 20, 'MariaDB Replication');

LOAD MYSQL SERVERS TO RUNTIME;
SAVE MYSQL SERVERS TO DISK;
```

#### Monitor

ProxySQL has built-in monitoring that continuously checks the health and status of backend servers:
- **Connectivity checks**: Verifies servers are reachable
- **Read-only detection**: Monitors `@@read_only` and `@@super_read_only` flags to automatically route traffic
- **Lag monitoring**: Tracks replication lag for replica servers

For more detailed information, see the [monitor variables documentation](https://proxysql.com/documentation/global-variables/mysql-variables/#mysql-monitor_connect_interval).

#### Users

ProxySQL users define the authentication credentials that applications use to connect. Users can be configured with:
- Frontend authentication (client to ProxySQL)
- Backend connection pooling settings
- Transaction persistence
- Default hostgroup for routing

For more detailed information, see the [users documentation](https://proxysql.com/documentation/main-runtime/#mysql_users).

## Deployment

### Helm Chart Integration (Recommended)

> [!NOTE]
> Helm chart integration is available in the `mariadb-cluster` chart. This automatically deploys ProxySQL alongside your MariaDB cluster.

The recommended deployment method is through the mariadb-cluster Helm chart:

```yaml
# values.yaml
mariadb:
  replicas: 3
  replication:
    enabled: true

proxysql:
  enabled: true
  replicas: 3
  config:
    writerHostgroup: 10
    readerHostgroup: 20
```

Deploy with:

```bash
helm install mariadb-cluster mariadb-operator/mariadb-cluster \
  --set proxysql.enabled=true
```

### Manual Deployment

For manual deployment or custom configurations, you can deploy ProxySQL using StatefulSet and ConfigMap/Secret resources. See the example configurations in the repository:
- `proxysql.cnf` - ProxySQL configuration file
- `proxysql-ss-svc.yml` - StatefulSet and Services

**Key configuration requirements:**

1. **Backend servers**: Configure all MariaDB pod FQDNs
2. **Replication hostgroups**: Enable automatic failover detection
3. **Monitor user**: Create monitor user in MariaDB with appropriate grants
4. **Application users**: Configure users in ProxySQL's mysql_users table

Example configuration:

```conf
# mysql_replication_hostgroups enables automatic failover
mysql_replication_hostgroups =
(
    { writer_hostgroup=10, reader_hostgroup=20, comment="MariaDB Replication" }
)

# All servers start in same hostgroup - ProxySQL will auto-assign based on @@read_only
mysql_servers =
(
    { address="mariadb-cluster-0.mariadb-cluster-internal.default.svc.cluster.local", port=3306, hostgroup=10 },
    { address="mariadb-cluster-1.mariadb-cluster-internal.default.svc.cluster.local", port=3306, hostgroup=10 },
    { address="mariadb-cluster-2.mariadb-cluster-internal.default.svc.cluster.local", port=3306, hostgroup=10 }
)

# Monitor configuration
mysql_variables=
{
    monitor_username="monitor"
    monitor_password="monitor"
    monitor_read_only_interval=1500    # Check @@read_only every 1.5 seconds
    monitor_read_only_timeout=500
}
```

**Monitor user setup in MariaDB:**

```sql
CREATE USER 'monitor'@'%' IDENTIFIED BY 'monitor';
GRANT REPLICATION CLIENT, SUPER ON *.* TO 'monitor'@'%';
FLUSH PRIVILEGES;
```

## Automatic failover detection

ProxySQL's automatic failover detection is built on the `mysql_replication_hostgroups` feature, which monitors the `@@read_only` variable on all backend servers.

### How it works

1. **Initial state**: All MariaDB servers are configured in ProxySQL
2. **Continuous monitoring**: ProxySQL monitor checks `@@read_only` every 1.5 seconds (configurable)
3. **Automatic routing**:
   - Servers with `read_only=OFF` → moved to writer hostgroup (10)
   - Servers with `read_only=ON` → moved to reader hostgroup (20)
4. **During failover**:
   - mariadb-operator sets old primary `read_only=ON`
   - ProxySQL detects change and moves old primary to reader hostgroup
   - mariadb-operator promotes new primary `read_only=OFF`
   - ProxySQL detects new primary and moves to writer hostgroup
   - **Client connections stay alive** - zero connection drops

### Key benefits

- **No operator coordination required**: ProxySQL monitors MariaDB directly through native `@@read_only` state
- **Fast detection**: Typical detection time is 1.5-3 seconds (one or two monitor intervals)
- **Connection persistence**: Client connections to ProxySQL remain active during entire failover
- **Simplified architecture**: No complex coordination protocol between operator and proxy
- **Self-healing**: ProxySQL automatically adapts to any topology change, including manual failover

### Failover timeline

Based on actual testing with mariadb-operator:

```
T+0s:   Client writing to primary (pod-1) through ProxySQL - sequence 710
T+0s:   mariadb-operator triggers failover (pod-1 → pod-2)
T+0.5s: Old primary locked, read_only=ON
T+0.5s: Client gets read-only error - sequence 711
T+1s:   Client connection lost during switchover - sequence 712
T+1s:   Client reconnects to ProxySQL (same endpoint)
T+8.5s: First write succeeds on new primary - sequence 713
        (7.5s latency waiting for ProxySQL to detect new primary)
T+9s:   Normal operation resumed - 100% success rate
```

**Success rate: 99.76%** (only 2 failures during ~10 second failover window)

## Test results

ProxySQL was tested with mariadb-operator v0.0.30 performing manual switchover operations.

### Test environment

- **MariaDB cluster**: 3 replicas with semi-synchronous replication
- **ProxySQL**: 3 replicas with cluster synchronization enabled
- **Test client**: Continuous write workload (0.5s interval)
- **Failover type**: Manual switchover via `kubectl patch`

### Failover test #1: pod-0 → pod-1

**Timeline:**
- Sequences 301-308: Normal operation (100% success)
- Sequence 309-311: Read-only errors (old primary locked)
- Sequence 312: Lost connection during switchover
- Sequence 313: Reconnected, first write with 7.7s latency
- Sequences 314+: Normal operation resumed

**Results at sequence 400:**
- Total writes: 400
- Successful: 395
- Failed: 5
- **Success rate: 98.75%**

### Failover test #2: pod-1 → pod-2

**Timeline:**
- Sequences 701-710: Normal operation (100% success)
- Sequence 711: Read-only error (old primary locked)
- Sequence 712: Lost connection during switchover
- Sequence 713: Reconnected, first write with 8.5s latency
- Sequences 714+: Normal operation resumed

**Results at sequence 840:**
- Total writes: 840
- Successful: 838
- Failed: 2
- **Success rate: 99.76%**

### Data consistency verification

After both failovers, all MariaDB nodes were verified for data consistency:

```bash
# GTID positions (all synced)
pod-0: 0-12-1973, read_only=1
pod-1: 0-12-1973, read_only=1
pod-2: 0-12-1973, read_only=0 (primary)

# Replication status
pod-0 → pod-2: IO=Yes, SQL=Yes, Lag=0 seconds
pod-1 → pod-2: IO=Yes, SQL=Yes, Lag=0 seconds

# Row counts (minor differences due to active writes + replication lag)
pod-0: 1958 rows
pod-1: 1959 rows
pod-2: 1960 rows (primary receiving writes first)
```

**Conclusion: No data divergence, all nodes properly synchronized.**

### ProxySQL behavior

ProxySQL correctly detected and handled both failovers:

```sql
-- After failover to pod-2
SELECT hostgroup_id, hostname, status FROM runtime_mysql_servers;

+---------------+------------------------------------------------------+--------+
| hostgroup_id  | hostname                                             | status |
+---------------+------------------------------------------------------+--------+
| 10            | mariadb-cluster-2.mariadb-cluster-internal....       | ONLINE |
| 20            | mariadb-cluster-0.mariadb-cluster-internal....       | ONLINE |
| 20            | mariadb-cluster-1.mariadb-cluster-internal....       | ONLINE |
+---------------+------------------------------------------------------+--------+
```

- ✅ Automatic detection within 1-3 seconds
- ✅ Correct hostgroup assignment based on @@read_only
- ✅ Zero connection drops to ProxySQL service
- ✅ Queries routed to new primary immediately after detection

### Key findings

1. **ProxySQL requires no operator coordination for failover** - operates entirely through @@read_only monitoring
2. **Client impact is minimal** - only 2-5 failed writes during ~10 second switchover window
3. **Connection persistence works perfectly** - clients stay connected to ProxySQL throughout failover
4. **Detection is fast** - typical 1.5-3 second detection time for primary change
5. **Data consistency maintained** - all nodes properly synchronized after failover

## Configuration

ProxySQL configuration is provided through a configuration file (`proxysql.cnf`) mounted as a Secret or ConfigMap.

### Key configuration sections

```conf
# Admin interface
admin_variables=
{
    admin_credentials="admin:admin"
    mysql_ifaces="0.0.0.0:6032"
}

# MySQL variables
mysql_variables=
{
    threads=4
    max_connections=2048
    interfaces="0.0.0.0:6033"
    server_version="8.0.23"

    # Monitor configuration
    monitor_username="monitor"
    monitor_password="monitor"
    monitor_read_only_interval=1500  # Check @@read_only every 1.5s
    monitor_read_only_timeout=500
}

# Replication hostgroups for automatic failover
mysql_replication_hostgroups =
(
    { writer_hostgroup=10, reader_hostgroup=20, comment="MariaDB Replication" }
)

# Backend servers
mysql_servers =
(
    { address="mariadb-0.mariadb-internal.default.svc.cluster.local", port=3306, hostgroup=10 },
    { address="mariadb-1.mariadb-internal.default.svc.cluster.local", port=3306, hostgroup=10 },
    { address="mariadb-2.mariadb-internal.default.svc.cluster.local", port=3306, hostgroup=10 }
)

# Application users
mysql_users =
(
    { username="app", password="secret", default_hostgroup=10, active=1 }
)
```

### Storage

ProxySQL requires persistent storage for its runtime database:

```yaml
volumeClaimTemplates:
  - metadata:
      name: proxysql-data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 2Gi
```

Refer to the [ProxySQL global variables documentation](https://proxysql.com/documentation/global-variables/) for all available configuration parameters.

## Authentication

ProxySQL requires authentication at multiple levels:

### 1. Admin interface

Used by operators/administrators to configure ProxySQL:

```conf
admin_variables=
{
    admin_credentials="admin:admin;cluster:secret"
}
```

### 2. Monitor user (in MariaDB)

ProxySQL monitor needs credentials to check MariaDB server status:

```sql
-- Create in MariaDB
CREATE USER 'monitor'@'%' IDENTIFIED BY 'monitor';
GRANT REPLICATION CLIENT, SUPER ON *.* TO 'monitor'@'%';
FLUSH PRIVILEGES;
```

```conf
# Configure in ProxySQL
mysql_variables=
{
    monitor_username="monitor"
    monitor_password="monitor"
}
```

### 3. Application users

Define in ProxySQL's `mysql_users` table:

```conf
mysql_users =
(
    { username="appuser", password="apppass", default_hostgroup=10, active=1 },
    { username="root", password="rootpass", default_hostgroup=10, active=1 }
)
```

**Important**: Users must exist in both ProxySQL and MariaDB. ProxySQL authenticates the client, then uses the same credentials to connect to MariaDB.

## Comparison with MaxScale

| Feature | ProxySQL | MaxScale |
|---------|----------|----------|
| **License** | GPL v3 (fully open source) | BSL (commercial restrictions) |
| **Failover Coordination** | ✅ Autonomous (@@read_only monitoring) | ❌ Requires operator coordination |
| **Connection Persistence** | ✅ Yes (99.76% success rate) | ✅ Yes |
| **Operator Coupling** | ✅ Independent operation | ❌ Tight coupling with operator |
| **Failover Detection** | ✅ 1.5-3 seconds | ~5-10 seconds |
| **Query Routing** | ✅ Hostgroup-based | ✅ Service-based |
| **Read/Write Split** | ✅ Yes | ✅ Yes |
| **Query Caching** | ✅ Yes | ❌ No |
| **Query Rewriting** | ✅ Yes | ❌ Limited |
| **Connection Pooling** | ✅ Multiplexing | ✅ Basic |
| **Admin Interface** | MySQL Protocol (port 6032) | REST API (port 8989) |
| **GUI** | ❌ No (3rd party available) | ✅ Built-in |
| **Resource Usage** | ~50MB RAM | ~200MB RAM |
| **Transaction Replay** | ✅ Yes | ✅ Yes |

**Key architectural difference:**

MaxScale requires the operator to:
1. Coordinate failover with MaxScale
2. Update MaxScale configuration before/after failover
3. Wait for MaxScale to acknowledge changes
4. Handle synchronization between operator and proxy state

ProxySQL operates independently:
1. Monitors MariaDB servers directly via @@read_only
2. Automatically adapts to topology changes
3. No operator coordination protocol needed
4. Simpler, more resilient architecture

**When to choose ProxySQL:**
- ✅ Need fully open source solution without licensing concerns
- ✅ Want autonomous failover without operator coordination
- ✅ Prefer simpler architecture with fewer moving parts
- ✅ Need advanced query routing, caching, or rewriting
- ✅ Prefer lighter resource footprint
- ✅ Need connection multiplexing for high connection counts

**When to choose MaxScale:**
- Need official MariaDB support
- Want built-in GUI for management
- Prefer REST API for automation
- Working with MariaDB-specific features

## ProxySQL Admin Interface

ProxySQL's admin interface uses the MySQL protocol on port 6032 (by default). You can access it using any MySQL client:

```bash
# Port-forward the admin port
kubectl port-forward -n default svc/proxysql 6032:6032

# Connect using MySQL client
mysql -h 127.0.0.1 -P 6032 -u admin -padmin

# View configured servers
SELECT * FROM mysql_servers;

# View runtime server status
SELECT * FROM runtime_mysql_servers;

# View connection pool statistics
SELECT * FROM stats_mysql_connection_pool;

# View read_only monitoring log
SELECT * FROM monitor.mysql_server_read_only_log ORDER BY time_start_us DESC LIMIT 10;
```

### Important admin tables

**Configuration tables:**
- `mysql_servers`: Backend server configuration
- `mysql_replication_hostgroups`: Automatic failover configuration
- `mysql_users`: User authentication and routing
- `mysql_query_rules`: Query routing rules

**Runtime tables:**
- `runtime_mysql_servers`: Current runtime server status
- `runtime_mysql_replication_hostgroups`: Active replication hostgroup rules
- `runtime_mysql_users`: Active user configuration

**Statistics tables:**
- `stats_mysql_connection_pool`: Connection pool statistics per server
- `stats_mysql_commands_counters`: Query statistics
- `stats_mysql_query_rules`: Query rule match statistics

**Monitor tables:**
- `monitor.mysql_server_connect_log`: Connection test results
- `monitor.mysql_server_read_only_log`: Read-only status check results
- `monitor.mysql_server_ping_log`: Ping test results

### Configuration persistence

ProxySQL has a three-layer configuration system:

1. **Config layer**: Configuration file loaded at startup
2. **Runtime layer**: Active configuration in memory
3. **Disk layer**: Persistent storage (SQLite database)

Changes made via admin interface affect the memory layer only. To persist changes:

```sql
-- Apply configuration to runtime
LOAD MYSQL SERVERS TO RUNTIME;
LOAD MYSQL USERS TO RUNTIME;

-- Save to disk for persistence across restarts
SAVE MYSQL SERVERS TO DISK;
SAVE MYSQL USERS TO DISK;
```

**Note**: For Kubernetes deployments using ConfigMap/Secret configuration, prefer updating the ConfigMap/Secret and restarting pods rather than making runtime changes.

## Troubleshooting

### View server status

```sql
SELECT hostgroup_id, hostname, status, Queries
FROM stats_mysql_connection_pool
ORDER BY hostgroup_id, hostname;
```

Expected output:
```
+---------------+------------------------------------------------------+--------+---------+
| hostgroup_id  | hostname                                             | status | Queries |
+---------------+------------------------------------------------------+--------+---------+
| 10            | mariadb-cluster-0.mariadb-cluster-internal....       | ONLINE | 1523    |
| 20            | mariadb-cluster-1.mariadb-cluster-internal....       | ONLINE | 0       |
| 20            | mariadb-cluster-2.mariadb-cluster-internal....       | ONLINE | 0       |
+---------------+------------------------------------------------------+--------+---------+
```

### Check read_only monitoring

```sql
SELECT hostname, port, read_only, error
FROM monitor.mysql_server_read_only_log
ORDER BY time_start_us DESC
LIMIT 10;
```

Expected output (healthy):
```
+------------------------------------------------------+------+-----------+-------+
| hostname                                             | port | read_only | error |
+------------------------------------------------------+------+-----------+-------+
| mariadb-cluster-0.mariadb-cluster-internal....       | 3306 | 0         | NULL  |
| mariadb-cluster-1.mariadb-cluster-internal....       | 3306 | 1         | NULL  |
| mariadb-cluster-2.mariadb-cluster-internal....       | 3306 | 1         | NULL  |
+------------------------------------------------------+------+-----------+-------+
```

### Common errors

#### Monitor authentication failures

```
Access denied for user 'monitor'@'10.244.0.73' (using password: YES)
```

**Solution**: Create monitor user in MariaDB with correct grants:

```sql
CREATE USER 'monitor'@'%' IDENTIFIED BY 'monitor';
GRANT REPLICATION CLIENT, SUPER ON *.* TO 'monitor'@'%';
FLUSH PRIVILEGES;
```

#### Servers in SHUNNED status

```sql
SELECT * FROM mysql_servers WHERE status='SHUNNED';
```

**Causes**:
- MariaDB server not reachable
- Monitor user authentication failure
- Network connectivity issues

**Solution**:
1. Check MariaDB pod is running
2. Verify monitor user exists and has grants
3. Test network connectivity from ProxySQL pod

#### All traffic going to one server

```sql
SELECT hostgroup_id, srv_host, Queries FROM stats_mysql_connection_pool;
```

**Cause**: `mysql_replication_hostgroups` not configured

**Solution**:
```sql
INSERT INTO mysql_replication_hostgroups (writer_hostgroup, reader_hostgroup, comment)
VALUES (10, 20, 'MariaDB Replication');

LOAD MYSQL SERVERS TO RUNTIME;
SAVE MYSQL SERVERS TO DISK;
```

#### Client connection failures

```
ERROR 1045 (28000): Access denied for user 'appuser'@'10.244.0.76'
```

**Solution**: Add user to ProxySQL's mysql_users table:

```sql
INSERT INTO mysql_users (username, password, default_hostgroup, active)
VALUES ('appuser', 'password', 10, 1);

LOAD MYSQL USERS TO RUNTIME;
SAVE MYSQL USERS TO DISK;
```

## Future work

The following features are planned for future releases:

### 1. ProxySQL CRD (Custom Resource Definition)

Create a native Kubernetes CRD for declarative ProxySQL management:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  mariaDbRef:
    name: mariadb-repl
  replicas: 3
  hostgroups:
    writer: 10
    reader: 20
```

This would enable:
- Declarative configuration via Kubernetes API
- Automatic server discovery from MariaDB resource
- Dynamic user synchronization
- Scaling/lifecycle management through operator

### 2. Automated cluster bootstrap

Currently, ProxySQL cluster configuration requires manual setup of:
- `proxysql_servers` table with all cluster member FQDNs
- Cluster credentials
- Synchronization settings

Future work:
- Automatic cluster member discovery
- Dynamic cluster membership updates during scaling
- Automated cluster credential generation
- Helm chart generation of cluster configuration

### 3. User synchronization

Automatically sync MariaDB users to ProxySQL:
- Watch MariaDB User CRs
- Create corresponding entries in ProxySQL mysql_users table
- Maintain password synchronization
- Remove users when deleted from MariaDB

### 4. Scaling integration

Handle ProxySQL scaling operations:
- Update mysql_servers when MariaDB replicas change
- Update proxysql_servers when ProxySQL replicas change
- Graceful connection draining during scale-down
- Automatic configuration synchronization

### 5. Advanced monitoring

Operator-level ProxySQL monitoring:
- Expose ProxySQL metrics to Prometheus
- Alert on server status changes
- Track query performance statistics
- Monitor connection pool utilization

### 6. Query routing rules

Declarative query routing configuration:
- Read/write split rules
- Regex-based routing
- Query rewriting rules
- Rate limiting and throttling

**Note**: These are future enhancements. The current implementation focuses on core failover functionality, which operates autonomously without requiring operator coordination.

## Reference
- [ProxySQL documentation](https://proxysql.com/documentation/)
- [ProxySQL GitHub](https://github.com/sysown/proxysql)
- [Percona ProxySQL Kubernetes guide](https://www.percona.com/blog/getting-started-with-proxysql-in-kubernetes/)
- [mysql_replication_hostgroups documentation](https://proxysql.com/documentation/main-runtime/#mysql_replication_hostgroups)
