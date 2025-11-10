# ProxySQL

> [!NOTE]
> This documentation applies to `mariadb-operator` version >= v0.0.30 (planned)

> [!IMPORTANT]
> ProxySQL is licensed under [GNU General Public License v3.0](https://github.com/sysown/proxysql/blob/master/LICENSE). It is fully open source with no commercial restrictions.

ProxySQL is a high-performance, open-source database proxy for MySQL and MariaDB. It provides advanced features that ensure optimal performance and high availability:
- Query-based routing: Route read and write queries to different backend servers using hostgroups.
- Connection pooling: Reduce backend connections and improve performance with multiplexing.
- Automatic primary failover detection via read_only monitoring.
- Query caching: Cache frequently executed queries to reduce database load.
- Query rewriting and traffic shaping: Modify queries on-the-fly and control traffic patterns.
- Support for Replication and Galera clusters.

To better understand what ProxySQL is capable of, check the [official website](https://proxysql.com/) and the [documentation](https://proxysql.com/documentation/).

## Table of contents
<!-- toc -->
- [ProxySQL resources](#proxysql-resources)
- [<code>ProxySQL</code> CR](#proxysql-cr)
- [<code>MariaDB</code> integration with ProxySQL](#mariadb-integration-with-proxysql)
- [Defaults](#defaults)
- [Server configuration](#server-configuration)
- [Primary server detection](#primary-server-detection)
- [Server maintenance](#server-maintenance)
- [Configuration](#configuration)
- [Authentication](#authentication)
- [Kubernetes <code>Services</code>](#kubernetes-services)
- [Connection](#connection)
- [High availability](#high-availability)
- [Comparison with MaxScale](#comparison-with-maxscale)
- [ProxySQL Admin Interface](#proxysql-admin-interface)
- [Troubleshooting](#troubleshooting)
- [Reference](#reference)
<!-- /toc -->

## ProxySQL resources

Prior to configuring ProxySQL within Kubernetes, it's essential to have a basic understanding of the resources managed through its admin interface.

#### Servers

A server (backend) defines the MariaDB instances that ProxySQL forwards traffic to. Servers are assigned to hostgroups based on their role. For more detailed information, please consult the [servers reference](https://proxysql.com/documentation/main-runtime/#mysql_servers).

#### Hostgroups

Hostgroups are logical groupings of servers. ProxySQL uses hostgroups to route traffic:
- **Writer hostgroup**: Contains the primary server for write queries
- **Reader hostgroup**: Contains replica servers for read queries

For more detailed information, see the [hostgroups documentation](https://proxysql.com/documentation/global-variables/mysql-variables/#mysql-monitor_writer_is_also_reader).

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
- Query routing rules

For more detailed information, see the [users documentation](https://proxysql.com/documentation/main-runtime/#mysql_users).

#### Query Rules

Query rules define how ProxySQL routes queries based on pattern matching:
- Route SELECT queries to reader hostgroup
- Route INSERT/UPDATE/DELETE to writer hostgroup
- Cache specific queries
- Rewrite queries on-the-fly
- Apply rate limiting

For more detailed information, see the [query rules documentation](https://proxysql.com/documentation/main-runtime/#mysql_query_rules).

## `ProxySQL` CR

The minimal spec you need to provision a ProxySQL instance is just a reference to a `MariaDB` resource:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  mariaDbRef:
    name: mariadb-repl
```

This will provision a new `StatefulSet` for running ProxySQL and configure the servers specified by the `MariaDB` resource. Refer to the [Server configuration](#server-configuration) section if you want to manually configure the MariaDB servers.

The rest of the configuration uses reasonable [defaults](#defaults) set automatically by the operator. If you need more fine-grained configuration, you can provide these values yourself:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  mariaDbRef:
    name: mariadb-repl

  hostgroups:
    writer: 10
    reader: 20
    writerIsReader: true  # Primary can serve reads

  monitor:
    enabled: true
    connectInterval: 2000      # ms
    pingInterval: 10000        # ms
    readOnlyTimeout: 1500      # ms
    readOnlyCheckInterval: 1500 # ms

  admin:
    port: 6032
    statsPort: 6080

  service:
    port: 6033  # Application connection port

  kubernetesService:
    type: LoadBalancer
    metadata:
      annotations:
        metallb.universe.tf/loadBalancerIPs: 172.18.0.225
```

As you can see, the [ProxySQL resources](#proxysql-resources) we previously mentioned have a counterpart in the `ProxySQL` CR.

Refer to the [Reference](#reference) section for further detail.

## `MariaDB` integration with ProxySQL

To make your `MariaDB` cluster aware of ProxySQL, use an annotation to specify the ProxySQL instance:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: MariaDB
metadata:
  name: mariadb-repl
  annotations:
    mariadb.mmontes.io/proxysql: "proxysql-repl"
spec:
  replicas: 3

  replication:
    enabled: true
    primary:
      automaticFailover: true
```

The annotation format is:
```yaml
mariadb.mmontes.io/proxysql: "<proxysql-name>"
```

When this annotation is present, the operator will:
- Monitor ProxySQL's detection of the primary server
- Coordinate failover operations with ProxySQL
- Wait for ProxySQL to detect the new primary after failover
- **No proxy restart needed** - ProxySQL auto-detects via `@@read_only` monitoring

**Benefits of annotation-based approach:**
- ✅ No changes to MariaDB CRD required
- ✅ Backward compatible with upstream mariadb-operator
- ✅ Easy to add/remove ProxySQL without modifying MariaDB spec

**Example: MariaDB with ProxySQL**

```yaml
---
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  mariaDbRef:
    name: mariadb-repl

  replicas: 2

  kubernetesService:
    type: LoadBalancer

---
apiVersion: k8s.mariadb.com/v1alpha1
kind: MariaDB
metadata:
  name: mariadb-repl
  annotations:
    mariadb.mmontes.io/proxysql: "proxysql-repl"
spec:
  replicas: 3

  replication:
    enabled: true
```

During failover, the sequence will be:
1. Operator locks old primary, sets `read_only=ON`
2. Operator configures new primary, sets `read_only=OFF`
3. **ProxySQL monitor detects change** (within 1-2 seconds)
4. ProxySQL automatically routes traffic to new primary
5. No connection drops - clients stay connected to ProxySQL

Refer to the [Reference](#reference) section for further detail.

## Defaults

`mariadb-operator` aims to provide highly configurable CRs while maximizing usability by providing reasonable defaults. In the case of `ProxySQL`, the following defaulting logic is applied:
- `spec.servers` are inferred from `spec.mariaDbRef`.
- `spec.hostgroups.writer` defaults to `10`.
- `spec.hostgroups.reader` defaults to `20`.
- `spec.hostgroups.writerIsReader` defaults to `true` (primary can serve reads).
- `spec.monitor.enabled` defaults to `true`.
- `spec.admin.port` defaults to `6032` (standard ProxySQL admin port).
- `spec.service.port` defaults to `6033` (application connection port).

## Server configuration

As an alternative to providing a reference to a `MariaDB` via `spec.mariaDbRef`, you can also specify the servers manually:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  servers:
    - name: mariadb-0
      address: mariadb-repl-0.mariadb-repl-internal.default.svc.cluster.local
      port: 3306
      hostgroup: 10  # Writer hostgroup
      maxConnections: 1000
      weight: 1
    - name: mariadb-1
      address: mariadb-repl-1.mariadb-repl-internal.default.svc.cluster.local
      port: 3306
      hostgroup: 20  # Reader hostgroup
      maxConnections: 1000
      weight: 1
    - name: mariadb-2
      address: mariadb-repl-2.mariadb-repl-internal.default.svc.cluster.local
      port: 3306
      hostgroup: 20  # Reader hostgroup
      maxConnections: 1000
      weight: 1

  hostgroups:
    writer: 10
    reader: 20
```

As you can see, you can refer to in-cluster MariaDB servers by providing the DNS names of the `MariaDB` `Pods` as server addresses. In addition, you can also refer to external MariaDB instances running outside of the Kubernetes cluster where `mariadb-operator` was deployed:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-external
spec:
  servers:
    - name: mariadb-0
      address: 172.18.0.140
      port: 3306
      hostgroup: 10
    - name: mariadb-1
      address: 172.18.0.141
      port: 3306
      hostgroup: 20
    - name: mariadb-2
      address: 172.18.0.142
      port: 3306
      hostgroup: 20

  hostgroups:
    writer: 10
    reader: 20

  auth:
    adminUsername: proxysql-admin
    adminPasswordSecretKeyRef:
      name: proxysql-auth
      key: admin-password
    clientUsername: proxysql-client
    clientPasswordSecretKeyRef:
      name: proxysql-auth
      key: client-password
    monitorUsername: proxysql-monitor
    monitorPasswordSecretKeyRef:
      name: proxysql-auth
      key: monitor-password
```

⚠️ Pointing to external MariaDBs has some limitations ⚠️. Since the operator doesn't have a reference to a `MariaDB` resource (`spec.mariaDbRef`), it will be unable to perform the following actions:
- Autogenerate authentication credentials (`spec.auth`), so they will need to be provided by the user. See [Authentication](#authentication) section.

## Primary server detection

ProxySQL automatically detects the primary server by monitoring the `@@read_only` variable on all servers:
- Servers with `read_only=OFF` are moved to the writer hostgroup
- Servers with `read_only=ON` are moved to the reader hostgroup (or kept in writer hostgroup if `writerIsReader=true`)

This means that during a MariaDB failover:
1. Old primary has `read_only` set to `ON`
2. ProxySQL monitor detects the change (within `readOnlyCheckInterval` milliseconds)
3. Old primary is moved to reader hostgroup (or marked accordingly)
4. New primary is promoted with `read_only=OFF`
5. ProxySQL monitor detects the new primary
6. New primary is moved to writer hostgroup
7. **Applications experience zero connection drops** - connections to ProxySQL stay alive

This provides seamless failover from the application perspective - no connection pooling reconfiguration or reconnection logic needed.

## Server maintenance

You can put servers in maintenance mode by setting the server field `maintenance=true`:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  servers:
    - name: mariadb-0
      address: mariadb-repl-0.mariadb-repl-internal.default.svc.cluster.local
      port: 3306
      hostgroup: 10
      maintenance: true  # Server will be marked as OFFLINE_SOFT
```

When maintenance mode is enabled, ProxySQL will:
- Mark the server as `OFFLINE_SOFT`
- Allow existing connections to complete
- Not route new connections to this server

## Configuration

ProxySQL allows you to provide global configuration parameters. You can use `spec.config.params` to configure ProxySQL variables:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  config:
    params:
      mysql-threads: "4"
      mysql-max_connections: "2048"
      mysql-default_query_delay: "0"
      mysql-default_query_timeout: "36000000"
      mysql-have_compress: "true"
      mysql-poll_timeout: "2000"
      mysql-interfaces: "0.0.0.0:6033"
      mysql-default_schema: "information_schema"
      mysql-stacksize: "1048576"
      mysql-server_version: "8.0.35"
      mysql-connect_timeout_server: "3000"
      mysql-monitor_username: "proxysql-monitor"
      mysql-monitor_password: "secret"
      mysql-monitor_history: "600000"
      mysql-monitor_connect_interval: "60000"
      mysql-monitor_ping_interval: "10000"
      mysql-monitor_read_only_interval: "1500"
      mysql-monitor_read_only_timeout: "500"

    volumeClaimTemplate:
      resources:
        requests:
          storage: 1Gi
      accessModes:
        - ReadWriteOnce
```

Both this global configuration and the runtime configuration managed by the operator are stored under a volume provisioned by the `spec.config.volumeClaimTemplate`.

Refer to the [ProxySQL global variables documentation](https://proxysql.com/documentation/global-variables/) for available configuration parameters.

## Authentication

ProxySQL requires authentication with different levels of permissions for the following components/actors:
- ProxySQL Admin Interface consumed by `mariadb-operator`
- Clients connecting to ProxySQL
- ProxySQL monitor connecting to MariaDB servers
- ProxySQL config synchronization (HA setup)

By default, `mariadb-operator` autogenerates these credentials when `spec.mariaDbRef` is set and `spec.auth.generate = true`, but you can still provide your own:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  auth:
    generate: false
    adminUsername: proxysql-admin
    adminPasswordSecretKeyRef:
      name: proxysql-auth
      key: admin-password
    clientUsername: proxysql-client
    clientPasswordSecretKeyRef:
      name: proxysql-auth
      key: client-password
    clientMaxConnections: 10000
    monitorUsername: proxysql-monitor
    monitorPasswordSecretKeyRef:
      name: proxysql-auth
      key: monitor-password
```

The operator will automatically:
1. Create the admin user in ProxySQL admin interface
2. Create the client user in ProxySQL's `mysql_users` table
3. Create the monitor user in MariaDB with appropriate grants (`REPLICATION CLIENT`, `SUPER` for read_only checks)
4. Configure connection pooling and routing rules

## Kubernetes `Services`

To enable your applications to communicate with ProxySQL, a Kubernetes `Service` is provisioned. You have the flexibility to provide a template to customize this `Service`:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  kubernetesService:
    type: LoadBalancer
    metadata:
      annotations:
        metallb.universe.tf/loadBalancerIPs: 172.18.0.225
```

This results in the reconciliation of the following `Service`:

```yaml
apiVersion: v1
kind: Service
metadata:
  annotations:
    metallb.universe.tf/loadBalancerIPs: 172.18.0.225
  name: proxysql-repl
spec:
  ports:
  - name: proxysql
    port: 6033
    targetPort: 6033
  - name: admin
    port: 6032
    targetPort: 6032
  selector:
    app.kubernetes.io/instance: proxysql-repl
    app.kubernetes.io/name: proxysql
  type: LoadBalancer
```

There is also a dedicated admin `Service` for accessing the ProxySQL admin interface. See the [ProxySQL Admin Interface](#proxysql-admin-interface) section for more details.

## Connection

You can leverage the `Connection` resource to automatically configure connection strings as `Secret` resources that your applications can mount.

> [!NOTE]
> The `Connection` resource for ProxySQL works by directly referencing the ProxySQL service, not through the MariaDB annotation.

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: Connection
metadata:
  name: connection-proxysql
spec:
  # Connect directly to ProxySQL service
  host: proxysql-repl.default.svc.cluster.local
  port: 6033
  username: proxysql-client
  passwordSecretKeyRef:
    name: proxysql-client
    key: password
  secretName: conn-proxysql
```

Alternatively, you can also provide a connection template to your `ProxySQL` resource:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  connection:
    secretName: proxysql-conn
    port: 6033
```

Note that the `Connection` uses the `Service` described in the [Kubernetes Services](#kubernetes-services) section.

## High availability

ProxySQL supports configuration synchronization across multiple replicas using the `proxysql_cluster` feature. This allows you to run multiple ProxySQL instances for high availability:

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: ProxySQL
metadata:
  name: proxysql-repl
spec:
  replicas: 3

  config:
    cluster:
      enabled: true
      port: 6032
      checkIntervalMs: 1000
      checkStatusFrequency: 10
```

Multiple `ProxySQL` replicas can be specified by providing the `spec.replicas` field. When `spec.config.cluster.enabled=true`, the operator will:
1. Configure each ProxySQL instance as a cluster member
2. Set up automatic configuration synchronization
3. Ensure all replicas have consistent routing rules and server definitions

Note that `ProxySQL` exposes the [scale subresource](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/#scale-subresource), so you can scale/downscale it by running:

```bash
kubectl scale proxysql proxysql-repl --replicas 3
```

Or even configure an `HorizontalPodAutoscaler` to do the job automatically.

### HA Monitoring

Unlike MaxScale's cooperative locking for monitors, ProxySQL's monitor is lightweight and can run on all instances simultaneously without conflicts. Each ProxySQL instance:
- Monitors all backend servers independently
- Updates its own routing tables based on server status
- Synchronizes configuration changes via `proxysql_cluster`

This provides better failover resilience - if one ProxySQL instance fails, others continue monitoring and routing traffic.

## Comparison with MaxScale

| Feature | ProxySQL | MaxScale |
|---------|----------|----------|
| **License** | GPL v3 (fully open source) | BSL (commercial restrictions) |
| **Connection Persistence** | ✅ Yes | ✅ Yes |
| **Query Routing** | ✅ Hostgroup-based | ✅ Service-based |
| **Read/Write Split** | ✅ Yes | ✅ Yes |
| **Query Caching** | ✅ Yes | ❌ No |
| **Query Rewriting** | ✅ Yes | ❌ Limited |
| **Connection Pooling** | ✅ Multiplexing | ✅ Basic |
| **Admin Interface** | MySQL Protocol (port 6032) | REST API (port 8989) |
| **GUI** | ❌ No (3rd party available) | ✅ Built-in |
| **HA Monitoring** | All instances monitor | Cooperative locking |
| **Failover Detection** | read_only monitoring | MariaDB internals |
| **Transaction Replay** | ✅ Yes | ✅ Yes |
| **Resource Usage** | ~50MB RAM | ~200MB RAM |

**When to choose ProxySQL:**
- Need fully open source solution without licensing concerns
- Want advanced query routing, caching, or rewriting
- Prefer lighter resource footprint
- Need connection multiplexing for high connection counts
- Familiar with MySQL protocol for administration

**When to choose MaxScale:**
- Need official MariaDB support
- Want built-in GUI for management
- Prefer REST API for automation
- Working with MariaDB-specific features

## ProxySQL Admin Interface

`mariadb-operator` interacts with ProxySQL's admin interface via the MySQL protocol (port 6032 by default). The admin interface provides:
- Runtime configuration management
- Server and user configuration
- Query rules management
- Statistics and monitoring data

You can access the admin interface yourself for debugging:

```bash
# Port-forward the admin port
kubectl port-forward -n default svc/proxysql-repl 6032:6032

# Connect using any MySQL client
mysql -h 127.0.0.1 -P 6032 -u admin -p

# View current servers
SELECT * FROM mysql_servers;

# View runtime server status
SELECT * FROM stats_mysql_connection_pool;

# View query rules
SELECT * FROM mysql_query_rules;
```

Important admin tables:
- `mysql_servers`: Backend server configuration
- `mysql_users`: User authentication and routing
- `mysql_query_rules`: Query routing rules
- `runtime_mysql_servers`: Current runtime server status
- `stats_mysql_connection_pool`: Connection pool statistics
- `stats_mysql_commands_counters`: Query statistics

## Troubleshooting

`mariadb-operator` tracks both the `ProxySQL` status in regards to Kubernetes resources as well as the status of ProxySQL's runtime configuration. This information is available on the status field of the `ProxySQL` resource:

```yaml
status:
  conditions:
  - lastTransitionTime: "2024-11-10T17:00:00Z"
    message: Running
    reason: ProxySQLReady
    status: "True"
    type: Ready
  primaryServer: mariadb-repl-0
  replicas: 3
  servers:
  - name: mariadb-repl-0
    hostgroup: 10
    status: ONLINE
  - name: mariadb-repl-1
    hostgroup: 20
    status: ONLINE
  - name: mariadb-repl-2
    hostgroup: 20
    status: ONLINE
```

Kubernetes events emitted by `mariadb-operator` may also be very relevant for debugging:

```bash
kubectl get events --field-selector involvedObject.name=proxysql-repl --sort-by='.lastTimestamp'

LAST SEEN   TYPE      REASON                        OBJECT                    MESSAGE
15s         Normal    ProxySQLPrimaryServerChanged  proxysql/proxysql-repl   ProxySQL primary server changed from 'mariadb-repl-0' to 'mariadb-repl-1'
```

`mariadb-operator` logs can also be a good source of information. You can increase its verbosity and enable ProxySQL admin logs by running:

```bash
helm upgrade --install mariadb-operator mariadb-operator/mariadb-operator --set logLevel=debug --set extraArgs={--log-proxysql}
```

### Common errors

#### Connection refused to admin interface

This error occurs when ProxySQL admin interface is not accessible:

```
Error 2003 (HY000): Can't connect to MySQL server on 'proxysql-repl' (111)
```

Check:
1. ProxySQL pod is running: `kubectl get pods -l app.kubernetes.io/name=proxysql`
2. Admin port is configured correctly in `spec.admin.port`
3. Service is exposing the admin port
4. Admin credentials are correct

#### Servers stuck in SHUNNED status

This occurs when ProxySQL cannot connect to backend servers:

```sql
SELECT * FROM mysql_servers WHERE status='SHUNNED';
```

Check:
1. MariaDB pods are running and accessible
2. Monitor user has correct grants in MariaDB
3. Network connectivity between ProxySQL and MariaDB pods
4. Monitor credentials are correct

#### Monitor user permission denied

```
ProxySQL Monitor: Access denied for user 'proxysql-monitor'@'%'
```

The monitor user needs specific grants in MariaDB:

```sql
GRANT REPLICATION CLIENT, SUPER ON *.* TO 'proxysql-monitor'@'%';
FLUSH PRIVILEGES;
```

These are automatically created by the operator when using `spec.mariaDbRef`, but must be created manually for external MariaDB servers.

## Reference
- [API reference](./api_reference.md)
- [Example suite](../examples/)
- [ProxySQL documentation](https://proxysql.com/documentation/)
- [ProxySQL GitHub](https://github.com/sysown/proxysql)
