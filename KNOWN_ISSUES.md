# Known Issues

## Client Connections Do Not Auto-Recover After Graceful Failover

**Issue**: [#1509](https://github.com/mariadb-operator/mariadb-operator/issues/1509)

**Affected**: Replication clusters with graceful primary switchover

**Symptom**: Client connections using connection pooling continue routing to demoted replica after failover, causing indefinite write failures with error 1290 (read-only).

**Cause**: Kubernetes conntrack maintains existing TCP connections to original pod after service selector changes.

**Workaround**: Applications must detect error 1290 and implement explicit reconnection logic.

**Note**: Forced failover (pod crash) is unaffected - connections break and clients reconnect normally.

## ProxySQL DNS Caching After Pod Restart (FIXED)

**Status**: ✅ Fixed in ProxySQL Helm chart

**Affected**: ProxySQL integration (Helm chart) versions before the fix when MariaDB pods are deleted and recreated

**Symptom**: ProxySQL continued trying to connect to old IP addresses after MariaDB pods restarted with new IPs. Affected pods remained SHUNNED indefinitely despite being healthy.

**Cause**: ProxySQL cached DNS resolutions and did not automatically refresh when backend pods got new IP addresses (e.g., after `kubectl delete pod`). ProxySQL continued using cached IP even though DNS (headless service) correctly resolved to new IP.

**Fix**: DNS caching is now disabled in ProxySQL configuration by setting:
- `monitor_local_dns_cache_ttl = 0`
- `monitor_local_dns_cache_refresh_interval = 0`

This ensures ProxySQL performs fresh DNS lookups on every connection attempt, automatically detecting when pods return with new IPs.

**Verification**: Failover testing confirms recovered pods now come back ONLINE automatically within seconds without manual intervention.

**Impact of Fix**: Negligible performance impact - DNS queries to cluster-local headless services are very fast (~0.2-0.4ms).

## ProxySQL Fast Failover Causes Data Loss During Operator-Controlled Switchover

**Issue**: [#2](https://github.com/rophy/mariadb-operator/issues/2)

**Status**: ProxySQL's fast failover (~1-2s) races against operator's controlled switchover (~10-12s), causing data loss. Use Gateway (Istio) instead for production.
