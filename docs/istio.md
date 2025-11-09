# Design Document: Integration of MariaDB Operator with Istio Gateway for Deterministic Failover

**Status:** Draft
**Author:** rophy
**Date:** 2025-01-08
**Target System:** Kubernetes-based DBaaS (MariaDB)
**Version:** 2.0

---

## 1. Overview

This document proposes integrating the **MariaDB Operator** with **Istio Gateway** (ingress controller only) to achieve **deterministic, strongly consistent failover orchestration** for externally-accessible databases in a Kubernetes cluster.

**Key Architecture Decision:**
- ✅ **Istio Gateway ONLY** (north-south traffic ingress)
- ❌ **NO Service Mesh** (no istio-proxy sidecars on MariaDB pods)
- ✅ Gateway provides reliable connection termination independent of database state

The goal is to guarantee that **no client connections or write operations reach a demoted primary** during failover, by coordinating network-layer traffic control (via Envoy Gateway) with the operator's database promotion logic.

---

## 2. Problem Statement

In multi-tenant DBaaS clusters, external clients (applications outside Kubernetes) connect to MariaDB databases through an ingress layer. Each database instance is managed by `mariadb-operator`, which handles replicas, health checks, and failover.

### Current Failover Challenges

**Existing Operator Behavior** (`pkg/controller/replication/switchover.go:71-96`):

```go
phases := []switchoverPhase{
    {
        name:      "Lock primary with read lock",
        reconcile: r.lockPrimaryWithReadLock,  // FLUSH TABLES WITH READ LOCK
    },
    {
        name:      "Set read_only in primary",
        reconcile: r.setPrimaryReadOnly,       // SET GLOBAL read_only=1
    },
    {
        name:      "Wait sync",
        reconcile: r.waitSync,                 // Wait for replicas to sync via GTID
    },
    {
        name:      "Configure new primary",
        reconcile: r.configureNewPrimary,      // Promote replica
    },
    {
        name:      "Connect replicas to new primary",
        reconcile: r.connectReplicasToNewPrimary,
    },
    {
        name:      "Change primary to replica",
        reconcile: r.changePrimaryToReplica,   // Demote old primary
    },
}
```

**Critical Gap:** The operator does NOT terminate client connections.

**File:** `pkg/sql/sql.go:678-684`
```go
func (c *Client) EnableReadOnly(ctx context.Context) error {
    return c.SetSystemVariable(ctx, "read_only", "1")  // Only sets read_only flag
}
```

When `read_only=1` is set:
- ✅ New writes fail with error
- ❌ Existing connections remain open
- ❌ Clients continue reading from old primary
- ⚠️ Clients only discover failover when they attempt to write

### What Happens When Primary is Unhealthy?

**File:** `pkg/controller/replication/switchover.go:443-450`

```go
func (r *ReplicationReconciler) currentPrimaryReady(ctx context.Context, mariadb *mariadbv1alpha1.MariaDB,
    clientSet *ReplicationClientSet) (bool, error) {
    if mariadb.Status.CurrentPrimaryPodIndex == nil {
        return false, errors.New("'status.currentPrimaryPodIndex' must be set")
    }
    _, err := clientSet.clientForIndex(ctx, *mariadb.Status.CurrentPrimaryPodIndex, sql.WithTimeout(1*time.Second))
    return err == nil, nil  // Only 1-second timeout
}
```

**File:** `pkg/controller/replication/switchover.go:172-186`

```go
func (r *ReplicationReconciler) setPrimaryReadOnly(ctx context.Context, req *ReconcileRequest, logger logr.Logger) error {
    if !req.currentPrimaryReady {
        logger.Info("Skipped enabling readonly mode in primary due to primary's non ready status")
        return nil  // ← SKIPS if primary is crashed/hung!
    }
    // ... execute SET GLOBAL read_only=1
}
```

**Problem:** If the old primary is crashed, hung, or network-partitioned:
- ❌ Cannot execute SQL commands to set read_only
- ❌ Cannot kill database connections via SQL
- ❌ External clients remain connected to failed primary indefinitely

### Why Gateway-Level Control is Necessary

**Using Cloud LoadBalancer only:**
```
External Clients → Cloud LB → K8s Service → MariaDB Pod
                      ↑
                      └── Health check propagation: 10-30 seconds
```

**Problems:**
1. Cloud LB health check updates are slow (10-30 seconds)
2. No control over forceful connection termination
3. No verification mechanism for routing changes
4. Total failover window: 20-60 seconds

**With Istio Gateway:**
```
External Clients → Cloud LB → Istio Gateway (Envoy) → K8s Service → MariaDB Pod
                                     ↑
                                     └── Operator has direct control
```

**Benefits:**
1. Operator can update Gateway routing via VirtualService CRD
2. Operator can forcefully close connections via Envoy Admin API
3. Operator can verify config application via `/config_dump`
4. Gateway works even if database is unresponsive
5. Total failover window: 5-10 seconds

---

## 3. Architecture

### 3.1 High-Level Architecture (Gateway-Only, No Service Mesh)

```
┌──────────────────────────────────────────────────────────────────┐
│ External Network (Client Applications)                           │
│   - Applications running outside Kubernetes                      │
│   - Connect to databases via TCP:3306                            │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ↓ DNS: tenant-a-db.example.com
┌──────────────────────────────────────────────────────────────────┐
│ Cloud LoadBalancer (or bare-metal LB)                            │
│   - Stable external IP                                           │
│   - Routes to Istio Gateway pods                                 │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ↓
┌──────────────────────────────────────────────────────────────────┐
│ Kubernetes Cluster                                               │
│                                                                  │
│  ┌────────────────────────────────────────────────────┐         │
│  │ Istio Gateway (Envoy Pods - NO SIDECARS)          │         │
│  │ - Ingress controller for external traffic          │         │
│  │ - TCP routing on port 3306                         │         │
│  │ - Envoy Admin API on :15000 (internal only)        │         │
│  │ - Receives xDS config from istiod                  │         │
│  │ - NO istio-proxy sidecars on MariaDB pods!         │         │
│  └────────────────────┬───────────────────────────────┘         │
│                       │                                          │
│                       ↓ VirtualService routes to                 │
│  ┌────────────────────────────────────────────────────┐         │
│  │ Kubernetes Service: mariadb-tenant-a-primary       │         │
│  │ - Type: ClusterIP                                  │         │
│  │ - Selector: statefulset.kubernetes.io/pod-name     │         │
│  │ - Operator updates selector during failover        │         │
│  │   (mariadb-0 → mariadb-1)                          │         │
│  └────────────────────┬───────────────────────────────┘         │
│                       │                                          │
│                       ↓ Direct pod-to-pod (NO SIDECAR)          │
│  ┌────────────────────────────────────────────────────┐         │
│  │ MariaDB StatefulSet (NO istio-proxy sidecars)      │         │
│  │                                                     │         │
│  │ ┌──────────────┐  ┌──────────────┐  ┌──────────┐  │         │
│  │ │ mariadb-0    │  │ mariadb-1    │  │mariadb-2 │  │         │
│  │ │ (replica)    │  │ (PRIMARY)    │  │(replica) │  │         │
│  │ │              │  │              │  │          │  │         │
│  │ │ Pod Label:   │  │ Pod Label:   │  │Pod Label:│  │         │
│  │ │ role=replica │  │ role=primary │  │role=repl │  │         │
│  │ └──────────────┘  └──────────────┘  └──────────┘  │         │
│  └────────────────────────────────────────────────────┘         │
│                       ↑                                          │
│                       │ Manages pods, services, failover         │
│  ┌────────────────────────────────────────────────────┐         │
│  │ MariaDB Operator (Enhanced for Gateway)            │         │
│  │ - Existing: Replication, failover, services        │         │
│  │ - NEW: Gateway integration, VirtualService updates │         │
│  │ - NEW: Envoy Admin API client for conn draining    │         │
│  └────────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 Key Components

#### Istio Gateway (Envoy - Ingress Only)
- Deployed as standalone pods (NOT sidecars)
- Receives external client connections
- Performs TCP routing based on VirtualService rules
- Exposes Admin API on `:15000` for operator control
- NO service mesh features needed

#### MariaDB Operator (Enhanced)
**Existing Functionality:**
- Pod management (`internal/controller/mariadb_controller.go`)
- Replication setup (`pkg/controller/replication/`)
- Failover detection (`internal/controller/pod_replication_controller.go:73-149`)
- Service reconciliation (`internal/controller/mariadb_controller.go:681-752`)

**New Functionality (to be added):**
- VirtualService reconciliation for Gateway routing
- Envoy Admin API client for connection draining
- Config verification via Envoy `/config_dump` endpoint
- Gateway-aware failover orchestration

#### Kubernetes Services (Existing)
**File:** `internal/controller/mariadb_controller.go:681-715`

The operator already creates:
1. **Primary Service** (`{name}-primary`)
   - Selector points to specific primary pod
   - Updated during failover (lines 696-700)
   ```go
   serviceLabels := labels.NewLabelsBuilder().
       WithMariaDBSelectorLabels(mariadb).
       WithStatefulSetPod(mariadb.ObjectMeta, *mariadb.Status.CurrentPrimaryPodIndex).
       Build()
   ```

2. **Secondary Service** (`{name}-secondary`)
   - Routes to all replica pods
   - Uses custom EndpointSlice that excludes primary

#### Istio VirtualService (New - Operator-Managed)
Maps external hostname to internal service:
```yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: tenant-a-db-primary
  ownerReferences:
    - apiVersion: k8s.mariadb.com/v1alpha1
      kind: MariaDB
      name: mariadb-tenant-a
spec:
  hosts:
  - "tenant-a-db.example.com"
  gateways:
  - istio-system/database-gateway
  tcp:
  - match:
    - port: 3306
    route:
    - destination:
        host: mariadb-tenant-a-primary  # Operator-managed service
        port:
          number: 3306
```

---

## 4. Enhanced Failover Sequence

### 4.1 Current Operator Failover (Baseline)

**Trigger:** `internal/controller/pod_replication_controller.go:73-149`

```go
func (r *PodReplicationReconciler) ReconcilePodNotReady(ctx context.Context, req *PodReconcileRequest) (ctrl.Result, error) {
    // Detects when primary pod becomes unhealthy
    // Sets CurrentPrimaryFailingSince timestamp
    // Waits for AutoFailoverDelay if configured
    // Selects new primary using FurthestAdvancedReplica()
    // Updates spec.replication.primary.podIndex to trigger switchover
}
```

**Switchover:** `pkg/controller/replication/switchover.go:43-118`

Existing phases (as shown in section 2).

**Problem:** No client connection management when primary is unhealthy.

---

### 4.2 Enhanced Failover with Gateway Integration

**New Switchover Phases:** Add to `pkg/controller/replication/switchover.go:71-96`

```go
phases := []switchoverPhase{
    // EXISTING PHASES
    {
        name:      "Lock primary with read lock",
        reconcile: r.lockPrimaryWithReadLock,
    },
    {
        name:      "Set read_only in primary",
        reconcile: r.setPrimaryReadOnly,
    },

    // NEW GATEWAY PHASES
    {
        name:      "Update Gateway to stop new connections",
        reconcile: r.updateGatewayToDrain,
    },
    {
        name:      "Verify Gateway config applied",
        reconcile: r.verifyGatewayConfigApplied,
    },
    {
        name:      "Close existing Gateway connections",
        reconcile: r.closeGatewayConnections,
    },

    // EXISTING PHASES (continue)
    {
        name:      "Wait sync",
        reconcile: r.waitSync,
    },
    {
        name:      "Configure new primary",
        reconcile: r.configureNewPrimary,
    },
    {
        name:      "Connect replicas to new primary",
        reconcile: r.connectReplicasToNewPrimary,
    },

    // NEW GATEWAY PHASE
    {
        name:      "Update Gateway to route to new primary",
        reconcile: r.updateGatewayToNewPrimary,
    },

    // EXISTING PHASE
    {
        name:      "Change primary to replica",
        reconcile: r.changePrimaryToReplica,
    },
}
```

---

### 4.3 Detailed Failover Steps

#### **Step 1: Lock Primary (Existing)**

**Only if primary is healthy** (`currentPrimaryReady = true`):
- Execute `FLUSH TABLES WITH READ LOCK`
- Execute `SET GLOBAL read_only=1`

**If primary is crashed/hung** (`currentPrimaryReady = false`):
- Skip SQL commands
- Proceed to Gateway-based connection handling

---

#### **Step 2: Update Gateway to Stop New Connections**

**Objective:** Prevent new client connections while existing ones drain.

**Implementation:** New function in `pkg/controller/replication/gateway.go` (to be created)

```go
func (r *ReplicationReconciler) updateGatewayToDrain(ctx context.Context, req *ReconcileRequest, logger logr.Logger) error {
    if !req.mariadb.Spec.Gateway.Enabled {
        logger.V(1).Info("Gateway not enabled, skipping")
        return nil
    }

    // Update VirtualService to route to a "draining" dummy service
    // This prevents new connections while we verify and close existing ones
    vs := req.mariadb.Spec.Gateway.VirtualServiceRef

    // Patch VirtualService to blackhole route
    patch := []byte(`{
        "spec": {
            "tcp": [{
                "match": [{"port": 3306}],
                "route": [{
                    "destination": {
                        "host": "blackhole.default.svc.cluster.local",
                        "port": {"number": 3306}
                    }
                }]
            }]
        }
    }`)

    return r.Patch(ctx, vs, client.RawPatch(types.MergePatchType, patch))
}
```

**Alternative (simpler):** Update weight to 0 instead of blackhole routing.

---

#### **Step 3: Verify Gateway Config Applied**

**Objective:** Ensure Gateway Envoy has received and applied the routing change.

**Method:** Poll Envoy Admin API `/config_dump` endpoint.

```go
func (r *ReplicationReconciler) verifyGatewayConfigApplied(ctx context.Context, req *ReconcileRequest, logger logr.Logger) error {
    if !req.mariadb.Spec.Gateway.Enabled {
        return nil
    }

    // Get Gateway pods
    gatewayPods, err := r.listGatewayPods(ctx, req.mariadb.Spec.Gateway.GatewaySelector)
    if err != nil {
        return err
    }

    // Poll each Gateway pod's Envoy admin API
    timeout := 5 * time.Second
    pollCtx, cancel := context.WithTimeout(ctx, timeout)
    defer cancel()

    for _, pod := range gatewayPods {
        if err := r.waitForEnvoyConfigUpdate(pollCtx, pod, expectedConfig); err != nil {
            return fmt.Errorf("gateway pod %s did not apply config: %v", pod.Name, err)
        }
    }

    logger.Info("Gateway config verified across all pods")
    return nil
}

func (r *ReplicationReconciler) waitForEnvoyConfigUpdate(ctx context.Context, pod corev1.Pod, expected ConfigMatcher) error {
    // Create HTTP client to pod:15000 (Envoy admin)
    // GET /config_dump
    // Parse JSON, check if route config matches expected
    // Retry with backoff until timeout
}
```

**Envoy Admin API Access:**
- Create ClusterIP service for Gateway admin port (15000)
- Or use pod exec to curl localhost:15000
- Parse JSON response to verify route destination

---

#### **Step 4: Close Existing Gateway Connections**

**Objective:** Forcefully terminate all client connections to old primary.

**Critical Advantage:** This works **even if the database pod is crashed or hung**.

```go
func (r *ReplicationReconciler) closeGatewayConnections(ctx context.Context, req *ReconcileRequest, logger logr.Logger) error {
    if !req.mariadb.Spec.Gateway.Enabled {
        return nil
    }

    gatewayPods, err := r.listGatewayPods(ctx, req.mariadb.Spec.Gateway.GatewaySelector)
    if err != nil {
        return err
    }

    for _, pod := range gatewayPods {
        // POST to Envoy admin API
        // Endpoint: /drain_listeners?inboundonly=true
        // This closes all active connections on the listener

        adminURL := fmt.Sprintf("http://%s:15000/drain_listeners?inboundonly=true", pod.Status.PodIP)
        resp, err := http.Post(adminURL, "application/json", nil)
        if err != nil {
            return fmt.Errorf("failed to drain connections on gateway pod %s: %v", pod.Name, err)
        }
        defer resp.Body.Close()

        if resp.StatusCode != http.StatusOK {
            return fmt.Errorf("gateway pod %s returned status %d", pod.Name, resp.StatusCode)
        }

        logger.Info("Drained connections on gateway pod", "pod", pod.Name)
    }

    return nil
}
```

**Result:** All client connections receive TCP RST, forcing immediate reconnection.

---

#### **Step 5-7: Database Promotion (Existing)**

**File:** `pkg/controller/replication/switchover.go:188-424`

- Wait for replicas to sync with primary GTID
- Disable replication on new primary
- Configure new primary (disable read_only, reset replication)
- Connect other replicas to new primary

**No changes needed** - existing logic works.

---

#### **Step 8: Update Gateway to Route to New Primary**

**Objective:** Direct all new connections to the newly promoted primary.

```go
func (r *ReplicationReconciler) updateGatewayToNewPrimary(ctx context.Context, req *ReconcileRequest, logger logr.Logger) error {
    if !req.mariadb.Spec.Gateway.Enabled {
        return nil
    }

    // At this point, CurrentPrimaryPodIndex has been updated to new primary
    newPrimaryService := req.mariadb.PrimaryServiceKey().Name  // e.g., mariadb-tenant-a-primary

    vs := req.mariadb.Spec.Gateway.VirtualServiceRef

    // Patch VirtualService back to normal route
    patch := []byte(fmt.Sprintf(`{
        "spec": {
            "tcp": [{
                "match": [{"port": 3306}],
                "route": [{
                    "destination": {
                        "host": "%s.%s.svc.cluster.local",
                        "port": {"number": 3306}
                    }
                }]
            }]
        }
    }`, newPrimaryService, req.mariadb.Namespace))

    if err := r.Patch(ctx, vs, client.RawPatch(types.MergePatchType, patch)); err != nil {
        return err
    }

    // Optional: Verify config applied
    return r.verifyGatewayConfigApplied(ctx, req, logger)
}
```

**Result:** Gateway now routes to new primary. Clients reconnect successfully.

---

#### **Step 9: Demote Old Primary (Existing)**

**File:** `pkg/controller/replication/switchover.go:381-424`

If old primary is healthy:
- Unlock tables
- Configure it as a replica of the new primary
- Enable read_only

If old primary is crashed:
- Skip (will be handled when pod recovers)

---

## 5. Operator API Changes

### 5.1 MariaDB CRD Extension

**File to modify:** `api/v1alpha1/mariadb_types.go`

```go
type MariaDBSpec struct {
    // ... existing fields (Replication, Storage, etc.)

    // Gateway configuration for external client access via Istio Gateway
    // +optional
    // +operator-sdk:csv:customresourcedefinitions:type=spec
    Gateway *GatewaySpec `json:"gateway,omitempty"`
}

type GatewaySpec struct {
    // Enabled determines whether Istio Gateway integration is active
    // +optional
    // +kubebuilder:default=false
    Enabled bool `json:"enabled"`

    // VirtualServiceRef references the VirtualService managed by the operator
    // The operator will update this VirtualService during failover
    // +optional
    VirtualServiceRef *corev1.ObjectReference `json:"virtualServiceRef,omitempty"`

    // GatewaySelector is the label selector for Istio Gateway pods
    // Used to find Gateway pods for admin API calls
    // Example: {"istio": "ingressgateway"}
    // +optional
    GatewaySelector map[string]string `json:"gatewaySelector,omitempty"`

    // AdminPort is the Envoy admin API port (default: 15000)
    // +optional
    // +kubebuilder:default=15000
    AdminPort int32 `json:"adminPort,omitempty"`
}
```

**Example Usage:**

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: MariaDB
metadata:
  name: mariadb-tenant-a
spec:
  replicas: 3
  replication:
    enabled: true
    primary:
      autoFailover: true

  gateway:
    enabled: true
    virtualServiceRef:
      name: tenant-a-db-primary
      namespace: default
    gatewaySelector:
      istio: ingressgateway
    adminPort: 15000
```

---

## 6. Implementation Phases

### Phase 1: Gateway Infrastructure Setup

**Tasks:**
1. Deploy Istio Gateway (ingress controller)
2. Create Gateway resource for TCP:3306
3. Expose Gateway via LoadBalancer
4. Test external connectivity

**No operator changes needed** - manual deployment and testing.

---

### Phase 2: Operator API Extension

**Files to create/modify:**
- `api/v1alpha1/mariadb_types.go` - Add `GatewaySpec`
- `api/v1alpha1/zz_generated.deepcopy.go` - Regenerate
- `config/crd/bases/k8s.mariadb.com_mariadbs.yaml` - Regenerate CRD

**Commands:**
```bash
make generate
make manifests
```

**Testing:** Deploy MariaDB with `spec.gateway.enabled: true`, verify field acceptance.

---

### Phase 3: VirtualService Reconciliation

**Files to create:**
- `internal/controller/virtualservice_controller.go`
- `pkg/controller/gateway/gateway.go` - Gateway utilities

**Integration:**
- Modify `internal/controller/mariadb_controller.go:Reconcile()` to call VirtualService reconciler

**Testing:** Verify VirtualService is created and points to primary service.

---

### Phase 4: Envoy Admin API Client

**Files to create:**
- `pkg/gateway/envoy_admin.go` - HTTP client for Envoy admin API

```go
type EnvoyAdminClient struct {
    httpClient *http.Client
}

func (c *EnvoyAdminClient) DrainListeners(ctx context.Context, podIP string, port int32) error
func (c *EnvoyAdminClient) GetConfigDump(ctx context.Context, podIP string, port int32) (*ConfigDump, error)
func (c *EnvoyAdminClient) VerifyRouteConfig(ctx context.Context, podIP string, expectedRoute RouteConfig) error
```

**Testing:** Unit tests + integration tests against real Envoy pod.

---

### Phase 5: Failover Integration

**Files to modify:**
- `pkg/controller/replication/switchover.go` - Add Gateway phases
- `pkg/controller/replication/gateway.go` - New file with Gateway-specific reconcile functions

**Testing:**
1. Trigger manual switchover via `podIndex` change
2. Verify Gateway config updates
3. Verify connections are closed
4. Verify clients reconnect to new primary

---

### Phase 6: Observability & Metrics

**Add metrics:**
- `mariadb_failover_gateway_config_update_duration_seconds`
- `mariadb_failover_gateway_connection_drain_duration_seconds`
- `mariadb_failover_gateway_verification_failures_total`

**Add events:**
- `GatewayConfigUpdated`
- `GatewayConnectionsDrained`
- `GatewayConfigVerified`

**File to modify:** `internal/controller/mariadb_controller.go` - Add to existing metrics.

---

## 7. Performance Expectations

| Phase | Without Gateway | With Gateway | Notes |
|-------|----------------|--------------|-------|
| Detect primary failure | 1-2s | 1-2s | Readiness probe |
| Set read_only | 100ms | 100ms | SQL command |
| Stop new connections | N/A | 300-500ms | VirtualService update + xDS propagation |
| Verify config applied | N/A | 200-400ms | Poll /config_dump across Gateway pods |
| Close existing connections | N/A (timeout) | 100-200ms | POST /drain_listeners |
| Promote new primary | 2-5s | 2-5s | GTID sync + promotion |
| Route to new primary | 1-3s (kube-proxy) | 300-500ms | VirtualService update |
| **Total Failover Time** | **10-30s** | **5-10s** | Significant improvement |

**Key improvement:** Gateway approach is deterministic and fast, even when old primary is unresponsive.

---

## 8. Security Considerations

### 8.1 Envoy Admin API Access

**Threat:** Unauthorized access to Envoy admin API could disrupt service.

**Mitigation:**
1. Admin port (15000) exposed only as ClusterIP (internal)
2. NetworkPolicy to restrict access to operator pods only
3. Optional: mTLS for admin API calls
4. Audit logging for all admin API calls

**Example NetworkPolicy:**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gateway-admin-access
  namespace: istio-system
spec:
  podSelector:
    matchLabels:
      istio: ingressgateway
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: mariadb-operator-system
      podSelector:
        matchLabels:
          app.kubernetes.io/name: mariadb-operator
    ports:
    - protocol: TCP
      port: 15000
```

---

### 8.2 VirtualService Update Authorization

**Threat:** Malicious actor could modify VirtualService to redirect traffic.

**Mitigation:**
1. RBAC: Operator ServiceAccount has minimal permissions
2. Admission webhooks validate VirtualService changes
3. OwnerReferences ensure only operator can update managed VirtualServices

---

## 9. Testing Strategy

### 9.1 Unit Tests

**Files:**
- `pkg/gateway/envoy_admin_test.go` - Mock HTTP responses
- `pkg/controller/replication/gateway_test.go` - Test failover phases

### 9.2 Integration Tests

**Scenarios:**
1. Normal failover (healthy primary)
2. Failover with crashed primary
3. Failover with hung/slow primary
4. Gateway pod restart during failover
5. Network partition scenarios

**Test framework:** Existing `test/e2e/` using Ginkgo/Gomega

### 9.3 Chaos Testing

Use Chaos Mesh to simulate:
- Primary pod kill
- Network delay to Gateway
- Istio control plane (istiod) failure

---

## 10. Comparison: Gateway vs Database-Level Connection Termination

| Aspect | Database-Level (SQL KILL) | Gateway-Level (Envoy API) |
|--------|---------------------------|---------------------------|
| **Works when DB crashed** | ❌ No | ✅ Yes |
| **Works when DB hung** | ❌ No | ✅ Yes |
| **Deterministic** | ⚠️ Depends on DB state | ✅ Always |
| **Speed** | ~1-2s (when healthy) | ~300-500ms |
| **Complexity** | ✅ Low | ⚠️ Moderate |
| **External dependency** | ✅ None | ⚠️ Requires Istio Gateway |
| **Multi-tenant scaling** | ⚠️ Per-DB LoadBalancer | ✅ Shared Gateway |
| **Verification** | ❌ No mechanism | ✅ /config_dump |
| **Observability** | ⚠️ Limited | ✅ Envoy metrics |

**Conclusion:** Gateway approach is superior for reliability and determinism, especially for multi-tenant DBaaS.

---

## 11. The "Stuck Client" Problem (Critical!)

### What Happens Without Connection Termination?

**Scenario:** Primary becomes read-only during failover, but connections are NOT closed.

```
Time T+0s: Primary fails, operator starts failover
Time T+2s: Operator sets read_only=ON on old primary (if healthy)
           ❌ Existing client connections REMAIN OPEN
Time T+5s: New primary promoted
Time T+7s: Service selector updated to new primary

┌──────────────────────────────────────────────────┐
│ Client with Connection Pool                     │
│                                                  │
│ Connection 1 ───┐                                │
│ Connection 2 ───┼──> Still connected to         │
│ Connection 3 ───┘    OLD PRIMARY (read-only!)   │
│                                                  │
│ What happens next?                               │
│                                                  │
│ ✅ SELECT queries: Work (reading stale data)     │
│ ❌ INSERT/UPDATE: ERROR 1290 (read-only)         │
│                                                  │
│ Connection pool behavior:                        │
│ - Keeps retrying writes on same connection       │
│ - MAY eventually mark connection as bad          │
│ - BUT: Many pools don't auto-close on SQL errors│
│ - TCP connection remains alive (keepalive)       │
│                                                  │
│ Result: CLIENT STUCK RETRYING WRITES!            │
│         Could last HOURS until:                  │
│         - Manual app restart                     │
│         - TCP keepalive timeout (often 2+ hours) │
│         - Application health check fails         │
└──────────────────────────────────────────────────┘
```

### Real-World Impact

**Without Gateway connection termination:**

1. **Application-level retry loops**
   - Apps with retry logic keep retrying writes
   - Connection pools don't automatically close on SQL errors
   - Error logs fill up with "read-only" errors

2. **Partial outage**
   - New connections → new primary (works!)
   - Existing connections → old primary (broken!)
   - Result: Some requests succeed, some fail

3. **Silent data loss risk**
   - Apps may silently drop write requests
   - Monitoring might not catch the issue
   - Users see inconsistent behavior

4. **Manual intervention required**
   - Operations team must identify affected apps
   - Restart apps or manually clear connection pools
   - Incident extends from minutes to hours

### How Applications Handle This

**Good connection pool implementations:**
```java
// Example: HikariCP with proper error handling
hikariConfig.setConnectionTestQuery("SELECT 1");
hikariConfig.setValidationTimeout(5000);
// On write error, tests connection and closes if bad
```

**Bad connection pool implementations:**
```java
// Naive pool - keeps connection alive on errors
// Retries indefinitely on same broken connection
// No automatic recovery
```

**Reality:** Most applications use connection pools that:
- ❌ Don't automatically close connections on SQL errors
- ❌ Don't have aggressive connection validation
- ⚠️ Rely on TCP keepalive (too slow)

### Gateway Solves This Completely

**With Gateway connection termination:**

```
Time T+0s: Primary fails, operator starts failover
Time T+2s: Operator updates Gateway VirtualService
Time T+3s: Operator calls /drain_listeners
           ✅ ALL client connections receive TCP RST
Time T+3.1s: Clients' connection pools detect closed connections
Time T+3.2s: Clients reconnect → routed to new primary
Time T+5s: New primary promoted and ready
Time T+7s: All clients reconnected and working

Result: CLEAN FAILOVER - no stuck clients!
```

**Key difference:** Gateway forcefully closes TCP connections at the network layer, which:
- ✅ Works even if database is crashed/hung
- ✅ Triggers immediate reconnection in all connection pools
- ✅ No application code changes needed
- ✅ No reliance on application-level error handling

---

## 12. Decision Matrix (Revised)

### When Istio Gateway is NECESSARY

**Use Istio Gateway if ANY of these apply:**

1. ✅ **External clients connect to database**
   - Clients outside Kubernetes cluster
   - No control over client application code
   - Cannot rely on client connection pool behavior

2. ✅ **Cannot tolerate stuck clients**
   - Production systems with strict SLAs
   - No ability to restart client applications quickly
   - Monitoring may not catch partial outages

3. ✅ **Multi-tenant DBaaS platform**
   - Shared Gateway infrastructure across tenants
   - Need deterministic failover guarantees
   - Cannot coordinate with all tenant applications

4. ✅ **Database primary failure scenarios**
   - Primary can crash/hang/network-partition
   - Need to handle failover when DB is unresponsive
   - Cannot rely on SQL commands (KILL CONNECTION)

5. ✅ **Fast failover requirement (<10s)**
   - Business requires rapid recovery
   - Cannot wait for TCP keepalive timeouts

### When Gateway Can Be Skipped

**Skip Istio Gateway ONLY if ALL of these are true:**

1. ❌ **All clients are inside cluster AND**
2. ❌ **You implement operator-level connection killing** (see alternatives below) AND
3. ❌ **You control all client applications** (can fix their connection pools) AND
4. ❌ **Team lacks Istio operational capacity** AND
5. ❌ **You can tolerate partial outages during failover**

### Alternative: Implement Connection Killing in Operator

If you skip Gateway, you **MUST** implement one of these:

**Option A: Database-Level Connection Killing**
```go
// Add to switchover.go
{
    name:      "Kill client connections (best-effort)",
    reconcile: r.killClientConnections,
}

func (r *ReplicationReconciler) killClientConnections(...) error {
    if !req.currentPrimaryReady {
        // Primary crashed - cannot kill connections
        logger.Warn("Cannot kill connections, primary unresponsive")
        return nil  // Clients remain stuck!
    }

    // Query INFORMATION_SCHEMA.PROCESSLIST
    // KILL CONNECTION for each non-system connection
    // Timeout: 5 seconds
}
```

**Limitations:**
- ❌ Only works if primary is healthy
- ❌ Clients stuck if primary is crashed/hung
- ⚠️ Requires careful filtering (don't kill replication threads!)

**Option B: Pod Deletion (Nuclear Option)**
```go
{
    name:      "Delete old primary pod",
    reconcile: r.deleteOldPrimaryPod,
}
```

**Limitations:**
- ❌ Destructive (forces pod restart)
- ❌ Loses in-flight transactions
- ⚠️ Aggressive approach

**Option C: Require Application-Level Handling**

Mandate all client applications implement:
```java
// Detect write failures and close connection
try {
    connection.execute("INSERT ...");
} catch (SQLException e) {
    if (e.getMessage().contains("read-only")) {
        connection.close();  // Force reconnection
        throw e;
    }
}
```

**Limitations:**
- ❌ Requires changes to ALL client applications
- ❌ Developers may not implement correctly
- ❌ No control over third-party clients

### Recommendation

**For production DBaaS:** Use Istio Gateway

**Rationale:**
- Handles all failure scenarios (crashed, hung, healthy primary)
- No reliance on client application behavior
- Deterministic connection termination
- No manual intervention required
- The "stuck client" problem is **not theoretical** - it will happen

**The cost of Gateway (operational complexity) is worth avoiding:**
- Hours-long partial outages
- Manual application restarts
- Customer-facing errors
- On-call incidents at 3 AM

---

## 13. Open Questions

1. **Gateway HA:** How many Gateway replicas? (Recommend: 2-3)
2. **Config verification timeout:** How long to wait for xDS propagation? (Recommend: 5s)
3. **Connection drain grace period:** Immediate or delay? (Recommend: Immediate for determinism)
4. **Fallback strategy:** What if Gateway API calls fail? (Recommend: Log error, proceed with DB promotion)
5. **Multi-cluster:** How to handle Gateway in federated clusters? (Future enhancement)

---

## 14. References

### Operator Codebase
- Replication switchover: `pkg/controller/replication/switchover.go`
- Failover detection: `internal/controller/pod_replication_controller.go`
- Service management: `internal/controller/mariadb_controller.go:681-752`
- SQL client: `pkg/sql/sql.go`

### Istio Documentation
- Gateway: https://istio.io/latest/docs/reference/config/networking/gateway/
- VirtualService: https://istio.io/latest/docs/reference/config/networking/virtual-service/
- Envoy Admin API: https://www.envoyproxy.io/docs/envoy/latest/operations/admin

### MariaDB
- Replication: https://mariadb.com/kb/en/standard-replication/
- GTID: https://mariadb.com/kb/en/gtid/
- Semi-sync: https://mariadb.com/kb/en/semisynchronous-replication/

---

## 15. Summary

This design integrates **Istio Gateway (ingress-only, no service mesh)** with the existing **mariadb-operator** to provide:

1. ✅ **Deterministic failover** - Works even when primary is crashed/hung
2. ✅ **Fast connection termination** - Gateway closes connections via Envoy API
3. ✅ **Verifiable routing updates** - Config dump confirms changes applied
4. ✅ **Multi-tenant efficiency** - Shared Gateway infrastructure
5. ✅ **Minimal operator changes** - Builds on existing failover logic

**Key Architecture Points:**
- Gateway-only deployment (no sidecars on MariaDB pods)
- Operator orchestrates both database and Gateway layers
- Gateway provides reliable connection control independent of DB state
- Existing service architecture (`{name}-primary`) works seamlessly

**Total Implementation Effort:** ~2-3 weeks for experienced team

- Phase 1-2: Infrastructure + API (1 week)
- Phase 3-5: Implementation (1-2 weeks)
- Testing & iteration (ongoing)
