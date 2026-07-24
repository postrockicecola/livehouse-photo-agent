"""Model and image-operator code on the **default** (non–inference-layer) path.

- **VLM:** :mod:`engine.models.vlm_model` (:class:`~engine.models.vlm_model.LivehouseVLM`)
- **Stages / ops:** :mod:`engine.operators`

When ``configs/livehouse.yaml`` sets ``model.use_inference_layer: true``, the pipeline
preferentially uses ``inference/`` (:class:`~inference.client.InferenceClient` et al.);
this package remains relevant for Stage 1–2 style operators and backwards-compatible VLM naming.
"""
