# Known Issues

## Client Connections Do Not Auto-Recover After Graceful Failover

**Issue**: [#1509](https://github.com/mariadb-operator/mariadb-operator/issues/1509)

**Affected**: Replication clusters with graceful primary switchover

**Symptom**: Client connections using connection pooling continue routing to demoted replica after failover, causing indefinite write failures with error 1290 (read-only).

**Cause**: Kubernetes conntrack maintains existing TCP connections to original pod after service selector changes.

**Workaround**: Applications must detect error 1290 and implement explicit reconnection logic.

**Note**: Forced failover (pod crash) is unaffected - connections break and clients reconnect normally.

## ProxySQL DNS Caching After Pod Restart

**Affected**: ProxySQL integration (Helm chart) when MariaDB pods are deleted and recreated

**Symptom**: ProxySQL continues trying to connect to old IP addresses after MariaDB pods restart with new IPs. Affected pods remain SHUNNED indefinitely despite being healthy.

**Cause**: ProxySQL caches DNS resolutions and does not automatically refresh when backend pods get new IP addresses (e.g., after `kubectl delete pod`). ProxySQL continues using cached IP even though DNS (headless service) correctly resolves to new IP.

**Workaround**: Manually trigger DNS refresh by executing on any ProxySQL pod:
```bash
kubectl exec -n <namespace> <proxysql-pod> -- \
  mysql -h127.0.0.1 -P6032 -u<admin-user> -p<admin-password> \
  -e "LOAD MYSQL SERVERS TO RUNTIME;"
```

**Timeline**:
- Pod deleted at T+0s
- New pod starts with new IP
- ProxySQL ping checks fail with old IP (every 10s)
- After manual reload: DNS refreshed, pod becomes ONLINE within ~5s

**Impact**: Reduced read capacity as recovered pods stay SHUNNED. No impact on writes (primary failover works correctly).

**Future Enhancement**: Consider implementing automatic DNS refresh mechanism or shorter DNS TTL handling in ProxySQL configuration.
