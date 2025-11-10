package replication

import (
	"context"
	"fmt"
	"time"

	"github.com/go-logr/logr"
	mariadbv1alpha1 "github.com/mariadb-operator/mariadb-operator/v25/api/v1alpha1"
	mariadbpod "github.com/mariadb-operator/mariadb-operator/v25/pkg/pod"
	"github.com/mariadb-operator/mariadb-operator/v25/pkg/wait"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// restartGatewayPods triggers a rolling restart of the Gateway workload (Deployment or StatefulSet).
// This ensures clients reconnect to the new primary after failover.
// Must be called AFTER database promotion to ensure clients reconnect to the new primary.
func (r *ReplicationReconciler) restartGatewayPods(ctx context.Context, req *ReconcileRequest, logger logr.Logger) error {
	if !req.mariadb.IsGatewayEnabled() {
		logger.V(1).Info("Gateway integration not enabled, skipping restart")
		return nil
	}

	gatewayConfig := req.mariadb.GetGatewayConfig()
	if gatewayConfig.Kind == "" || gatewayConfig.Name == "" {
		return fmt.Errorf("invalid gateway configuration: kind and name are required")
	}

	// Determine namespace
	namespace := gatewayConfig.Namespace
	if namespace == "" {
		namespace = req.mariadb.Namespace
	}

	logger.Info("Restarting Gateway workload to force connection termination",
		"kind", gatewayConfig.Kind, "namespace", namespace, "name", gatewayConfig.Name)
	r.recorder.Event(req.mariadb, corev1.EventTypeNormal, mariadbv1alpha1.ReasonReplicationGatewayRestart,
		"Restarting Gateway pods")

	// Trigger rollout restart based on kind
	switch gatewayConfig.Kind {
	case "deployment":
		return r.restartDeployment(ctx, namespace, gatewayConfig.Name, logger)
	case "statefulset":
		return r.restartStatefulSet(ctx, namespace, gatewayConfig.Name, logger)
	default:
		return fmt.Errorf("unsupported gateway kind: %s (supported: deployment, statefulset)", gatewayConfig.Kind)
	}
}

// restartDeployment triggers a rolling restart of a Deployment by updating its template annotation.
func (r *ReplicationReconciler) restartDeployment(ctx context.Context, namespace, name string, logger logr.Logger) error {
	deployment := &appsv1.Deployment{}
	if err := r.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, deployment); err != nil {
		if apierrors.IsNotFound(err) {
			return fmt.Errorf("gateway deployment %s/%s not found", namespace, name)
		}
		return fmt.Errorf("error getting deployment %s/%s: %v", namespace, name, err)
	}

	// Trigger rollout restart by updating the restart annotation
	// This is equivalent to: kubectl rollout restart deployment/xxx
	if deployment.Spec.Template.Annotations == nil {
		deployment.Spec.Template.Annotations = make(map[string]string)
	}
	deployment.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = time.Now().Format(time.RFC3339)

	if err := r.Update(ctx, deployment); err != nil {
		return fmt.Errorf("error restarting deployment %s/%s: %v", namespace, name, err)
	}

	logger.Info("Triggered rolling restart of Gateway deployment", "deployment", name)
	return nil
}

// restartStatefulSet triggers a rolling restart of a StatefulSet by updating its template annotation.
func (r *ReplicationReconciler) restartStatefulSet(ctx context.Context, namespace, name string, logger logr.Logger) error {
	statefulset := &appsv1.StatefulSet{}
	if err := r.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, statefulset); err != nil {
		if apierrors.IsNotFound(err) {
			return fmt.Errorf("gateway statefulset %s/%s not found", namespace, name)
		}
		return fmt.Errorf("error getting statefulset %s/%s: %v", namespace, name, err)
	}

	// Trigger rollout restart by updating the restart annotation
	if statefulset.Spec.Template.Annotations == nil {
		statefulset.Spec.Template.Annotations = make(map[string]string)
	}
	statefulset.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = time.Now().Format(time.RFC3339)

	if err := r.Update(ctx, statefulset); err != nil {
		return fmt.Errorf("error restarting statefulset %s/%s: %v", namespace, name, err)
	}

	logger.Info("Triggered rolling restart of Gateway statefulset", "statefulset", name)
	return nil
}

// waitForGatewayReady waits for Gateway pods to be recreated and become ready after restart.
func (r *ReplicationReconciler) waitForGatewayReady(ctx context.Context, req *ReconcileRequest, logger logr.Logger) error {
	if !req.mariadb.IsGatewayEnabled() {
		logger.V(1).Info("Gateway integration not enabled, skipping readiness check")
		return nil
	}

	gatewayConfig := req.mariadb.GetGatewayConfig()
	if gatewayConfig.Kind == "" || gatewayConfig.Name == "" {
		return fmt.Errorf("invalid gateway configuration: kind and name are required")
	}

	// Determine namespace
	namespace := gatewayConfig.Namespace
	if namespace == "" {
		namespace = req.mariadb.Namespace
	}

	logger.Info("Waiting for Gateway pods to be ready")
	r.recorder.Event(req.mariadb, corev1.EventTypeNormal, mariadbv1alpha1.ReasonReplicationGatewayWait,
		"Waiting for Gateway pods to be ready")

	// Wait up to 2 minutes for Gateway pods to be ready
	waitCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()

	// Get label selector based on kind
	var selector labels.Selector
	switch gatewayConfig.Kind {
	case "deployment":
		deployment := &appsv1.Deployment{}
		if err := r.Get(ctx, types.NamespacedName{Namespace: namespace, Name: gatewayConfig.Name}, deployment); err != nil {
			return fmt.Errorf("error getting deployment: %v", err)
		}
		selector = labels.SelectorFromSet(deployment.Spec.Selector.MatchLabels)
	case "statefulset":
		statefulset := &appsv1.StatefulSet{}
		if err := r.Get(ctx, types.NamespacedName{Namespace: namespace, Name: gatewayConfig.Name}, statefulset); err != nil {
			return fmt.Errorf("error getting statefulset: %v", err)
		}
		selector = labels.SelectorFromSet(statefulset.Spec.Selector.MatchLabels)
	default:
		return fmt.Errorf("unsupported gateway kind: %s", gatewayConfig.Kind)
	}

	if err := wait.PollUntilSuccessOrContextCancel(waitCtx, logger, func(ctx context.Context) error {
		// Find Gateway pods using the selector
		var podList corev1.PodList
		listOpts := &client.ListOptions{
			Namespace:     namespace,
			LabelSelector: selector,
		}

		if err := r.List(ctx, &podList, listOpts); err != nil {
			return fmt.Errorf("error listing Gateway pods: %v", err)
		}

		if len(podList.Items) == 0 {
			return fmt.Errorf("no Gateway pods found")
		}

		// Check all Gateway pods are ready
		for _, pod := range podList.Items {
			if !mariadbpod.PodReady(&pod) {
				return fmt.Errorf("Gateway pod %s not ready", pod.Name)
			}
		}

		return nil
	}); err != nil {
		logger.Error(err, "Error waiting for Gateway pods to be ready")
		r.recorder.Eventf(req.mariadb, corev1.EventTypeWarning, mariadbv1alpha1.ReasonReplicationGatewayWaitErr,
			"Error waiting for Gateway pods to be ready: %v", err)
		return err
	}

	logger.Info("Gateway pods are ready")
	return nil
}
