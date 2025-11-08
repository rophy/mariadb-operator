# Skaffold Development Guide

This document describes how to use Skaffold for developing and testing the MariaDB Operator.

## Prerequisites

- [Skaffold](https://skaffold.dev/docs/install/) v2.0+
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Docker](https://www.docker.com/)
- A Kubernetes cluster (local or remote):
  - [KIND](https://kind.sigs.k8s.io/)
  - [Minikube](https://minikube.sigs.k8s.io/)
  - [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  - Any other Kubernetes cluster

## Quick Start

### 1. Create a Local Cluster (if needed)

```bash
# Using KIND
make cluster

# Or manually
kind create cluster --name mariadb-operator
```

### 2. Deploy Everything

```bash
# Build and deploy operator + standalone MariaDB
skaffold dev

# Or for one-time deployment
skaffold run
```

This will:
1. Build the operator Docker image from source
2. Install MariaDB Operator CRDs
3. Deploy the MariaDB Operator to `mariadb-system` namespace
4. Deploy a single-node MariaDB instance to `default` namespace
5. Set up port forwarding:
   - `localhost:8080` → Operator metrics
   - `localhost:3306` → MariaDB database

### 3. Connect to MariaDB

```bash
# Use the root password (default: mariadb-root-password)
mysql -h 127.0.0.1 -P 3306 -u root -pmariadb-root-password
```

## Profiles

Skaffold profiles allow you to deploy different configurations.

### Default Profile (Standalone)

Single MariaDB instance, webhook disabled:

```bash
skaffold dev
```

### Webhook Profile

Enable webhooks for validation/defaulting:

```bash
skaffold dev -p webhook
```

### Galera Profile

Deploy a 3-node Galera cluster:

```bash
skaffold dev -p galera
```

Features:
- 3 MariaDB replicas in sync multi-master mode
- Automatic cluster bootstrap and recovery
- 2Gi storage per node

### Replication Profile

Deploy a primary-replica setup (3 nodes):

```bash
skaffold dev -p replication
```

Features:
- 1 primary + 2 replicas
- Asynchronous replication
- Note: Additional CRs may be needed for full replication setup

### Production Profile

Production-like deployment with HA and metrics:

```bash
skaffold dev -p production
```

Features:
- 3 operator replicas (HA)
- 3-node Galera cluster
- Webhooks enabled
- Metrics enabled (requires Prometheus operator)
- 10Gi storage per node

## Development Workflow

### Continuous Development

```bash
# Watch for changes and auto-rebuild/redeploy
skaffold dev
```

This mode:
- Watches for code changes
- Rebuilds the image on changes
- Redeploys automatically
- Streams logs to your terminal
- Cleans up on Ctrl+C

### One-Time Deployment

```bash
# Deploy and exit
skaffold run

# Clean up
skaffold delete
```

### Debug Mode

```bash
# Enable debug logging
skaffold dev -v debug
```

### Skip Building

If you only want to deploy without rebuilding:

```bash
# Use existing image
skaffold deploy
```

## Customization

### Override Image Tag

```bash
# Use specific tag
skaffold dev --tag=v0.0.30
```

### Use Remote Image

Edit `skaffold.yaml` and change:

```yaml
setValueTemplates:
  image.repository: docker-registry3.mariadb.com/mariadb-operator/mariadb-operator
  image.tag: "25.10.2"
```

### Change MariaDB Storage Size

Edit `skaffold.yaml`:

```yaml
setValues:
  mariadb.storage.size: 5Gi  # Change from 2Gi
```

### Change Root Password

The default root password is `mariadb-root-password`. To change:

```bash
# Update the secret before deploying
kubectl create secret generic mariadb \
  --from-literal=root-password=your-password \
  --namespace=default \
  --dry-run=client -o yaml | kubectl apply -f -

skaffold dev
```

## Port Forwarding

Skaffold automatically sets up port forwarding:

| Service | Local Port | Namespace | Purpose |
|---------|-----------|-----------|---------|
| mariadb-operator | 8080 | mariadb-system | Operator metrics |
| mariadb-cluster | 3306 | default | MariaDB connection |

Access:
```bash
# Operator metrics
curl http://localhost:8080/metrics

# MariaDB
mysql -h 127.0.0.1 -P 3306 -u root -p
```

## Troubleshooting

### Image Pull Errors

If using a local registry or custom images:

```bash
# Load image to KIND
make docker-build
make docker-load

# Or manually
docker build -t mariadb-operator:latest .
kind load docker-image mariadb-operator:latest --name mariadb-operator
```

### Webhook Certificate Issues

If webhooks fail to start:

```bash
# Check cert-controller logs
kubectl logs -n mariadb-system deployment/mariadb-operator-cert-controller

# Use webhook profile which enables cert-manager integration
skaffold dev -p webhook
```

### CRD Not Found

Ensure CRDs are installed first:

```bash
# Check CRDs
kubectl get crds | grep mariadb.com

# Reinstall if needed
kubectl apply -k deploy/charts/mariadb-operator-crds/templates/
```

### MariaDB Won't Start

Check the MariaDB Pod logs:

```bash
kubectl logs -n default mariadb-cluster-0

# Check operator logs
kubectl logs -n mariadb-system deployment/mariadb-operator
```

Common issues:
- Insufficient resources
- Storage class not available
- Network policies blocking traffic

## Advanced Usage

### Multiple Profiles

Combine profiles (if compatible):

```bash
skaffold dev -p webhook -p production
```

### Custom Helm Values

Create a `skaffold-values.yaml`:

```yaml
mariadb:
  replicas: 5
  storage:
    size: 20Gi
  resources:
    requests:
      cpu: 1000m
      memory: 2Gi
```

Then reference it in `skaffold.yaml` under the helm release:

```yaml
valuesFiles:
  - skaffold-values.yaml
```

### Deploy to Remote Cluster

```bash
# Switch context
kubectl config use-context production-cluster

# Deploy
skaffold run -p production
```

### File Sync (for quick iterations)

For faster development, add file sync in `skaffold.yaml`:

```yaml
build:
  artifacts:
    - image: mariadb-operator
      sync:
        manual:
          - src: "cmd/**/*.go"
            dest: /app/cmd
```

## Clean Up

```bash
# Delete all resources
skaffold delete

# Or manually
helm uninstall mariadb-cluster -n default
helm uninstall mariadb-operator -n mariadb-system
helm uninstall mariadb-operator-crds -n mariadb-system
```

## Integration with Existing Workflows

### With Make

```bash
# Use existing Makefile targets
make cluster        # Create KIND cluster
make net           # Setup network (MetalLB)
make install-minio # Install Minio for backups

# Then use Skaffold
skaffold dev
```

### With Tilt

If you prefer Tilt over Skaffold, you can create a `Tiltfile` based on this configuration.

## Examples

### Testing Backup/Restore

```bash
# Deploy with Minio
make install-minio

# Deploy operator and DB
skaffold dev

# In another terminal, create a backup
kubectl apply -f examples/manifests/backup.yaml

# Monitor backup
kubectl get backups -w
```

### Testing Replication

```bash
# Deploy replication setup
skaffold dev -p replication

# Apply replication configuration
kubectl apply -f examples/manifests/mariadb_replication.yaml

# Check replication status
kubectl describe mariadb mariadb-cluster
```

## References

- [Skaffold Documentation](https://skaffold.dev/docs/)
- [MariaDB Operator Documentation](./docs/README.md)
- [Helm Charts](./deploy/charts/)
- [Example Manifests](./examples/manifests/)
