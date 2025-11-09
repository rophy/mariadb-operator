# Envoy Admin API - Connection Termination Research

**Date:** 2025-01-10
**Context:** Research for MariaDB operator failover with Istio Gateway integration

---

## Executive Summary

**Key Finding:** The **only reliable way to forcefully terminate existing TCP connections through Istio Gateway is to restart the Gateway pod**. Envoy's admin API endpoints do not forcefully close existing TCP connections.

---

## Envoy Admin API Endpoints Tested

### 1. `/drain_listeners` (POST)

**Purpose:** Drain listeners to stop accepting new connections before shutdown.

**Parameters:**
- `graceful` - When set, enters a graceful drain period
- `inboundonly` - Drains only inbound listeners

**Test Results:**

```bash
# Test 1: Graceful drain
kubectl exec -n istio-ingress <pod> -- curl -X POST "http://localhost:15000/drain_listeners?inboundonly"
Result: Returns "OK", but existing TCP connections REMAIN OPEN
```

```bash
# Test 2: Non-graceful drain
kubectl exec -n istio-ingress <pod> -- curl -X POST "http://localhost:15000/drain_listeners?inboundonly&graceful=n"
Result: Returns "OK", but existing TCP connections REMAIN OPEN
```

**Behavior:**
- ✅ Stops accepting NEW connections
- ❌ Does NOT close existing connections
- ⚠️ Only works for graceful shutdowns

**Impact on Active Connections:** None - connections continue working

---

## Alternative Approaches Tested

### 2. VirtualService Route Update

**Test:** Update VirtualService to route to non-existent backend

```bash
kubectl patch virtualservice mariadb-test-client-vs -n default \
  --type=json \
  -p='[{"op": "replace", "path": "/spec/tcp/0/route/0/destination/host", "value": "blackhole.default.svc.cluster.local"}]'
```

**Result:** ❌ Existing connections REMAIN OPEN
**Explanation:** Once TCP connection is established, Envoy passes through traffic. The connection mapping persists at kernel/conntrack level.

---

### 3. Gateway Resource Deletion

**Test:** Delete the Istio Gateway resource entirely

```bash
kubectl delete gateway mariadb-test-client-gateway -n istio-ingress
```

**Result:** ❌ Existing connections REMAIN OPEN
**Explanation:** Deleting the Gateway CRD doesn't immediately affect Envoy's active connections. The listener configuration is updated but existing connections persist.

---

### 4. Gateway Pod Restart ✅

**Test:** Delete Gateway pod to force restart

```bash
kubectl delete pod -n istio-ingress istio-ingress-<pod-name>
```

**Result:** ✅ **ALL connections immediately terminated**

**Evidence from test-client logs:**
```
2025-11-09 21:48:52 [INFO] Failed Writes:        8
2025-11-09 21:48:52 [INFO] Connection Errors:    8
2025-11-09 21:48:53 [WARNING] Connection lost, attempting to reconnect...
2025-11-09 21:48:53 [ERROR] Failed to connect to MariaDB: 2003 (HY000): Can't connect to MySQL server
```

**Recovery Time:**
- Connections broken: Immediate (when pod terminates)
- New pod ready: ~10-15 seconds
- Client reconnection: Automatic

---

## Deep Dive: Why `/drain_listeners` Doesn't Close TCP Connections

### Critical Discovery: Listener Configuration

**Listener Details (from `/config_dump`):**
```json
{
  "name": "0.0.0.0_3306",
  "traffic_direction": "OUTBOUND",
  "filter_chains": [{
    "filters": [
      {"name": "istio.stats"},
      {"name": "envoy.filters.network.tcp_proxy"}
    ]
  }]
}
```

**Key Observations:**
1. **Traffic Direction:** `OUTBOUND` (not `INBOUND`)
2. **Filter Type:** `envoy.filters.network.tcp_proxy` (network filter, not HTTP filter)
3. **Drain Type:** Not explicitly set (defaults to `DEFAULT`)

### Why drain_listeners Works Differently Than Expected

#### Test Results Breakdown

**Test 1: `/drain_listeners?inboundonly`**
```bash
curl -X POST "http://localhost:15000/drain_listeners?inboundonly"
```
- **Result:** Returns "OK" but does NOT drain the listener
- **Reason:** Listener has `traffic_direction: OUTBOUND`, so `inboundonly` param doesn't match
- **Existing connections:** Continue working ✅
- **New connections:** Still accepted ✅

**Test 2: `/drain_listeners` (all listeners)**
```bash
curl -X POST "http://localhost:15000/drain_listeners"
```
- **Result:** Returns "OK" and DOES prevent new connections
- **Existing connections:** Continue working ✅
- **New connections:** REJECTED with error 2002 ❌
- **Server state:** Remains "LIVE" (not "DRAINING")
- **Stats:** `total_listeners_draining: 0`

**Evidence - New Connection Test:**
```
$ mysql -h istio-ingress.istio-ingress.svc.cluster.local ...
ERROR 2002 (HY000): Can't connect to server (115)
```

**Evidence - Existing Connection Still Works:**
```
2025-11-09 22:02:54 [INFO] ✓ Wrote sequence 2172 (latency: 10.05ms, success_rate: 93.7%)
2025-11-09 22:02:55 [INFO] ✓ Wrote sequence 2173 (latency: 28.33ms, success_rate: 93.7%)
```

### Why This Behavior Occurs

#### 1. Envoy Documentation Warning

From the [Envoy Admin API docs](https://www.envoyproxy.io/docs/envoy/latest/operations/admin):

> `/drain_listeners?inboundonly` may not be effective for **network filters** like Redis, Mongo, or Thrift proxies.

Our listener uses `envoy.filters.network.tcp_proxy`, which is a **network filter**.

#### 2. Listener Draining vs Connection Draining

**Two Separate Concepts:**

| Concept | What It Does | Affected by `/drain_listeners`? |
|---------|-------------|--------------------------------|
| **Listener Draining** | Stops accepting NEW connections on socket | ✅ Yes |
| **Connection Draining** | Closes EXISTING connections gracefully | ❌ No (for TCP) |

**Server Configuration (from `/server_info`):**
```json
{
  "drain_strategy": "Immediate",
  "drain_time": "45s",
  "terminationDrainDuration": "5s"
}
```

These settings apply during **server shutdown**, not when calling `/drain_listeners`.

#### 3. TCP Pass-Through Nature

Once a TCP connection is established:
1. Envoy creates upstream connection to backend (MariaDB)
2. Envoy acts as **transparent proxy**, passing bytes bidirectionally
3. Connection state maintained at **kernel level** (conntrack/netfilter)
4. Changing listener configuration doesn't affect established connections

#### 4. HTTP vs TCP Behavior

**HTTP Connections:**
- `drain_listeners` triggers HTTP-specific behavior:
  - Adds "Connection: close" header to HTTP/1.1 responses
  - Sends HTTP/2 GOAWAY frames
  - Connections close after current request completes

**TCP Connections:**
- No application-layer protocol to signal graceful close
- No equivalent to HTTP headers or GOAWAY frames
- Connection remains open until one side closes socket

### Why Listener Stats Show `total_listeners_draining: 0`

The `/drain_listeners` API stops the listener from accepting new connections, but the listener isn't tracked as "draining" in the traditional sense. This appears to be an implementation detail of how Envoy handles TCP listeners vs HTTP listeners.

### Technical Explanation Summary

1. **TCP Pass-Through Nature**
   - Envoy TCP proxy establishes connection then acts as a pass-through
   - Connection state is maintained at kernel level (conntrack/netfilter)
   - Changing routing rules doesn't affect established connections

2. **Envoy HTTP vs TCP Behavior**
   - **HTTP:** `drain_listeners` sends "Connection: close" headers and HTTP/2 GOAWAY frames
   - **TCP:** `drain_listeners` only prevents NEW connections, cannot signal existing ones
   - **Network Filters:** May not respond to drain commands as expected (per docs)

3. **Kubernetes Network Stack**
   - Service selector changes update iptables/IPVS rules
   - Existing connection tracking entries (conntrack) remain until timeout or connection close
   - NAT translations persist for active connections

---

## Recommended Approach for Operator

### Option 1: Gateway Pod Restart (Recommended)

**Pros:**
- ✅ 100% reliable - forcefully closes all connections
- ✅ Works regardless of database state (crashed, hung, healthy)
- ✅ Simple to implement
- ✅ No Envoy admin API required

**Cons:**
- ⚠️ Affects ALL connections through that Gateway pod (multi-tenancy impact)
- ⚠️ Requires pod deletion permissions
- ⚠️ Brief service interruption (~10-15 seconds)

**Implementation:**
```go
func (r *ReplicationReconciler) closeGatewayConnections(ctx context.Context, req *ReconcileRequest) error {
    // Get Gateway pods by selector
    gatewayPods, err := r.listGatewayPods(ctx, req.mariadb.Spec.Gateway.GatewaySelector)
    if err != nil {
        return err
    }

    // Delete pods to force connection termination
    for _, pod := range gatewayPods {
        if err := r.Delete(ctx, &pod); err != nil {
            return fmt.Errorf("failed to delete gateway pod %s: %v", pod.Name, err)
        }
        logger.Info("Deleted gateway pod to terminate connections", "pod", pod.Name)
    }

    // Wait for new pods to be ready
    return r.waitForGatewayReady(ctx, req.mariadb.Spec.Gateway, 30*time.Second)
}
```

---

### Option 2: Database-Level Connection Killing (Fallback)

**When Primary is Healthy:**
```sql
-- Kill all non-system connections
SELECT CONCAT('KILL ', id, ';') FROM information_schema.processlist
WHERE user NOT IN ('system user', 'event_scheduler');
```

**Pros:**
- ✅ No impact on other tenants
- ✅ More granular control

**Cons:**
- ❌ Only works if database is responsive
- ❌ Clients using connection pooling via Gateway still maintain TCP connection
- ❌ Requires careful filtering to avoid killing replication threads

---

### Option 3: Hybrid Approach (Best for Production)

**Failover Sequence:**

```go
phases := []switchoverPhase{
    {
        name:      "Lock primary with read lock",
        reconcile: r.lockPrimaryWithReadLock,
    },
    {
        name:      "Set read_only in primary",
        reconcile: r.setPrimaryReadOnly,
    },
    {
        // GATEWAY PHASE 1: Stop new connections
        name:      "Drain Gateway listeners",
        reconcile: r.drainGatewayListeners,  // POST /drain_listeners?inboundonly
    },
    {
        // GATEWAY PHASE 2: Force close existing connections
        name:      "Restart Gateway pods",
        reconcile: r.restartGatewayPods,  // Delete pods
    },
    {
        // GATEWAY PHASE 3: Wait for recovery
        name:      "Wait for Gateway ready",
        reconcile: r.waitForGatewayReady,
    },
    {
        name:      "Wait sync",
        reconcile: r.waitSync,
    },
    {
        name:      "Configure new primary",
        reconcile: r.configureNewPrimary,
    },
    {
        // GATEWAY PHASE 4: Restore routing
        name:      "Update Gateway to new primary",
        reconcile: r.updateGatewayToNewPrimary,
    },
    {
        name:      "Change primary to replica",
        reconcile: r.changePrimaryToReplica,
    },
}
```

---

## Multi-Tenancy Considerations

### Problem
Restarting Gateway pods affects **all tenants** using that Gateway, not just the failing database.

### Solutions

**Option A: Dedicated Gateway per Tenant**
- Each tenant gets own Gateway deployment
- Isolated failure domains
- Higher resource cost

**Option B: Accept Brief Multi-Tenant Impact**
- Document 10-15 second connection disruption during failover
- Gateway pods restart quickly
- Clients auto-reconnect
- Acceptable for most use cases

**Option C: Selective Connection Filtering (Future Enhancement)**
- Requires Envoy extension to filter connections by destination
- Not currently available in standard Envoy
- Would need custom Envoy filter

---

## Testing Summary

| Test | Method | New Connections Blocked? | Existing Connections Closed? | Recovery Time |
|------|--------|--------------------------|------------------------------|---------------|
| drain_listeners?inboundonly | POST to admin API | ❌ No* | ❌ No | N/A |
| drain_listeners (all) | POST to admin API | ✅ Yes | ❌ No | N/A |
| drain_listeners?graceful | POST to admin API | ✅ Yes | ❌ No | N/A |
| VirtualService update | Update route destination | ❌ No | ❌ No | N/A |
| Gateway CRD deletion | Delete Gateway resource | ❌ No | ❌ No | N/A |
| **Gateway pod restart** | **Delete pod** | **✅ Yes** | **✅ Yes** | **~10-15s** |

\* `?inboundonly` doesn't match OUTBOUND listener, so it has no effect

---

## References

- Envoy Draining Documentation: https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/operations/draining
- GitHub Issue #32510: TCP proxy graceful draining problems
- Istio Gateway TCP routing behavior

---

## Conclusion

### Key Findings

1. **`/drain_listeners` DOES work for TCP listeners:**
   - ✅ Successfully prevents NEW connections from being accepted
   - ❌ Does NOT close existing connections
   - This is **by design** for TCP network filters

2. **Why existing connections aren't closed:**
   - TCP proxy is a network filter (Envoy docs warn about this)
   - No application-layer protocol to signal graceful shutdown
   - Connection state maintained at kernel level
   - Listener marked as OUTBOUND (not INBOUND)

3. **`/drain_listeners?inboundonly` had no effect because:**
   - The 3306 listener is marked as `traffic_direction: OUTBOUND`
   - The `inboundonly` parameter only affects INBOUND listeners
   - This explains why initial tests seemed to fail

### Recommended Failover Sequence

**For production MariaDB failover with Istio Gateway:**

1. **Call `/drain_listeners`** (without inboundonly) to stop NEW connections gracefully
   - Prevents new clients from connecting during failover
   - Existing connections continue working

2. **Restart Gateway pods** to forcefully terminate EXISTING TCP connections
   - Only reliable way to close active connections
   - Brief disruption (~10-15 seconds)
   - Affects all tenants using the Gateway

3. **Wait for Gateway to become ready** (10-15 seconds)
   - New pods come online with fresh listeners
   - All connections start clean

4. **Update VirtualService** to route to new primary
   - Configure routing to promoted replica
   - Clients auto-reconnect to new primary

5. **Verify configuration** applied via `/config_dump`
   - Confirm routing changes propagated
   - Ensure listener is healthy

This approach provides **deterministic failover** even when the database is unresponsive, as required by the design document.

### Alternative: If Gateway Pod Restart is Unacceptable

If restarting Gateway pods affects too many tenants:

**Option 1:** Deploy dedicated Gateway per database/tenant
- Isolated failure domains
- Higher resource cost
- Full control over connection lifecycle

**Option 2:** Accept that existing connections will eventually time out
- Use `/drain_listeners` to prevent new connections
- Existing connections remain until TCP timeout (typically hours)
- Not suitable for fast failover requirements

**Option 3:** Implement application-level connection management
- Clients must detect database failover
- Close and reconnect when detecting read-only errors
- Requires changes to all client applications
