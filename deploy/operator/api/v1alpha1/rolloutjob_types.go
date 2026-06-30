package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
)

// RolloutJobSpec is the desired state: how to run one RL training rollout loop.
type RolloutJobSpec struct {
	// Image is the container image that runs the trainer (defaults to the platform image).
	// +optional
	Image string `json:"image,omitempty"`

	// Budget is the number of analyze calls per episode.
	// +kubebuilder:default=30
	// +optional
	Budget int32 `json:"budget,omitempty"`

	// Iterations is the number of REINFORCE update steps.
	// +kubebuilder:default=40
	// +optional
	Iterations int32 `json:"iterations,omitempty"`

	// Batch is the number of episodes collected per iteration.
	// +kubebuilder:default=12
	// +optional
	Batch int32 `json:"batch,omitempty"`

	// LearningRate is passed straight to --lr (a string to keep the CRD float-free).
	// +kubebuilder:default="0.5"
	// +optional
	LearningRate string `json:"learningRate,omitempty"`

	// Synthetic runs the self-contained environment (no data mounts). Defaults to true.
	// +optional
	Synthetic *bool `json:"synthetic,omitempty"`

	// SyntheticN is the synthetic candidate pool size (when Synthetic is true).
	// +kubebuilder:default=120
	// +optional
	SyntheticN int32 `json:"syntheticN,omitempty"`

	// SyntheticKeepers is the synthetic keeper count (when Synthetic is true).
	// +kubebuilder:default=30
	// +optional
	SyntheticKeepers int32 `json:"syntheticKeepers,omitempty"`

	// ExtraArgs are appended verbatim to the trainer command (escape hatch).
	// +optional
	ExtraArgs []string `json:"extraArgs,omitempty"`

	// BackoffLimit for the underlying Job.
	// +kubebuilder:default=1
	// +optional
	BackoffLimit *int32 `json:"backoffLimit,omitempty"`
}

// RolloutJobPhase mirrors the lifecycle of the underlying Job.
type RolloutJobPhase string

const (
	PhasePending   RolloutJobPhase = "Pending"
	PhaseRunning   RolloutJobPhase = "Running"
	PhaseSucceeded RolloutJobPhase = "Succeeded"
	PhaseFailed    RolloutJobPhase = "Failed"
)

// RolloutJobStatus is the observed state, reflected from the owned Job.
type RolloutJobStatus struct {
	// +optional
	Phase RolloutJobPhase `json:"phase,omitempty"`
	// +optional
	JobName string `json:"jobName,omitempty"`
	// +optional
	StartTime *metav1.Time `json:"startTime,omitempty"`
	// +optional
	CompletionTime *metav1.Time `json:"completionTime,omitempty"`
	// +optional
	Message string `json:"message,omitempty"`
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=rlj
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Job",type=string,JSONPath=`.status.jobName`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// RolloutJob is one declarative RL training run.
type RolloutJob struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   RolloutJobSpec   `json:"spec,omitempty"`
	Status RolloutJobStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// RolloutJobList contains a list of RolloutJob.
type RolloutJobList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []RolloutJob `json:"items"`
}

// ---------------------------------------------------------------------------
// DeepCopy implementations (hand-written so the operator builds without
// controller-gen; equivalent to zz_generated.deepcopy.go).
// ---------------------------------------------------------------------------

func (in *RolloutJobSpec) DeepCopyInto(out *RolloutJobSpec) {
	*out = *in
	if in.Synthetic != nil {
		out.Synthetic = new(bool)
		*out.Synthetic = *in.Synthetic
	}
	if in.ExtraArgs != nil {
		out.ExtraArgs = make([]string, len(in.ExtraArgs))
		copy(out.ExtraArgs, in.ExtraArgs)
	}
	if in.BackoffLimit != nil {
		out.BackoffLimit = new(int32)
		*out.BackoffLimit = *in.BackoffLimit
	}
}

func (in *RolloutJobSpec) DeepCopy() *RolloutJobSpec {
	if in == nil {
		return nil
	}
	out := new(RolloutJobSpec)
	in.DeepCopyInto(out)
	return out
}

func (in *RolloutJobStatus) DeepCopyInto(out *RolloutJobStatus) {
	*out = *in
	if in.StartTime != nil {
		out.StartTime = in.StartTime.DeepCopy()
	}
	if in.CompletionTime != nil {
		out.CompletionTime = in.CompletionTime.DeepCopy()
	}
}

func (in *RolloutJobStatus) DeepCopy() *RolloutJobStatus {
	if in == nil {
		return nil
	}
	out := new(RolloutJobStatus)
	in.DeepCopyInto(out)
	return out
}

func (in *RolloutJob) DeepCopyInto(out *RolloutJob) {
	*out = *in
	out.TypeMeta = in.TypeMeta
	in.ObjectMeta.DeepCopyInto(&out.ObjectMeta)
	in.Spec.DeepCopyInto(&out.Spec)
	in.Status.DeepCopyInto(&out.Status)
}

func (in *RolloutJob) DeepCopy() *RolloutJob {
	if in == nil {
		return nil
	}
	out := new(RolloutJob)
	in.DeepCopyInto(out)
	return out
}

func (in *RolloutJob) DeepCopyObject() runtime.Object {
	if c := in.DeepCopy(); c != nil {
		return c
	}
	return nil
}

func (in *RolloutJobList) DeepCopyInto(out *RolloutJobList) {
	*out = *in
	out.TypeMeta = in.TypeMeta
	in.ListMeta.DeepCopyInto(&out.ListMeta)
	if in.Items != nil {
		out.Items = make([]RolloutJob, len(in.Items))
		for i := range in.Items {
			in.Items[i].DeepCopyInto(&out.Items[i])
		}
	}
}

func (in *RolloutJobList) DeepCopy() *RolloutJobList {
	if in == nil {
		return nil
	}
	out := new(RolloutJobList)
	in.DeepCopyInto(out)
	return out
}

func (in *RolloutJobList) DeepCopyObject() runtime.Object {
	if c := in.DeepCopy(); c != nil {
		return c
	}
	return nil
}
