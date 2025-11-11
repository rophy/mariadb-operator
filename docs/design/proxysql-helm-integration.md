# ProxySQL Helm Chart Integration Design

## Overview

This document describes the design for integrating ProxySQL into the `mariadb-cluster` Helm chart, enabling users to deploy ProxySQL alongside MariaDB clusters with automatic failover detection capabilities.

## Goals

1. **Seamless integration**: ProxySQL deployment should be opt-in via a simple `proxysql.enabled=true` flag
2. **Automatic configuration**: ProxySQL should auto-configure based on MariaDB cluster settings
3. **Production-ready defaults**: Provide sensible defaults while allowing full customization
4. **Cluster support**: Support ProxySQL clustering for high availability
5. **Security**: Properly handle credentials and secrets
6. **Flexibility**: Support both Galera and Replication topologies

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   mariadb-cluster Helm Chart                 │
│                                                               │
│  ┌─────────────────┐              ┌─────────────────┐       │
│  │ MariaDB CR      │              │ ProxySQL        │       │
│  │ (StatefulSet)   │◄─────────────│ (StatefulSet)   │       │
│  │                 │   monitors   │                 │       │
│  │ - pod-0         │   @@read_only│ - pod-0         │       │
│  │ - pod-1         │              │ - pod-1         │       │
│  │ - pod-2         │              │ - pod-2         │       │
│  └─────────────────┘              └─────────────────┘       │
│         ▲                                 ▲                  │
│         │                                 │                  │
│         │                                 │                  │
│  ┌──────┴──────┐              ┌──────────┴────────┐         │
│  │ Secrets:    │              │ Services:         │         │
│  │ - root pw   │              │ - proxysql (6033) │         │
│  │ - monitor   │              │ - proxysql-admin  │         │
│  │ - users     │              │ - proxysql-cluster│         │
│  └─────────────┘              └───────────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

### Integration Approach

**Option A: Embedded in mariadb-cluster chart** (RECOMMENDED)
- ProxySQL templates live in `deploy/charts/mariadb-cluster/templates/proxysql/`
- Controlled by `proxysql.enabled` flag in values.yaml
- Shares namespace and lifecycle with MariaDB cluster
- Simpler user experience (single chart installation)

**Option B: Separate proxysql chart**
- Standalone chart in `deploy/charts/proxysql/`
- Can be used independently
- More flexible but requires separate installation

**Decision: Go with Option A** for better user experience, similar to how backups/users are integrated.

## Chart Structure

### Directory Layout

```
deploy/charts/mariadb-cluster/
├── Chart.yaml                          # Add proxysql keyword
├── values.yaml                         # Add proxysql section
├── templates/
│   ├── proxysql/
│   │   ├── _helpers.tpl               # ProxySQL-specific helpers
│   │   ├── statefulset.yaml           # ProxySQL StatefulSet
│   │   ├── service.yaml                # ProxySQL services
│   │   ├── service-headless.yaml       # Headless service for clustering
│   │   ├── configmap.yaml              # ProxySQL configuration
│   │   ├── secret.yaml                 # ProxySQL credentials
│   │   └── monitor-user.yaml           # Monitor User CR
│   ├── mariadb.yaml
│   ├── user.yaml
│   └── ...
└── README.md                           # Update with ProxySQL docs
```

## Values Schema

### Top-level values.yaml additions

```yaml
# ProxySQL configuration
proxysql:
  # -- Enable ProxySQL deployment
  enabled: false

  # -- ProxySQL image configuration
  image:
    registry: docker.io
    repository: proxysql/proxysql
    tag: "3.0.3-debian"
    pullPolicy: IfNotPresent

  # -- Number of ProxySQL replicas for HA
  replicas: 3

  # -- ProxySQL service configuration
  service:
    type: ClusterIP
    annotations: {}
    mysql:
      port: 6033
      nodePort: null
    admin:
      port: 6032
      nodePort: null

  # -- ProxySQL admin credentials
  admin:
    # If not provided, will generate random password
    existingSecret: ""
    secretKeys:
      password: "admin-password"
      clusterPassword: "cluster-password"
    # Alternative: specify values directly (not recommended for production)
    password: ""
    clusterPassword: ""

  # -- Monitor user configuration
  monitor:
    username: "proxysql-monitor"
    # If not provided, will generate random password
    existingSecret: ""
    secretKey: "password"
    password: ""

  # -- Hostgroup configuration
  hostgroups:
    writer: 10
    reader: 20
    comment: "MariaDB Cluster"

  # -- Monitoring intervals (milliseconds)
  monitoring:
    connectInterval: 60000
    pingInterval: 10000
    readOnlyInterval: 1500
    readOnlyTimeout: 500

  # -- MySQL variables configuration
  mysql:
    threads: 4
    maxConnections: 2048
    serverVersion: "8.0.23"
    interfaces: "0.0.0.0:6033"

  # -- ProxySQL clustering configuration
  cluster:
    enabled: true
    # When true, ProxySQL instances sync configuration automatically
    # Requires headless service for pod DNS resolution

  # -- Users to configure in ProxySQL
  # These users must exist in MariaDB (via User CRs)
  users:
    - username: "root"
      # Reference to existing secret (created by MariaDB chart)
      passwordSecretKeyRef:
        name: "{{ .Release.Name }}"
        key: "root-password"
      defaultHostgroup: 10
      maxConnections: 200
      active: true
    # Additional users can be added
    # - username: "app"
    #   passwordSecretKeyRef:
    #     name: "app-credentials"
    #     key: "password"
    #   defaultHostgroup: 10

  # -- Storage configuration for ProxySQL data
  storage:
    storageClassName: ""
    size: 2Gi

  # -- Resource limits
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 1000m
      memory: 512Mi

  # -- Pod annotations
  podAnnotations: {}

  # -- Pod security context
  podSecurityContext:
    fsGroup: 999

  # -- Container security context
  securityContext:
    runAsNonRoot: true
    runAsUser: 999

  # -- Node selector
  nodeSelector: {}

  # -- Tolerations
  tolerations: []

  # -- Affinity rules
  affinity:
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          podAffinityTerm:
            labelSelector:
              matchExpressions:
                - key: app.kubernetes.io/name
                  operator: In
                  values:
                    - proxysql
            topologyKey: kubernetes.io/hostname

  # -- Advanced ProxySQL configuration
  # Allows overriding any ProxySQL global variable
  advanced:
    adminVariables: {}
      # refresh_interval: 2000
    mysqlVariables: {}
      # connect_timeout_server: 3000
      # connect_retries_on_failure: 10

# Existing mariadb configuration
mariadb:
  # ... existing config ...

  # When proxysql.enabled=true, automatically create monitor user
  # This is handled by the chart, no user action needed
```

## Template Implementation

### 1. StatefulSet Template

**File: `templates/proxysql/statefulset.yaml`**

Key features:
- Conditional rendering: `{{- if .Values.proxysql.enabled }}`
- Dynamic server list generation based on `mariadb.replicas`
- ProxySQL clustering configuration
- Init container to wait for MariaDB availability
- Proper volume mounts for config and data

```yaml
{{- if .Values.proxysql.enabled }}
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {{ include "mariadb-cluster.fullname" . }}-proxysql
  namespace: {{ include "mariadb-cluster.namespace" . }}
  labels:
    {{- include "mariadb-cluster.proxysql.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.proxysql.replicas }}
  serviceName: {{ include "mariadb-cluster.fullname" . }}-proxysql-cluster
  selector:
    matchLabels:
      {{- include "mariadb-cluster.proxysql.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/proxysql/configmap.yaml") . | sha256sum }}
        {{- with .Values.proxysql.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      labels:
        {{- include "mariadb-cluster.proxysql.selectorLabels" . | nindent 8 }}
    spec:
      {{- with .Values.proxysql.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      initContainers:
      - name: wait-mariadb
        image: busybox:1.36
        command:
        - sh
        - -c
        - |
          until nc -z {{ include "mariadb-cluster.fullname" . }}-internal 3306; do
            echo "Waiting for MariaDB to be ready..."
            sleep 2
          done
      containers:
      - name: proxysql
        image: "{{ .Values.proxysql.image.registry }}/{{ .Values.proxysql.image.repository }}:{{ .Values.proxysql.image.tag }}"
        imagePullPolicy: {{ .Values.proxysql.image.pullPolicy }}
        ports:
        - name: mysql
          containerPort: 6033
          protocol: TCP
        - name: admin
          containerPort: 6032
          protocol: TCP
        volumeMounts:
        - name: config
          mountPath: /etc/proxysql.cnf
          subPath: proxysql.cnf
        - name: data
          mountPath: /var/lib/proxysql
        {{- with .Values.proxysql.resources }}
        resources:
          {{- toYaml . | nindent 10 }}
        {{- end }}
        {{- with .Values.proxysql.securityContext }}
        securityContext:
          {{- toYaml . | nindent 10 }}
        {{- end }}
      volumes:
      - name: config
        configMap:
          name: {{ include "mariadb-cluster.fullname" . }}-proxysql-config
      {{- with .Values.proxysql.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.proxysql.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.proxysql.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      {{- if .Values.proxysql.storage.storageClassName }}
      storageClassName: {{ .Values.proxysql.storage.storageClassName }}
      {{- end }}
      resources:
        requests:
          storage: {{ .Values.proxysql.storage.size }}
{{- end }}
```

### 2. ConfigMap Template

**File: `templates/proxysql/configmap.yaml`**

Dynamically generates `proxysql.cnf` with:
- MariaDB server list from `mariadb.replicas`
- Hostgroup configuration
- Monitor settings
- Cluster configuration (if enabled)
- User configuration from values

```yaml
{{- if .Values.proxysql.enabled }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "mariadb-cluster.fullname" . }}-proxysql-config
  namespace: {{ include "mariadb-cluster.namespace" . }}
  labels:
    {{- include "mariadb-cluster.proxysql.labels" . | nindent 4 }}
data:
  proxysql.cnf: |
    datadir="/var/lib/proxysql"

    admin_variables=
    {
        admin_credentials="admin:{{ include "mariadb-cluster.proxysql.adminPassword" . }};cluster:{{ include "mariadb-cluster.proxysql.clusterPassword" . }}"
        mysql_ifaces="0.0.0.0:6032"
        refresh_interval=2000
        {{- if .Values.proxysql.cluster.enabled }}
        cluster_username="cluster"
        cluster_password="{{ include "mariadb-cluster.proxysql.clusterPassword" . }}"
        {{- end }}
        {{- range $key, $value := .Values.proxysql.advanced.adminVariables }}
        {{ $key }}={{ $value }}
        {{- end }}
    }

    mysql_variables=
    {
        threads={{ .Values.proxysql.mysql.threads }}
        max_connections={{ .Values.proxysql.mysql.maxConnections }}
        interfaces="{{ .Values.proxysql.mysql.interfaces }}"
        server_version="{{ .Values.proxysql.mysql.serverVersion }}"

        monitor_username="{{ .Values.proxysql.monitor.username }}"
        monitor_password="{{ include "mariadb-cluster.proxysql.monitorPassword" . }}"
        monitor_connect_interval={{ .Values.proxysql.monitoring.connectInterval }}
        monitor_ping_interval={{ .Values.proxysql.monitoring.pingInterval }}
        monitor_read_only_interval={{ .Values.proxysql.monitoring.readOnlyInterval }}
        monitor_read_only_timeout={{ .Values.proxysql.monitoring.readOnlyTimeout }}
        {{- range $key, $value := .Values.proxysql.advanced.mysqlVariables }}
        {{ $key }}={{ $value }}
        {{- end }}
    }

    # Replication hostgroups enable automatic failover detection
    mysql_replication_hostgroups =
    (
        {
            writer_hostgroup={{ .Values.proxysql.hostgroups.writer }},
            reader_hostgroup={{ .Values.proxysql.hostgroups.reader }},
            comment="{{ .Values.proxysql.hostgroups.comment }}"
        }
    )

    # Backend MariaDB servers
    mysql_servers =
    (
        {{- $fullname := include "mariadb-cluster.fullname" . }}
        {{- $namespace := include "mariadb-cluster.namespace" . }}
        {{- $hostgroup := .Values.proxysql.hostgroups.writer }}
        {{- range $i := until (int .Values.mariadb.replicas) }}
        { address="{{ $fullname }}-{{ $i }}.{{ $fullname }}-internal.{{ $namespace }}.svc.cluster.local", port=3306, hostgroup={{ $hostgroup }}, max_connections=100 }{{ if ne $i (sub (int $.Values.mariadb.replicas) 1) }},{{ end }}
        {{- end }}
    )

    # ProxySQL users
    mysql_users =
    (
        {{- range $idx, $user := .Values.proxysql.users }}
        {
            username="{{ $user.username }}",
            password="{{ include "mariadb-cluster.proxysql.userPassword" (dict "user" $user "context" $) }}",
            default_hostgroup={{ $user.defaultHostgroup }},
            {{- if $user.maxConnections }}
            max_connections={{ $user.maxConnections }},
            {{- end }}
            active={{ if $user.active }}1{{ else }}0{{ end }}
        }{{ if ne $idx (sub (len $.Values.proxysql.users) 1) }},{{ end }}
        {{- end }}
    )

    {{- if .Values.proxysql.cluster.enabled }}
    # ProxySQL cluster configuration
    proxysql_servers =
    (
        {{- $fullname := include "mariadb-cluster.fullname" . }}
        {{- $namespace := include "mariadb-cluster.namespace" . }}
        {{- range $i := until (int .Values.proxysql.replicas) }}
        { hostname="{{ $fullname }}-proxysql-{{ $i }}.{{ $fullname }}-proxysql-cluster.{{ $namespace }}.svc.cluster.local", port=6032, weight=1 }{{ if ne $i (sub (int $.Values.proxysql.replicas) 1) }},{{ end }}
        {{- end }}
    )
    {{- end }}
{{- end }}
```

### 3. Service Templates

**File: `templates/proxysql/service.yaml`**

```yaml
{{- if .Values.proxysql.enabled }}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "mariadb-cluster.fullname" . }}-proxysql
  namespace: {{ include "mariadb-cluster.namespace" . }}
  labels:
    {{- include "mariadb-cluster.proxysql.labels" . | nindent 4 }}
  {{- with .Values.proxysql.service.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  type: {{ .Values.proxysql.service.type }}
  ports:
  - name: mysql
    port: {{ .Values.proxysql.service.mysql.port }}
    targetPort: 6033
    protocol: TCP
    {{- if and (eq .Values.proxysql.service.type "NodePort") .Values.proxysql.service.mysql.nodePort }}
    nodePort: {{ .Values.proxysql.service.mysql.nodePort }}
    {{- end }}
  - name: admin
    port: {{ .Values.proxysql.service.admin.port }}
    targetPort: 6032
    protocol: TCP
    {{- if and (eq .Values.proxysql.service.type "NodePort") .Values.proxysql.service.admin.nodePort }}
    nodePort: {{ .Values.proxysql.service.admin.nodePort }}
    {{- end }}
  selector:
    {{- include "mariadb-cluster.proxysql.selectorLabels" . | nindent 4 }}
{{- end }}
```

**File: `templates/proxysql/service-headless.yaml`**

```yaml
{{- if and .Values.proxysql.enabled .Values.proxysql.cluster.enabled }}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "mariadb-cluster.fullname" . }}-proxysql-cluster
  namespace: {{ include "mariadb-cluster.namespace" . }}
  labels:
    {{- include "mariadb-cluster.proxysql.labels" . | nindent 4 }}
spec:
  clusterIP: None
  publishNotReadyAddresses: true
  ports:
  - name: mysql
    port: 6033
    targetPort: 6033
    protocol: TCP
  - name: admin
    port: 6032
    targetPort: 6032
    protocol: TCP
  selector:
    {{- include "mariadb-cluster.proxysql.selectorLabels" . | nindent 4 }}
{{- end }}
```

### 4. Monitor User CR Template

**File: `templates/proxysql/monitor-user.yaml`**

Automatically creates the monitor user in MariaDB when ProxySQL is enabled:

```yaml
{{- if .Values.proxysql.enabled }}
apiVersion: k8s.mariadb.com/v1alpha1
kind: User
metadata:
  name: {{ include "mariadb-cluster.fullname" . }}-proxysql-monitor
  namespace: {{ include "mariadb-cluster.namespace" . }}
  labels:
    {{- include "mariadb-cluster.proxysql.labels" . | nindent 4 }}
spec:
  mariaDbRef:
    name: {{ include "mariadb-cluster.fullname" . }}
  name: {{ .Values.proxysql.monitor.username }}
  passwordSecretKeyRef:
    name: {{ include "mariadb-cluster.fullname" . }}-proxysql-monitor
    key: password
  host: "%"
  maxUserConnections: 10
  cleanupPolicy: Delete
---
apiVersion: k8s.mariadb.com/v1alpha1
kind: Grant
metadata:
  name: {{ include "mariadb-cluster.fullname" . }}-proxysql-monitor
  namespace: {{ include "mariadb-cluster.namespace" . }}
  labels:
    {{- include "mariadb-cluster.proxysql.labels" . | nindent 4 }}
spec:
  mariaDbRef:
    name: {{ include "mariadb-cluster.fullname" . }}
  privileges:
    - "REPLICATION CLIENT"
    - "SUPER"
  database: "*"
  table: "*"
  username: {{ .Values.proxysql.monitor.username }}
  host: "%"
  grantOption: false
  cleanupPolicy: Delete
{{- end }}
```

### 5. Secret Template

**File: `templates/proxysql/secret.yaml`**

```yaml
{{- if .Values.proxysql.enabled }}
{{- if not .Values.proxysql.monitor.existingSecret }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "mariadb-cluster.fullname" . }}-proxysql-monitor
  namespace: {{ include "mariadb-cluster.namespace" . }}
  labels:
    {{- include "mariadb-cluster.proxysql.labels" . | nindent 4 }}
type: Opaque
data:
  password: {{ include "mariadb-cluster.proxysql.monitorPassword" . | b64enc }}
{{- end }}
---
{{- if not .Values.proxysql.admin.existingSecret }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "mariadb-cluster.fullname" . }}-proxysql-admin
  namespace: {{ include "mariadb-cluster.namespace" . }}
  labels:
    {{- include "mariadb-cluster.proxysql.labels" . | nindent 4 }}
type: Opaque
data:
  admin-password: {{ include "mariadb-cluster.proxysql.adminPassword" . | b64enc }}
  cluster-password: {{ include "mariadb-cluster.proxysql.clusterPassword" . | b64enc }}
{{- end }}
{{- end }}
```

### 6. Helper Functions

**File: `templates/proxysql/_helpers.tpl`**

```gotmpl
{{/*
ProxySQL labels
*/}}
{{- define "mariadb-cluster.proxysql.labels" -}}
{{ include "mariadb-cluster.labels" . }}
app.kubernetes.io/component: proxysql
{{- end }}

{{/*
ProxySQL selector labels
*/}}
{{- define "mariadb-cluster.proxysql.selectorLabels" -}}
{{ include "mariadb-cluster.selectorLabels" . }}
app.kubernetes.io/component: proxysql
{{- end }}

{{/*
Get ProxySQL admin password
*/}}
{{- define "mariadb-cluster.proxysql.adminPassword" -}}
{{- if .Values.proxysql.admin.existingSecret }}
{{- /* Password will be loaded from existing secret */ -}}
{{- else if .Values.proxysql.admin.password }}
{{- .Values.proxysql.admin.password }}
{{- else }}
{{- randAlphaNum 32 }}
{{- end }}
{{- end }}

{{/*
Get ProxySQL cluster password
*/}}
{{- define "mariadb-cluster.proxysql.clusterPassword" -}}
{{- if .Values.proxysql.admin.existingSecret }}
{{- /* Password will be loaded from existing secret */ -}}
{{- else if .Values.proxysql.admin.clusterPassword }}
{{- .Values.proxysql.admin.clusterPassword }}
{{- else }}
{{- randAlphaNum 32 }}
{{- end }}
{{- end }}

{{/*
Get ProxySQL monitor password
*/}}
{{- define "mariadb-cluster.proxysql.monitorPassword" -}}
{{- if .Values.proxysql.monitor.existingSecret }}
{{- /* Password will be loaded from existing secret */ -}}
{{- else if .Values.proxysql.monitor.password }}
{{- .Values.proxysql.monitor.password }}
{{- else }}
{{- randAlphaNum 32 }}
{{- end }}
{{- end }}

{{/*
Get ProxySQL user password
*/}}
{{- define "mariadb-cluster.proxysql.userPassword" -}}
{{- $user := .user }}
{{- $ctx := .context }}
{{- if $user.passwordSecretKeyRef }}
{{- /* Will be replaced by actual password lookup in runtime */ -}}
{{- tpl $user.passwordSecretKeyRef.name $ctx }}-{{ $user.passwordSecretKeyRef.key }}
{{- else if $user.password }}
{{- $user.password }}
{{- else }}
{{- fail (printf "User %s must have either passwordSecretKeyRef or password" $user.username) }}
{{- end }}
{{- end }}
```

## Usage Examples

### Basic Deployment

```bash
# Enable ProxySQL with default settings
helm install mariadb mariadb-operator/mariadb-cluster \
  --set proxysql.enabled=true

# Connect to MariaDB via ProxySQL
kubectl run -it --rm mysql-client --image=mariadb:11.4 -- \
  mysql -h mariadb-proxysql -P 6033 -uroot -p
```

### Custom Configuration

```yaml
# values.yaml
mariadb:
  replicas: 5
  replication:
    enabled: true

proxysql:
  enabled: true
  replicas: 3

  monitor:
    username: "proxysql-monitor"

  users:
    - username: "root"
      passwordSecretKeyRef:
        name: "mariadb"
        key: "root-password"
      defaultHostgroup: 10
    - username: "app"
      passwordSecretKeyRef:
        name: "app-secret"
        key: "password"
      defaultHostgroup: 10
      maxConnections: 100

  resources:
    requests:
      cpu: 200m
      memory: 256Mi
    limits:
      cpu: 1000m
      memory: 1Gi

  storage:
    size: 5Gi
    storageClassName: "fast-ssd"
```

### With Galera Cluster

```yaml
mariadb:
  replicas: 3
  galera:
    enabled: true

proxysql:
  enabled: true
  # ProxySQL works with both replication and Galera
  # For Galera, all nodes can be writers, but we still use hostgroups
  hostgroups:
    writer: 10
    reader: 20
```

## Migration Path

For users with existing ProxySQL deployments:

1. **Phase 1**: Deploy ProxySQL via Helm chart alongside existing deployment
2. **Phase 2**: Migrate traffic to Helm-managed ProxySQL
3. **Phase 3**: Remove manual ProxySQL deployment

## Testing Strategy

### Unit Tests (Helm)

```bash
# Test template rendering
helm template mariadb ./deploy/charts/mariadb-cluster \
  --set proxysql.enabled=true

# Test with different replica counts
helm template mariadb ./deploy/charts/mariadb-cluster \
  --set mariadb.replicas=5 \
  --set proxysql.enabled=true \
  --set proxysql.replicas=3
```

### Integration Tests

1. **Basic connectivity**: Verify ProxySQL accepts connections
2. **Failover test**: Trigger MariaDB failover, verify ProxySQL detects and routes correctly
3. **Cluster sync**: With 3 ProxySQL replicas, verify config synchronization
4. **User authentication**: Test all configured users can connect
5. **Query routing**: Verify read/write split works correctly

## Security Considerations

1. **Secret management**:
   - Generate random passwords by default
   - Support external secret references
   - Never log passwords in ProxySQL pods

2. **Network policies**:
   - ProxySQL should only accept connections from allowed namespaces
   - Future: Add NetworkPolicy template (optional)

3. **RBAC**:
   - Monitor user has minimal privileges (REPLICATION CLIENT, SUPER)
   - Application users follow principle of least privilege

4. **Pod security**:
   - Run as non-root (uid 999)
   - Read-only root filesystem (where possible)
   - Drop all capabilities

## Limitations & Future Work

### Current Limitations

1. **No CRD support**: This is pure Helm, not operator-managed
2. **Manual user sync**: Users must be added to both MariaDB and ProxySQL values
3. **No dynamic scaling**: Changing replicas requires manual config updates
4. **Static configuration**: Runtime changes require pod restart

### Future Enhancements (Beyond Helm)

Once this Helm integration is stable, consider:

1. **ProxySQL CRD**: Operator-managed ProxySQL with reconciliation
2. **Automatic user sync**: Watch MariaDB User CRs, sync to ProxySQL
3. **Dynamic server discovery**: Automatically update ProxySQL when MariaDB scales
4. **Advanced monitoring**: Prometheus metrics, Grafana dashboards
5. **Query rules CRD**: Declarative query routing configuration

See [proxysql.md Future Work section](../proxysql.md#future-work) for details.

## Documentation Updates

### README.md additions

```markdown
## ProxySQL Integration

ProxySQL can be deployed alongside your MariaDB cluster for connection pooling,
query routing, and automatic failover detection.

### Enable ProxySQL

```yaml
proxysql:
  enabled: true
  replicas: 3
```

### Connect via ProxySQL

```bash
# Get the ProxySQL service
kubectl get svc mariadb-proxysql

# Connect to MariaDB through ProxySQL
mysql -h <proxysql-service> -P 6033 -u root -p
```

See [ProxySQL documentation](../../docs/proxysql.md) for detailed configuration options.
```

## Implementation Checklist

- [ ] Create ProxySQL template files
- [ ] Update values.yaml with proxysql section
- [ ] Update values.schema.json (if exists)
- [ ] Add helper functions to _helpers.tpl
- [ ] Create proxysql/_helpers.tpl
- [ ] Update Chart.yaml keywords
- [ ] Update README.md with ProxySQL section
- [ ] Add example values files
- [ ] Write integration tests
- [ ] Update CI/CD pipelines
- [ ] Documentation review
- [ ] Release notes

## Rollout Plan

### Phase 1: Development (Week 1-2)
- Implement all templates
- Local testing with kind/minikube
- Unit test template rendering

### Phase 2: Testing (Week 3)
- Deploy to dev environment
- Integration testing
- Failover testing
- Performance testing

### Phase 3: Documentation (Week 4)
- Complete all documentation
- Create examples
- Write migration guide

### Phase 4: Release (Week 5)
- Code review
- Final testing
- Release as part of mariadb-operator v0.0.30

## Open Questions

1. **Should we support query rules in initial release?**
   - Decision: No, keep initial release focused on basic failover
   - Query rules can be added later via `advanced.mysqlVariables`

2. **Should ProxySQL service be exposed externally by default?**
   - Decision: No, default to ClusterIP. Users can change to LoadBalancer/NodePort

3. **How to handle ProxySQL upgrades?**
   - Decision: Follow same pattern as MariaDB - rolling update
   - Document any breaking changes in release notes

4. **Should we create separate charts for Galera vs Replication?**
   - Decision: No, single chart handles both topologies

## References

- [ProxySQL Documentation](https://proxysql.com/documentation/)
- [ProxySQL Helm Chart Integration Docs](../proxysql.md)
- [Helm Best Practices](https://helm.sh/docs/chart_best_practices/)
- [mariadb-operator API Reference](https://github.com/mariadb-operator/mariadb-operator/blob/main/docs/api_reference.md)
