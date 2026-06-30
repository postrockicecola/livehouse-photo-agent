package controller

import (
	"context"
	"fmt"
	"strconv"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	rlv1alpha1 "livehouse.ai/rollout-operator/api/v1alpha1"
)

const defaultImage = "livehouse-python:local"

// RolloutJobReconciler reconciles a RolloutJob: it owns exactly one batch/v1 Job
// that runs the RL trainer and mirrors that Job's lifecycle into the CR status.
type RolloutJobReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=rl.livehouse.ai,resources=rolloutjobs,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=rl.livehouse.ai,resources=rolloutjobs/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=rl.livehouse.ai,resources=rolloutjobs/finalizers,verbs=update
// +kubebuilder:rbac:groups=batch,resources=jobs,verbs=get;list;watch;create;update;patch;delete

func (r *RolloutJobReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	lg := log.FromContext(ctx)

	var ro rlv1alpha1.RolloutJob
	if err := r.Get(ctx, req.NamespacedName, &ro); err != nil {
		// Deleted: owned Job is garbage-collected via owner reference. Nothing to do.
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	jobName := ro.Name + "-rl"

	// Desired vs actual: ensure the owned Job exists (idempotent create).
	var job batchv1.Job
	err := r.Get(ctx, types.NamespacedName{Namespace: ro.Namespace, Name: jobName}, &job)
	if apierrors.IsNotFound(err) {
		desired := r.buildJob(&ro, jobName)
		if err := ctrl.SetControllerReference(&ro, desired, r.Scheme); err != nil {
			return ctrl.Result{}, err
		}
		if err := r.Create(ctx, desired); err != nil {
			if apierrors.IsAlreadyExists(err) {
				return ctrl.Result{Requeue: true}, nil
			}
			return ctrl.Result{}, fmt.Errorf("create job: %w", err)
		}
		lg.Info("created training Job", "job", jobName)
		_ = r.patchStatus(ctx, &ro, rlv1alpha1.PhasePending, jobName, "training Job created", nil, nil)
		return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
	} else if err != nil {
		return ctrl.Result{}, fmt.Errorf("get job: %w", err)
	}

	// Job exists: reflect its lifecycle into the CR status.
	phase, msg := phaseFromJob(&job)
	if err := r.patchStatus(ctx, &ro, phase, jobName, msg, job.Status.StartTime, job.Status.CompletionTime); err != nil {
		return ctrl.Result{}, err
	}

	if phase == rlv1alpha1.PhasePending || phase == rlv1alpha1.PhaseRunning {
		// Owns() re-triggers on Job changes; the requeue is a slow safety net.
		return ctrl.Result{RequeueAfter: 15 * time.Second}, nil
	}
	return ctrl.Result{}, nil
}

func (r *RolloutJobReconciler) patchStatus(
	ctx context.Context,
	ro *rlv1alpha1.RolloutJob,
	phase rlv1alpha1.RolloutJobPhase,
	jobName, msg string,
	start, completion *metav1.Time,
) error {
	base := ro.DeepCopy()
	ro.Status.Phase = phase
	ro.Status.JobName = jobName
	ro.Status.Message = msg
	ro.Status.StartTime = start
	ro.Status.CompletionTime = completion
	ro.Status.ObservedGeneration = ro.Generation
	if equalStatus(&base.Status, &ro.Status) {
		return nil
	}
	return r.Status().Patch(ctx, ro, client.MergeFrom(base))
}

func equalStatus(a, b *rlv1alpha1.RolloutJobStatus) bool {
	return a.Phase == b.Phase &&
		a.JobName == b.JobName &&
		a.Message == b.Message &&
		a.ObservedGeneration == b.ObservedGeneration &&
		timeEqual(a.StartTime, b.StartTime) &&
		timeEqual(a.CompletionTime, b.CompletionTime)
}

func timeEqual(a, b *metav1.Time) bool {
	if a == nil || b == nil {
		return a == b
	}
	return a.Equal(b)
}

// phaseFromJob maps a Job's conditions/counters to a RolloutJob phase.
func phaseFromJob(job *batchv1.Job) (rlv1alpha1.RolloutJobPhase, string) {
	for _, c := range job.Status.Conditions {
		if c.Type == batchv1.JobComplete && c.Status == corev1.ConditionTrue {
			return rlv1alpha1.PhaseSucceeded, "training Job completed"
		}
		if c.Type == batchv1.JobFailed && c.Status == corev1.ConditionTrue {
			return rlv1alpha1.PhaseFailed, fmt.Sprintf("training Job failed: %s", c.Reason)
		}
	}
	if job.Status.Active > 0 {
		return rlv1alpha1.PhaseRunning, "training in progress"
	}
	return rlv1alpha1.PhasePending, "waiting for pod to start"
}

func (r *RolloutJobReconciler) buildJob(ro *rlv1alpha1.RolloutJob, jobName string) *batchv1.Job {
	image := ro.Spec.Image
	if image == "" {
		image = defaultImage
	}
	backoff := int32(1)
	if ro.Spec.BackoffLimit != nil {
		backoff = *ro.Spec.BackoffLimit
	}
	ttl := int32(600)

	labels := map[string]string{
		"app":                        "rollout-trainer",
		"livehouse.role":             "rl-train",
		"rl.livehouse.ai/rolloutjob": ro.Name,
	}

	return &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      jobName,
			Namespace: ro.Namespace,
			Labels:    labels,
		},
		Spec: batchv1.JobSpec{
			BackoffLimit:            &backoff,
			TTLSecondsAfterFinished: &ttl,
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					Containers: []corev1.Container{
						{
							Name:            "trainer",
							Image:           image,
							ImagePullPolicy: corev1.PullIfNotPresent,
							Args:            trainerArgs(ro),
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceCPU:    mustQuantity("100m"),
									corev1.ResourceMemory: mustQuantity("256Mi"),
								},
								Limits: corev1.ResourceList{
									corev1.ResourceCPU:    mustQuantity("1"),
									corev1.ResourceMemory: mustQuantity("512Mi"),
								},
							},
						},
					},
				},
			},
		},
	}
}

// trainerArgs renders the RolloutJob spec into the trainer command line. The
// default path is --synthetic so the Job needs nothing mounted (reproducible demo).
func trainerArgs(ro *rlv1alpha1.RolloutJob) []string {
	args := []string{"python", "scripts/rl/train_curation_policy.py"}

	synthetic := true
	if ro.Spec.Synthetic != nil {
		synthetic = *ro.Spec.Synthetic
	}
	if synthetic {
		args = append(args, "--synthetic")
		args = append(args, "--synthetic-n", itoa(ro.Spec.SyntheticN, 120))
		args = append(args, "--synthetic-keepers", itoa(ro.Spec.SyntheticKeepers, 30))
	}
	args = append(args, "--budget", itoa(ro.Spec.Budget, 30))
	args = append(args, "--iterations", itoa(ro.Spec.Iterations, 40))
	args = append(args, "--batch", itoa(ro.Spec.Batch, 12))
	lr := ro.Spec.LearningRate
	if lr == "" {
		lr = "0.5"
	}
	args = append(args, "--lr", lr)
	args = append(args, "--out", "/tmp/rl_"+ro.Name+".json")
	args = append(args, ro.Spec.ExtraArgs...)
	return args
}

func itoa(v int32, def int32) string {
	if v <= 0 {
		v = def
	}
	return strconv.Itoa(int(v))
}

func mustQuantity(s string) resource.Quantity {
	return resource.MustParse(s)
}

// SetupWithManager wires the controller to watch RolloutJobs and the Jobs it owns.
func (r *RolloutJobReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&rlv1alpha1.RolloutJob{}).
		Owns(&batchv1.Job{}).
		Complete(r)
}
