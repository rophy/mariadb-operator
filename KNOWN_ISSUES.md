# Known Issues

## Client Connections Do Not Auto-Recover After Graceful Failover

**Issue**: [#1509](https://github.com/mariadb-operator/mariadb-operator/issues/1509)

**Affected**: Replication clusters with graceful primary switchover

**Symptom**: Client connections using connection pooling continue routing to demoted replica after failover, causing indefinite write failures with error 1290 (read-only).

**Cause**: Kubernetes conntrack maintains existing TCP connections to original pod after service selector changes.

**Workaround**: Applications must detect error 1290 and implement explicit reconnection logic.

**Note**: Forced failover (pod crash) is unaffected - connections break and clients reconnect normally.

## ProxySQL DNS Caching After Pod Restart (FIXED)

**Issue**: [#3](https://github.com/rophy/mariadb-operator/issues/3)

**Status**: ✅ Fixed in ProxySQL Helm chart by disabling DNS caching.

## ProxySQL Fast Failover Causes Data Loss During Operator-Controlled Switchover

**Issue**: [#2](https://github.com/rophy/mariadb-operator/issues/2)

**Status**: ProxySQL's fast failover (~1-2s) races against operator's controlled switchover (~10-12s), causing data loss. Use Gateway (Istio) instead for production.
