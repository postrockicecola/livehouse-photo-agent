// Package v1alpha1 contains the RolloutJob custom resource — a thin K8s-native
// wrapper that turns one declarative spec into a Kubernetes Job which runs the RL
// training loop (scripts/rl/train_curation_policy.py). It is the "control plane"
// half of the demo: a reconcile loop driving desired (the CR) toward actual (a Job).
//
// +kubebuilder:object:generate=true
// +groupName=rl.livehouse.ai
package v1alpha1

import (
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/scheme"
)

// GroupVersion is the group/version used to register these objects.
var GroupVersion = schema.GroupVersion{Group: "rl.livehouse.ai", Version: "v1alpha1"}

// SchemeBuilder registers the Go types with a runtime.Scheme.
var SchemeBuilder = &scheme.Builder{GroupVersion: GroupVersion}

// AddToScheme adds the types in this group-version to the given scheme.
var AddToScheme = SchemeBuilder.AddToScheme

func init() {
	SchemeBuilder.Register(&RolloutJob{}, &RolloutJobList{})
}
