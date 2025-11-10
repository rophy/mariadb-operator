package v1alpha1

import (
	"strings"
)

const (
	// GatewayAnnotation specifies the Gateway workload to restart during failover.
	// When set, Gateway integration is enabled and the operator will trigger a rolling restart
	// to force client reconnection.
	// Format: "kind/name" or "kind/namespace/name"
	// Supported kinds: deployment, statefulset
	// Examples:
	//   - "deployment/mariadb-gateway"
	//   - "deployment/istio-system/istio-ingressgateway"
	//   - "statefulset/gateway-sts"
	GatewayAnnotation = "mariadb.mmontes.io/gateway"
)

// GatewayConfig holds parsed Gateway configuration from MariaDB annotations.
type GatewayConfig struct {
	Enabled   bool
	Kind      string // "deployment" or "statefulset"
	Name      string
	Namespace string // Empty means use MariaDB's namespace
}

// IsGatewayEnabled checks if Gateway integration is enabled via annotations.
// Gateway is enabled if the gateway annotation is present and non-empty.
func (m *MariaDB) IsGatewayEnabled() bool {
	if m.Annotations == nil {
		return false
	}
	gateway := m.Annotations[GatewayAnnotation]
	return gateway != ""
}

// GetGatewayConfig parses Gateway configuration from MariaDB annotations.
// Format: "kind/name" or "kind/namespace/name"
// Returns GatewayConfig with parsed kind, namespace, and name.
func (m *MariaDB) GetGatewayConfig() GatewayConfig {
	config := GatewayConfig{
		Enabled: false,
	}

	if m.Annotations == nil {
		return config
	}

	gateway := m.Annotations[GatewayAnnotation]
	if gateway == "" {
		return config
	}

	config.Enabled = true

	// Parse the annotation value
	// Format: "kind/name" or "kind/namespace/name"
	parts := strings.Split(gateway, "/")

	if len(parts) == 2 {
		// Format: kind/name
		config.Kind = strings.ToLower(strings.TrimSpace(parts[0]))
		config.Name = strings.TrimSpace(parts[1])
		config.Namespace = "" // Will use MariaDB's namespace
	} else if len(parts) == 3 {
		// Format: kind/namespace/name
		config.Kind = strings.ToLower(strings.TrimSpace(parts[0]))
		config.Namespace = strings.TrimSpace(parts[1])
		config.Name = strings.TrimSpace(parts[2])
	} else {
		// Invalid format, disable
		config.Enabled = false
	}

	return config
}
